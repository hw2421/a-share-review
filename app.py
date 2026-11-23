# -*- coding: utf-8 -*-
"""A股情绪复盘站点（单端口应用）：静态站点 + 手动刷新接口。

为什么需要它：站点是纯静态托管时，浏览器点「刷新」只能重新下载静态文件，
无法让服务端去抓同花顺当日涨停池；公共 CORS 代理又全部不可用。
把站点改为单端口服务后，页面可以 POST /api/refresh 真正触发后台抓取与重建。

接口：
  GET  /api/status   当前数据版本、数据截止日、刷新任务状态
  POST /api/refresh  触发一次「抓涨停池 → 重建全站数据」，异步执行
"""
import datetime
import json
import os
import subprocess
import sys
import threading
import time

from flask import Flask, jsonify, request, send_from_directory

BASE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, BASE)
app = Flask(__name__, static_folder=None)

_job = {"running": False, "started": 0.0}
_lock = threading.Lock()

# 内置定时器状态（.sched_status.json 落盘，便于排查「定时到底跑没跑」）
SCHED_F = os.path.join(BASE, ".sched_status.json")
_sched = {"lastSlot": ""}

# 不对外暴露的文件（源码 / 缓存 / 中间数据）
HIDDEN_EXT = (".py", ".pyc", ".log", ".lock", ".bak")
HIDDEN_NAMES = {"data.json", ".update_status.json", ".refresh_status.json",
                ".site_meta.json", ".build.lock"}


def data_ver():
    try:
        s = open(os.path.join(BASE, "version.js"), encoding="utf-8").read()
        return s.split('"')[1] if '"' in s else ""
    except Exception:
        return ""


def site_meta():
    try:
        return json.load(open(os.path.join(BASE, ".site_meta.json"),
                              encoding="utf-8"))
    except Exception:
        return {}


def read_status():
    try:
        return json.load(open(os.path.join(BASE, ".refresh_status.json"), encoding="utf-8"))
    except Exception:
        return {}


def _run_job():
    try:
        subprocess.run([sys.executable, os.path.join(BASE, "refresh_job.py")],
                       timeout=1200)
    except Exception as e:
        print("refresh_job error: %s" % e, flush=True)
    finally:
        with _lock:
            _job["running"] = False


# ---------------------------------------------------------------- 内置定时抓取
# 为什么放在应用里：自动化定时任务跑在独立沙箱，访问不到本项目目录（实测：
# 本地日志/探针文件全盘都不存在）。站点本身是本沙箱里的单端口服务，由它自己
# 定时抓取才能落到同一份磁盘上。沙箱休眠会停线程，所以进程启动时先补抓一次。
def sched_status():
    try:
        return json.load(open(SCHED_F, encoding="utf-8"))
    except Exception:
        return {}


def _write_sched(**kw):
    try:
        obj = sched_status()
        obj.update(kw)
        obj["at"] = time.strftime("%Y-%m-%d %H:%M:%S")
        tmp = SCHED_F + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(obj, f, ensure_ascii=False, indent=1)
        os.replace(tmp, SCHED_F)
    except Exception:
        pass


def _run_kline():
    """盘中刷新近期活跃股日K（脚本自带 09:25-15:10 窗口判断）。
    不调 --publish：站点由本进程直接服务同一目录，文件写完即生效。"""
    with _lock:
        if _job["running"]:
            _write_sched(kline="busy")
            return
        _job["running"] = True
        _job["started"] = time.time()
    try:
        r = subprocess.run(
            [sys.executable, os.path.join(BASE, "refresh_klines.py"),
             "--recent", "12"],
            capture_output=True, text=True, timeout=900, cwd=BASE)
        lines = [x for x in (r.stdout or "").strip().splitlines() if x.strip()]
        _write_sched(kline_ok=(r.returncode == 0),
                     kline_tail=lines[-1][:160] if lines else "")
        print("[sched/kline] " + (lines[-1] if lines else "no output"), flush=True)
    except Exception as e:
        _write_sched(kline_ok=False, kline_tail=str(e)[:160])
        print("[sched/kline] error: %s" % e, flush=True)
    finally:
        with _lock:
            _job["running"] = False


def _run_poll(force=False):
    """调 hunan_poll.py --once（它自带幂等与 16:30-23:59 时段判断）。"""
    with _lock:
        if _job["running"]:
            _write_sched(skipped="busy", ok=None)
            return
        _job["running"] = True
        _job["started"] = time.time()
    try:
        cmd = [sys.executable, os.path.join(BASE, "hunan_poll.py"), "--once"]
        if force:
            cmd.append("--force")
        r = subprocess.run(cmd, capture_output=True, text=True,
                           timeout=1700, cwd=BASE)
        lines = [x for x in (r.stdout or "").strip().splitlines() if x.strip()]
        _write_sched(ok=(r.returncode == 0),
                     tail=lines[-1][:200] if lines else "",
                     forced=bool(force))
        print("[sched] " + (lines[-1] if lines else "no output"), flush=True)
    except Exception as e:
        _write_sched(ok=False, tail=str(e)[:200])
        print("[sched] error: %s" % e, flush=True)
    finally:
        with _lock:
            _job["running"] = False


def _sched_loop():
    time.sleep(15)          # 等 web 服务先起来
    boot = False
    while True:
        try:
            now = datetime.datetime.now()
            hm = now.hour * 60 + now.minute
            in_win = now.weekday() < 5 and (16 * 60 + 30) <= hm <= (23 * 60 + 59)
            # 触发点：进程启动后第一次（补抓）+ 每小时的 00/30 分
            due = in_win and (not boot or now.minute in (0, 30))
            if due:
                slot = now.strftime("%Y%m%d%H%M")
                if _sched.get("lastSlot") != slot:
                    _sched["lastSlot"] = slot
                    threading.Thread(target=_run_poll, daemon=True).start()
            # 盘中日K：09:30-15:00 每 30 分钟
            in_kline = now.weekday() < 5 and (9 * 60 + 25) <= hm <= (15 * 60 + 10)
            if in_kline and now.minute in (0, 30):
                ks = now.strftime("%Y%m%d%H%M")
                if _sched.get("lastKline") != ks:
                    _sched["lastKline"] = ks
                    threading.Thread(target=_run_kline, daemon=True).start()
        except Exception as e:
            print("[sched] loop error: %s" % e, flush=True)
        boot = True
        time.sleep(30)


_sched_started = False


def start_scheduler():
    global _sched_started
    if _sched_started or os.environ.get("NO_SCHED") == "1":
        return
    _sched_started = True
    threading.Thread(target=_sched_loop, daemon=True).start()
    print("[sched] 内置定时抓取已启动（工作日 16:30-23:59，每 30 分钟）", flush=True)


@app.after_request
def no_cache(resp):
    # data.js 会随构建变化，禁止中间层缓存，避免「刷新了还是旧数据」
    if request.path.endswith((".js", ".html", "/")):
        resp.headers["Cache-Control"] = "no-store, must-revalidate"
    return resp


@app.route("/")
def index():
    return send_from_directory(BASE, "index.html")


@app.route("/<path:fn>")
def static_file(fn):
    if fn.startswith("api/"):
        return jsonify({"error": "not found"}), 404
    name = os.path.basename(fn)
    if name in HIDDEN_NAMES or name.lower().endswith(HIDDEN_EXT):
        return jsonify({"error": "forbidden"}), 403
    full = os.path.join(BASE, fn)
    if not os.path.isfile(full):
        return jsonify({"error": "not found"}), 404
    return send_from_directory(BASE, fn)


@app.route("/api/status")
def api_status():
    st = read_status()
    meta = site_meta()
    running = _job["running"] or st.get("state") == "running"
    return jsonify({
        "server": "v31",
        "ver": meta.get("ver") or data_ver(),
        "latest": meta.get("latest") or "",
        "updated": meta.get("updated") or "",
        "source": meta.get("source") or st.get("source") or "",
        "phase": meta.get("phase") or st.get("phase") or "",
        "running": bool(running),
        "job": st,
        "sched": sched_status(),
    })


@app.route("/api/refresh", methods=["POST", "GET"])
def api_refresh():
    with _lock:
        if _job["running"]:
            return jsonify({"started": False, "running": True,
                            "ver": data_ver(), "job": read_status()})
        _job["running"] = True
        _job["started"] = time.time()
    threading.Thread(target=_run_job, daemon=True).start()
    return jsonify({"started": True, "running": True, "ver": data_ver()})


if __name__ == "__main__":
    start_scheduler()
    port = 8000
    if len(sys.argv) > 1:
        port = int(sys.argv[1])
    else:
        port = int(os.environ.get("PORT", 8000))
    app.run(host="0.0.0.0", port=port, threaded=True, debug=False)
