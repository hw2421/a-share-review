# -*- coding: utf-8 -*-
"""湖南人复盘主贴「每半小时轮询、抓到即止」。

平台定时任务在 16:30-23:30 每 30 分钟调用一次 `--once`：

  1) 今日题材已是湖南人主贴（themeSource = tgb / weibo）→ 直接退出，
     本轮及后续各轮都不再抓取、不再重建（幂等）。
  2) 主贴还没发布 → 轻量退出（只查列表页，不重建），等下一轮。
  3) 主贴已发布 → 淘股吧文章 OCR 优先，失败回退微博纯文本
     → 写 .theme_backfill.json（题材）/ .hunan_boards.json（连板锚点）
     → 调 update_daily.py --slot 5 --force 重建并发布站点。

判定「提取成功」：重建后 data.json 里 themeSource[今日] ∈ {tgb, weibo}
且 themeDetail[今日] 非空。

用法：
  python3 hunan_poll.py --once        # 检查一次（定时任务用）
  python3 hunan_poll.py --once --dry  # 只看主贴是否已发布，不抓不建

产物：
  .hunan_poll.json  最近一次检查/抓取状态
  .hunan_poll.log   运行日志（append）
"""
import argparse
import datetime
import json
import os
import subprocess
import sys

BASE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, BASE)

BF = os.path.join(BASE, ".theme_backfill.json")
HB = os.path.join(BASE, ".hunan_boards.json")
STATE = os.path.join(BASE, ".hunan_poll.json")
LOG_F = os.path.join(BASE, ".hunan_poll.log")

MIN_COUNT, MIN_MAXB = 5, 2          # 与 backfill_from_weibo.pick / update_daily 一致


def log(msg):
    line = "[%s] %s" % (datetime.datetime.now().strftime("%m-%d %H:%M:%S"), msg)
    print(line, flush=True)
    try:
        with open(LOG_F, "a", encoding="utf-8") as f:
            f.write(line + "\n")
    except Exception:
        pass


def read_json(path, default):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return default


def write_json(path, obj):
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, indent=1)
    os.replace(tmp, path)


def state(state_, **kw):
    obj = {"date": datetime.datetime.now().strftime("%Y%m%d"),
           "state": state_,
           "checkedAt": datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")}
    obj.update(kw)
    write_json(STATE, obj)


# ---------------------------------------------------------------- 成功判定
def done_today(today):
    """今日题材是否已由湖南人主贴生成。"""
    d = read_json(os.path.join(BASE, "data.json"), {})
    src = (d.get("themeSource") or {}).get(today, "")
    tds = (d.get("themeDetail") or {}).get(today) or []
    return (src in ("tgb", "weibo") and bool(tds)), src, len(tds)


# ---------------------------------------------------------------- 涨停池/连板
def pool_map_for(today):
    """{date: {code: stock}}，stock 带 boards（湖南人锚点优先）。"""
    import fetch_data as fd
    pc = fd.load_pool_cache()
    if not pc.get(today):
        log("涨停池缺 %s，先增量抓一次 …" % today)
        try:
            new = fd.collect_range(today)
            pc = dict(pc)
            pc.update({k: v for k, v in new.items() if v})
            fd.save_pool_cache(pc)
        except Exception as e:
            log("涨停池抓取失败：%s" % e)
    dates = set(read_json(os.path.join(BASE, "data.json"), {}).get("dates") or [])
    _pl = {d: v for d, v in pc.items() if (not dates or d in dates) and v}
    if today not in _pl:
        return None
    hb = read_json(HB, {})
    stk = fd.compute_streaks(_pl, {d: v for d, v in hb.items() if d in _pl})
    for d in _pl:
        for s in _pl[d]:
            s["boards"] = stk[d].get(s["code"], 1)
    return {d: {s["code"]: s for s in _pl[d]} for d in _pl}


def save_boards(blocks, today):
    """把湖南人表里标了连板数的个股写成锚点，供之后交易日递推。"""
    hb = read_json(HB, {})
    n = 0
    for b in blocks:
        if b["name"] in ("涨停炸板", "炸板", "跌停", "跌停股", "昨日涨停表现"):
            continue
        for s in b.get("stocks", []):
            if s.get("hunan"):
                hb.setdefault(today, {})[s["code"]] = s["boards"]
                n += 1
    if n:
        write_json(HB, hb)
    return n


def filter_themes(blocks, fd):
    ts = []
    for b in blocks:
        if b["name"] in fd.NON_THEME:
            continue
        maxb = max([s["boards"] for s in b.get("stocks") or []] or [0])
        if b["count"] >= MIN_COUNT and maxb >= MIN_MAXB:
            b["maxB"] = maxb
            ts.append(b)
    return ts


# ---------------------------------------------------------------- 主贴探测
def tgb_post_today(today):
    import fetch_data as fd
    try:
        arts = fd.tgb_articles()
    except Exception as e:
        log("淘股吧列表不可用：%s" % e)
        return None
    hits = [a for a in arts if a["date"] == today and "午间" not in (a["title"] or "")]
    return hits[0] if hits else None


def weibo_post_today(today):
    import backfill_from_weibo as BW
    try:
        posts = BW.list_posts()
    except Exception as e:
        log("微博列表不可用：%s" % e)
        return None
    hits = [p for p in posts if p["date"] == today and not p.get("noon")]
    return max(hits, key=lambda p: p.get("time") or "") if hits else None


# ---------------------------------------------------------------- 抓取
def fetch_tgb(post, today, pm):
    """淘股吧主贴：正文图 OCR → 解析题材表。失败返回 None。"""
    import fetch_data as fd
    try:
        imgs = fd.article_images(post["url"])
    except Exception as e:
        log("淘股吧正文图抓取失败：%s" % e)
        return None
    log("淘股吧主贴 %s，图片 %d 张，开始 OCR（有缓存则很快）…"
        % (post["url"], len(imgs)))
    lines = []
    for u in imgs:
        try:
            lines.extend(fd.ocr_image(u))
        except Exception as e:
            log("  OCR 失败 %s：%s" % (u[-40:], e))
    if not lines:
        log("OCR 无文本（登录墙/图裂），转微博兜底")
        return None
    d = fd.table_date(lines, today)
    if d != today:
        log("表内日期为 %s（非今日，可能贴的旧表），本轮跳过" % d)
        return None
    try:
        _ov, blocks = fd.parse_article(lines, pm.get(today) or {})
    except Exception as e:
        log("题材表解析失败：%s" % e)
        return None
    ts = filter_themes(blocks, fd)
    if not ts:
        log("解析出 %d 个板块但都不达门槛（题材数≥%d 且最高板≥%d）"
            % (len(blocks), MIN_COUNT, MIN_MAXB))
        return None
    n_hb = save_boards(blocks, today)
    return {"themes": ts, "src": post["url"], "via": "tgb",
            "postTime": post.get("title", "")[:40], "boards": n_hb}


def fetch_weibo(post, today):
    """微博同源纯文本（淘股吧抓不到时的兜底）。"""
    import backfill_from_weibo as BW
    txt = BW.post_text(post["url"])
    if not txt:
        log("微博正文抓取失败：%s" % post["url"])
        return None
    ts = BW.pick(BW.wt.theme_blocks(txt))
    if not ts:
        log("微博正文解析不到题材块")
        return None
    return {"themes": ts, "src": post["url"], "via": "weibo",
            "postTime": post.get("time", "")}


def build_and_publish():
    log("开始重建（update_daily --slot 5 --force）…")
    # v34：站点是单端口应用（app.py 直接服务本目录），构建完不调 publish.js，
    # 否则会把线上应用覆盖成静态托管。
    env = dict(os.environ, SKIP_PUBLISH="1")
    r = subprocess.run(
        [sys.executable, os.path.join(BASE, "update_daily.py"), "--slot", "5", "--force"],
        capture_output=True, text=True, timeout=1500, cwd=BASE, env=env)
    tail = "\n".join((r.stdout or "").strip().splitlines()[-8:])
    if tail:
        log("重建输出：\n" + tail)
    if r.returncode != 0:
        log("重建失败（exit=%d）：%s" % (r.returncode, (r.stderr or "")[-300:]))
        return False
    return True


# ---------------------------------------------------------------- main
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--once", action="store_true", help="检查一次后退出（默认）")
    ap.add_argument("--dry", action="store_true", help="只看主贴是否已发布")
    ap.add_argument("--force", action="store_true", help="今日已就绪也强制重抓重建（测试用）")
    args = ap.parse_args()

    now = datetime.datetime.now()
    today = now.strftime("%Y%m%d")
    # 时段闸门：定时任务每 30 分钟触发一次，只在 16:30-23:59 真正干活。
    # 窗口外立即退出（不发请求、不写日志），避免全天空跑产生噪音。
    if not args.force:
        hm = now.hour * 60 + now.minute
        if not (16 * 60 + 30 <= hm <= 23 * 60 + 59):
            return 0
    if now.weekday() >= 5:
        log("周末（%s），不抓取" % now.strftime("%Y-%m-%d"))
        state("weekend")
        return 0

    ok, src, n = done_today(today)
    if ok and not args.force:
        log("今日（%s）题材已就绪：来源 %s，%d 个题材 —— 本轮及后续无需再抓"
            % (today, src, n))
        state("done", src=src, themes=n, skipped=True)
        return 0

    tp = tgb_post_today(today)
    wp = weibo_post_today(today)
    if not tp and not wp:
        log("今日（%s）湖南人收盘复盘尚未发布（淘股吧/微博都未见到），%s 下一轮再试"
            % (today, now.strftime("%H:%M")))
        state("waiting", checkedAt=now.strftime("%Y-%m-%d %H:%M:%S"))
        return 0

    if args.dry:
        log("已发布：%s" % ((tp or {}).get("url") or (wp or {}).get("url")))
        state("published", dry=True)
        return 0

    if ok and args.force:
        log("--force：今日已就绪（%s）仍强制重抓" % src)
    if not ok and src == "ztpool":
        log("注意：今日题材目前是同花顺兜底（ztpool），本轮用主贴覆盖")

    # 两个源都抓一遍，取题材更全的一侧（并列时优先淘股吧原表）
    cands = []
    if tp:
        pm = pool_map_for(today)
        if pm:
            g = fetch_tgb(tp, today, pm)
            if g:
                cands.append(g)
        else:
            log("涨停池缺今日数据，淘股吧表格无法对码，先走微博兜底")
    if wp:
        g = fetch_weibo(wp, today)
        if g:
            cands.append(g)
    got = max(cands, key=lambda g: (len(g["themes"]), g["via"] == "tgb")) if cands else None

    if not got:
        # 文章已发但正文里还没有复盘表（如只有「晚间消息汇总」、或图片未上传完），
        # 不算失败，等下一轮再试。
        log("主贴已发布但本轮未解析出复盘表，等下一轮：%s"
            % ((tp or wp or {}).get("url")))
        state("waiting", hint="post_without_table")
        return 0

    bf = read_json(BF, {})
    bf[today] = got
    write_json(BF, bf)
    log("抓取成功 %s（via=%s）：%d 个题材 %s"
        % (today, got["via"], len(got["themes"]),
           [(t["name"], t["count"], t.get("maxB")) for t in got["themes"]][:8]))

    if not build_and_publish():
        state("build_failed", via=got["via"])
        return 1

    ok2, src2, n2 = done_today(today)
    if not ok2:
        log("重建完成但校验未通过：themeSource=%s，题材 %d 个" % (src2 or "空", n2))
        state("verify_failed", src=src2, themes=n2)
        return 1

    ver = ""
    try:
        s = open(os.path.join(BASE, "version.js"), encoding="utf-8").read()
        ver = s.split('"')[1] if '"' in s else ""
    except Exception:
        pass
    # v36：同步到 GitHub Pages（仓库 main 分支直出站点，沙箱休眠也不停服）
    try:
        r = subprocess.run([sys.executable, os.path.join(BASE, "pages_sync.py")],
                           capture_output=True, text=True, timeout=1200, cwd=BASE)
        log("GitHub 同步：%s" % ((r.stdout or r.stderr or "").strip()[-160:] or "无输出"))
    except Exception as e:
        log("GitHub 同步失败（不影响本地站点）：%s" % e)
    log("完成：题材来源 %s，%d 个题材，数据版本 %s —— 后续轮次自动停止"
        % (src2, n2, ver))
    state("done", src=src2, themes=n2, via=got["via"], ver=ver)
    return 0


if __name__ == "__main__":
    sys.exit(main())
