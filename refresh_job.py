# -*- coding: utf-8 -*-
"""手动刷新任务（给 app.py 调用）：抓当日涨停池 → 重建全站数据（不发布）。

页面「⟳ 刷新」→ POST /api/refresh → 本脚本在后台线程执行 → 完成后页面自动重载。
与 update_daily.py 的区别：
  · 不做「今天已完成就跳过」的幂等判断（用户手动点就必须真跑）；
  · 不调用 publish.js（站点由本进程直接服务本地文件，构建产物立即生效）。
"""
import datetime
import json
import os
import subprocess
import sys

BASE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, BASE)
STATUS_F = os.path.join(BASE, ".refresh_status.json")


def log(msg):
    print("[%s] %s" % (datetime.datetime.now().strftime("%H:%M:%S"), msg), flush=True)


def write_status(**kw):
    st = {}
    try:
        st = json.load(open(STATUS_F, encoding="utf-8"))
    except Exception:
        pass
    st.update(kw)
    st["ts"] = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    with open(STATUS_F, "w", encoding="utf-8") as f:
        json.dump(st, f, ensure_ascii=False, indent=1)


def main():
    # 与每日定时更新共用一把构建锁：手动刷新撞上 16:00 自动更新时，后来者直接退出，
    # 避免两个 build_merged 同时写 data.js / version.js 造成半截文件。
    import fcntl
    lk = open(os.path.join(BASE, ".build.lock"), "w")
    try:
        fcntl.flock(lk, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        log("已有构建在进行，本次跳过")
        write_status(state="busy", step="等待", ok=False,
                     msg="后台正在构建（定时更新或另一次刷新），请稍后再点")
        return 2

    write_status(state="running", step="抓取涨停池", ok=None, msg="")
    latest = ""
    try:
        import fetch_data as fd
        pc = fd.load_pool_cache()
        last = max(pc) if pc else "20200101"
        start = (datetime.datetime.strptime(last, "%Y%m%d")
                 - datetime.timedelta(days=6)).strftime("%Y%m%d")
        log("抓取涨停池 %s 至今" % start)
        new = fd.collect_range(start)
        pc2 = dict(pc)
        pc2.update({d: v for d, v in (new or {}).items() if v})
        fd.save_pool_cache(pc2)
        latest = max(pc2) if pc2 else ""
        log("涨停池 %d 天，最新 %s" % (len(pc2), latest))
    except Exception as e:
        log("涨停池抓取失败：%s" % e)
        write_status(state="error", step="抓取涨停池", ok=False, msg="涨停池抓取失败：%s" % e)
        return 1

    write_status(state="running", step="重建全站数据", ok=None, msg="", latest=latest)
    try:
        env = dict(os.environ, SKIP_KLINES="1")
        r = subprocess.run([sys.executable, os.path.join(BASE, "build_merged.py")],
                           capture_output=True, text=True, timeout=900, env=env)
        if r.returncode != 0:
            tail = (r.stderr or r.stdout or "")[-400:]
            log("构建失败：%s" % tail)
            write_status(state="error", step="重建全站数据", ok=False,
                         msg="重建失败：%s" % tail[-200:])
            return 1
        log("构建完成")
    except Exception as e:
        log("构建异常：%s" % e)
        write_status(state="error", step="重建全站数据", ok=False, msg="重建异常：%s" % e)
        return 1

    ver = ""
    try:
        s = open(os.path.join(BASE, "version.js"), encoding="utf-8").read()
        ver = s.split('"')[1] if '"' in s else ""
    except Exception:
        pass
    write_status(state="done", step="完成", ok=True, msg="", ver=ver, latest=latest)
    log("完成，版本 %s，数据截至 %s" % (ver, latest))
    # 手动刷新成功后也同步 GitHub Pages（常驻站点），无变化时脚本自动跳过
    try:
        ps = subprocess.run([sys.executable, os.path.join(BASE, "pages_sync.py")],
                            capture_output=True, text=True, timeout=600)
        log("Pages 同步：%s" % ((ps.stdout or "").strip().splitlines()[-1][:120] if (ps.stdout or "").strip() else "无输出"))
    except Exception as e:
        log("Pages 同步失败（不影响本站）：%s" % e)
    return 0


if __name__ == "__main__":
    sys.exit(main())
