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


# ---------------------------------------------------------------- 时段 / 数据来源
def phase_now():
    """刷新所处时段：工作日 09:25-15:05 视为「盘中」，其余为「盘后」。
    盘中只以同花顺公开数据（实时涨停池 / 日K）为准；盘后再用湖南人收盘复盘题材补充。"""
    now = datetime.datetime.now()
    hm = now.hour * 60 + now.minute
    intraday = (now.weekday() < 5 and 9 * 60 + 25 <= hm < 15 * 60 + 5)
    return ("盘中", now.strftime("%Y%m%d")) if intraday else ("盘后", now.strftime("%Y%m%d"))


def theme_source_of(today):
    """读 data.json 里最新日的题材来源代码（tgb/weibo/excel/ztpool）。"""
    try:
        d = json.load(open(os.path.join(BASE, "data.json"), encoding="utf-8"))
        return (d.get("themeSource") or {}).get(today, "") or ""
    except Exception:
        return ""


def source_desc(phase, today, hunan_note=""):
    """给前端展示的数据来源描述。题材来源取自 data.json，如实反映实际口径。"""
    SRC = {"tgb": "湖南人复盘·淘股吧", "weibo": "湖南人复盘·微博",
           "excel": "历史复盘长图", "ztpool": "同花顺涨停池"}
    code = theme_source_of(today)
    src = SRC.get(code, code or "同花顺涨停池")
    if phase == "盘中":
        return "同花顺公开数据（盘中实时）· 题材暂用 " + src
    # 盘后
    if code in ("tgb", "weibo", "excel"):
        return "湖南人题材复盘 + 同花顺涨停池（盘后更新）" + (" · " + hunan_note if hunan_note else "")
    return "同花顺涨停池（湖南人复盘待发布）" + (" · " + hunan_note if hunan_note else "")


def try_hunan_backfill(today):
    """盘后：尝试抓取湖南人收盘复盘题材写入 .theme_backfill.json（不重建）。
    由后续 build_merged 统一消费 backfill，避免与定时 hunan_poll 重复重建。
    返回 (抓到与否, 说明)。主贴未发布 / 非交易日 / 解析失败 → (False, 原因)。"""
    try:
        import hunan_poll as HP
        ok, src, n = HP.done_today(today)      # 今日已是湖南人题材 → 幂等跳过
        if ok:
            return True, "今日题材已是湖南人来源（%s，%d 个）" % (src, n)
        tp = HP.tgb_post_today(today)
        wp = HP.weibo_post_today(today)
        if not tp and not wp:
            return False, "湖南人收盘复盘主贴尚未发布"
        pm = HP.pool_map_for(today)
        cands = []
        if tp and pm:
            g = HP.fetch_tgb(tp, today, pm)
            if g:
                cands.append(g)
        if wp:
            g = HP.fetch_weibo(wp, today)
            if g:
                cands.append(g)
        got = max(cands, key=lambda g: (len(g["themes"]), g["via"] == "tgb")) if cands else None
        if not got:
            return False, "主贴已发布但复盘表尚未解析出"
        bf = HP.read_json(HP.BF, {})
        bf[today] = got
        HP.write_json(HP.BF, bf)
        return True, "已补充 %s %d 个题材" % (got["via"], len(got["themes"]))
    except Exception as e:
        return False, "湖南人题材补充跳过：%s" % e


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

    phase, today = phase_now()
    log("刷新时段：%s（%s）" % (phase, today))
    write_status(state="running", step="抓取涨停池", ok=None, msg="", phase=phase)
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

    # 盘后：在用同花顺涨停池重建前，先补抓湖南人收盘复盘题材（幂等；盘中不抓，主贴尚未发布）。
    # 补充结果写 .theme_backfill.json，由紧随其后的 build_merged 统一消费，避免重复重建。
    hunan_note = ""
    if phase == "盘后":
        write_status(state="running", step="补充湖南人题材", ok=None, msg="", latest=latest)
        got, hunan_note = try_hunan_backfill(today)
        log("湖南人题材：%s" % hunan_note)

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
    src = source_desc(phase, latest or today, hunan_note)
    write_status(state="done", step="完成", ok=True, msg="", ver=ver, latest=latest,
                 phase=phase, source=src)
    # 站点元数据同步数据来源（前端 api/status 与静态页均可读）
    try:
        mp = os.path.join(BASE, ".site_meta.json")
        meta = json.load(open(mp, encoding="utf-8")) if os.path.exists(mp) else {}
        meta.update(ver=ver, latest=latest, source=src, phase=phase,
                    updated=datetime.datetime.now().strftime("%Y-%m-%d %H:%M"))
        tmp = mp + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(meta, f, ensure_ascii=False)
        os.replace(tmp, mp)
    except Exception as e:
        log("站点元数据写入失败：%s" % e)
    log("完成，版本 %s，数据截至 %s，来源 %s" % (ver, latest, src))
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
