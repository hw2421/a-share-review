# -*- coding: utf-8 -*-
"""从新浪微博「TGB湖南人」补抓指定日期的题材数据（纯文本，无需 OCR）。

淘股吧 PC 版对较早文章要求登录（正文只剩 [图片] 占位），
微博同源发布且是纯文本，解析更准。产出 .theme_backfill.json，
由 build_merged.py 合并进 data.json 的 themeDetail。

用法：
  python3 backfill_from_weibo.py            # 自动补 themeDates 缺口
  python3 backfill_from_weibo.py 20261012   # 指定日期
"""
import html
import json
import os
import re
import subprocess
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import fetch_data as fd
import weibo_themes as wt

OUT = os.path.dirname(os.path.abspath(__file__))
BF = os.path.join(OUT, ".theme_backfill.json")
LIST_URL = "https://sina.cn/media/1798332054"
UA = ("Mozilla/5.0 (iPhone; CPU iPhone OS 16_0 like Mac OS X) "
      "AppleWebKit/605.1.15 (KHTML, like Gecko) Version/16.0 "
      "Mobile/15E148 Safari/604.1")
MIN_COUNT, MIN_MAXB = 5, 2


def get(url, tries=5):
    """抓页面。新浪对频繁请求会 302 到首页或直接空响应，需重试。"""
    for i in range(tries):
        r = subprocess.run(
            ["curl", "-s", "--max-time", "25", "-A", UA,
             "-w", "\n@@%{http_code}", url], capture_output=True)
        s = r.stdout.decode("utf-8", "ignore")
        m = re.search(r"\n@@(\d{3})$", s)
        code = m.group(1) if m else "000"
        body = s[:m.start()] if m else s
        if code == "200" and len(body) > 5000:
            return body
        time.sleep(2 + i * 1.5)
    return ""


def list_posts():
    """微博列表页 -> [{date, url, time, kind}]（仅收盘版复盘）"""
    h = get(LIST_URL)
    items = re.findall(
        r'<a class="post-link" href="(/news/detail/\d+\.html)">\s*'
        r'<article class="post">.*?<div class="time">([\d\-: ]+).*?'
        r'<div class="post-text">(.*?)</div>', h, re.S)
    out = []
    for href, tm, txt in items:
        s = html.unescape(re.sub(r"<[^>]+>", "", txt)).strip()
        m = re.match(r"【(\d{1,2})\.(\d{1,2})[^】]*】\s*湖南人(午间)?涨停复盘", s)
        if not m:
            continue
        year = tm.strip()[:4]
        date = "%s%02d%02d" % (year, int(m.group(1)), int(m.group(2)))
        out.append({"date": date, "url": "https://sina.cn" + href,
                    "time": tm.strip(), "noon": bool(m.group(3))})
    return out


def post_text(url, retry=1):
    """微博全文：优先 <article class="article-content">（<br/> 分行），
    其次 meta description（部分是 AI 摘要，不可用）"""
    h = get(url)
    if not h:
        return ""
    m = re.search(r'<article class="article-content">(.*?)</article>', h, re.S)
    if m:
        seg = re.sub(r"<br\s*/?>", "\n", m.group(1))
        seg = re.sub(r"<[^>]+>", "", seg)
        seg = html.unescape(seg)
        if "末次时间" in seg:
            return seg
    m = re.search(r'property="description"\s+content="(.*?)">\s*<', h, re.S)
    if m:
        t = html.unescape(m.group(1))
        if "末次时间" in t:
            return t
    return ""


def pick(blocks):
    ts = []
    for b in blocks:
        if b["name"] in fd.NON_THEME:
            continue
        stocks = b.get("stocks") or []
        maxb = max([s["boards"] for s in stocks] or [0])
        if b["count"] >= MIN_COUNT and maxb >= MIN_MAXB:
            b["maxB"] = maxb
            ts.append(b)
    return ts


def main():
    bf = {}
    if os.path.exists(BF):
        try:
            bf = json.load(open(BF, encoding="utf-8"))
        except Exception:
            bf = {}

    posts = [p for p in list_posts() if not p["noon"]]
    by_date = {}
    for p in posts:                      # 同日多篇取时间最晚（收盘版）
        old = by_date.get(p["date"])
        if not old or p["time"] > old["time"]:
            by_date[p["date"]] = p
    print("微博收盘复盘 %d 天（%s ~ %s）"
          % (len(by_date), min(by_date), max(by_date)))

    want = sys.argv[1:]
    if not want:
        data = json.load(open(os.path.join(OUT, "data.json"), encoding="utf-8"))
        have = set(data.get("themeDates") or [])
        want = [d for d in data["dates"] if d not in have and d in by_date]
        print("缺口中有微博可补 %d 天：%s" % (len(want), " ".join(want)))

    ok = 0
    for d in want:
        p = by_date.get(d)
        if not p:
            print("%s 微博无当日收盘复盘" % d)
            continue
        txt = post_text(p["url"])
        if not txt:
            print("%s 正文为空" % d)
            continue
        blocks = wt.theme_blocks(txt)
        ts = pick(blocks)
        bf[d] = {"themes": ts, "src": p["url"], "via": "weibo",
                 "postTime": p["time"]}
        print("%s 块%d 题材 %s" % (
            d, len(blocks),
            [(t["name"], t["count"], t["maxB"]) for t in ts] or "无"))
        ok += 1
        with open(BF, "w", encoding="utf-8") as f:
            json.dump(bf, f, ensure_ascii=False)
        time.sleep(0.6)

    print("完成 %d 天 -> %s（共 %d 天）"
          % (ok, os.path.basename(BF), len(bf)))


if __name__ == "__main__":
    main()
