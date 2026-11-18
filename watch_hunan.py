# -*- coding: utf-8 -*-
"""湖南人主贴「发出即抓」：微博「TGB湖南人」当日收盘复盘一发布，就抓题材并重建站点。

用法：
  python3 watch_hunan.py --once          # 检查一次（平台定时任务每 10 分钟调用）
  python3 watch_hunan.py --once --dry    # 只检查是否已发布，不抓不建（调试）

判定逻辑：
  · 周末：退出（0）
  · 当日收盘复盘（非午间版）未发布：退出（0），日志写明已检查时间
  · 已发布且与上次抓取的 url / 发布时间一致：退出（0），不重复构建
  · 已发布且是新的（或主贴被更新重发）：抓正文 → 解析题材块 → 写 .theme_backfill.json
    → 调 update_daily.py --slot 5 --force 重建并发布

产物：
  .theme_backfill.json   题材补抓（build_merged 里优先级最高，覆盖同花顺兜底）
  .hunan_watch.json      最近一次检查/抓取状态
  .hunan_watch.log       运行日志（append）
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
WATCH = os.path.join(BASE, ".hunan_watch.json")
LOG_F = os.path.join(BASE, ".hunan_watch.log")


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


def today_close_posts(posts, today):
    """当日收盘版（非午间）复盘，按发布时间取最晚一条。"""
    hits = [p for p in posts if p.get("date") == today and not p.get("noon")]
    return max(hits, key=lambda p: p.get("time") or "") if hits else None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--once", action="store_true", help="检查一次后退出（默认行为）")
    ap.add_argument("--dry", action="store_true", help="只检查是否已发布，不抓取不重建")
    args = ap.parse_args()

    now = datetime.datetime.now()
    today = now.strftime("%Y%m%d")
    if now.weekday() >= 5:
        log("周末（%s），不检查" % now.strftime("%Y-%m-%d"))
        return 0

    import backfill_from_weibo as BW

    try:
        posts = BW.list_posts()
    except Exception as e:
        log("微博列表页不可用：%s" % e)
        return 1
    if not posts:
        log("微博列表页返回空（可能被限流），稍后重试")
        return 1

    post = today_close_posts(posts, today)
    if not post:
        log("今日（%s）收盘复盘尚未发布，已检查 %s"
            % (today, now.strftime("%H:%M")))
        write_json(WATCH, {"date": today, "state": "waiting",
                           "checkedAt": now.strftime("%Y-%m-%d %H:%M:%S")})
        return 0

    if args.dry:
        log("今日主贴已发布：%s %s（dry，不抓取）" % (post["time"], post["url"]))
        return 0

    bf = read_json(BF, {})
    prev = bf.get(today) or {}
    if (prev.get("src") == post["url"]
            and prev.get("postTime") == post["time"]
            and prev.get("themes")):
        log("今日主贴已抓取过（%s %s），无需重复" % (post["time"], post["url"]))
        write_json(WATCH, {"date": today, "state": "done",
                           "postTime": post["time"], "url": post["url"],
                           "themes": len(prev["themes"]),
                           "checkedAt": now.strftime("%Y-%m-%d %H:%M:%S")})
        return 0

    txt = BW.post_text(post["url"])
    if not txt:
        log("主贴正文抓取失败（可能被限流），稍后重试：%s" % post["url"])
        return 1
    ts = BW.pick(BW.wt.theme_blocks(txt))
    if not ts:
        log("主贴解析不到题材块（格式可能变了），未写入：%s" % post["url"])
        return 1

    bf[today] = {"themes": ts, "src": post["url"], "via": "weibo",
                 "postTime": post["time"]}
    write_json(BF, bf)
    log("抓取成功 %s（%s）：%d 个题材 %s"
        % (today, post["time"], len(ts),
           [(t["name"], t["count"], t["maxB"]) for t in ts][:8]))

    log("开始重建（update_daily --slot 5 --force）…")
    r = subprocess.run(
        [sys.executable, os.path.join(BASE, "update_daily.py"), "--slot", "5", "--force"],
        capture_output=True, text=True, timeout=1500, cwd=BASE)
    tail = "\n".join((r.stdout or "").strip().splitlines()[-12:])
    if tail:
        log("重建输出：\n" + tail)
    if r.returncode != 0:
        log("重建失败（exit=%d）：%s" % (r.returncode, (r.stderr or "")[-300:]))
        write_json(WATCH, {"date": today, "state": "build_failed",
                           "postTime": post["time"], "url": post["url"],
                           "checkedAt": datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")})
        return 1

    ver = ""
    try:
        s = open(os.path.join(BASE, "version.js"), encoding="utf-8").read()
        ver = s.split('"')[1] if '"' in s else ""
    except Exception:
        pass
    log("完成：题材已按湖南人主贴更新，数据版本 %s" % ver)
    write_json(WATCH, {"date": today, "state": "done", "postTime": post["time"],
                       "url": post["url"], "themes": len(ts), "ver": ver,
                       "checkedAt": datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")})
    return 0


if __name__ == "__main__":
    sys.exit(main())
