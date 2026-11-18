# -*- coding: utf-8 -*-
"""恢复 data.json 中丢失的 2026 年 themeDetail（tgb OCR 缓存 + 同花顺补充），
然后重新跑 build_merged.py 合并两年数据。"""
import json
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import fetch_data as fd

OUT = os.path.dirname(os.path.abspath(__file__))

# 2026 池直接用磁盘缓存
pool_cache = fd.load_pool_cache()
pools = {d: v for d, v in pool_cache.items() if d >= "20260101" and v}
# 连板口径统一为递推（同花顺原始 boards 是"N天M板"波段口径，不可用）
streaks = fd.compute_streaks(pools)
for _d in pools:
    for s in pools[_d]:
        s["boards"] = streaks[_d].get(s["code"], 1)
dates = sorted(pools)
pool_map = {d: {s["code"]: s for s in pools[d]} for d in dates}
print("2026 池 %d 天（%s ~ %s）" % (len(dates), dates[0], dates[-1]))

# tgb 文章（OCR 走 .ocr_cache.json 缓存）
arts = [a for a in fd.tgb_articles() if a["date"] >= dates[0]]
arts.sort(key=lambda a: a["date"])
print("待处理文章 %d 篇" % len(arts))
theme_detail = {}
hunan_boards = {}                     # tgb 日湖南人连板锚点
for k, a in enumerate(arts, 1):
    if a["date"] not in pool_map:
        continue
    img_urls = fd.article_images(a["url"])
    lines = []
    for u in img_urls:
        lines.extend(fd.ocr_image(u))
    if not lines:
        print("  [%d/%d] %s 无表格内容" % (k, len(arts), a["date"]))
        continue
    d = fd.table_date(lines, a["date"])
    if d not in pool_map:
        prev = [x for x in dates if x <= d]
        if not prev:
            continue
        d = prev[-1]
    ov, blocks = fd.parse_article(lines, pool_map[d])
    # 湖南人连板锚点（排除炸板/跌停/昨涨停表现等非涨停块）
    _poison = {"涨停炸板", "炸板", "跌停", "跌停股", "昨日涨停表现"}
    for b in blocks:
        if b["name"] in _poison:
            continue
        for s in b.get("stocks", []):
            if s.get("hunan"):
                _a = hunan_boards.setdefault(d, {})
                if s["code"] not in _a:
                    _a[s["code"]] = s["boards"]
    ts = []
    for b in blocks:
        if b["name"] in fd.NON_THEME:
            continue
        max_bs = max([s["boards"] for s in b["stocks"]] or [0])
        if b["count"] >= 5 and max_bs >= 2:
            b["maxB"] = max_bs
            ts.append(b)
    if d in theme_detail:
        old = {t["name"]: t for t in theme_detail[d]}
        for t in ts:
            o = old.get(t["name"])
            if o:
                seen = {s["code"] for s in o["stocks"]}
                o["stocks"].extend(
                    s for s in t["stocks"] if s["code"] not in seen)
                o["count"] = max(o["count"], t["count"])
            else:
                theme_detail[d].append(t)
    else:
        theme_detail[d] = ts
    print("  [%d/%d] %s 题材 %s" % (
        k, len(arts), d, [(t["name"], t["count"]) for t in ts] or "无"))
    time.sleep(1.0)

# 湖南人缺失日：同花顺补充（纯本地计算，用缓存池）
theme_source = {}
for d in dates:
    if theme_detail.get(d):
        theme_source[d] = "tgb"
    else:
        fb = fd.ths_fallback_themes(pool_map[d])
        if fb:
            theme_detail[d] = fb
            theme_source[d] = "ths"

# 题材名归一 + 同日合并（与主流程一致）
THEME_ALIAS = {"短剧传媒": "短剧", "机器人+物理AI": "机器人",
               "“华”字辈": "华字辈", '"华"字辈': "华字辈"}
for d in list(theme_detail.keys()):
    ts = theme_detail[d]
    for t in ts:
        t["name"] = THEME_ALIAS.get(t["name"], t["name"])
    agg = {}
    for t in ts:
        o = agg.get(t["name"])
        if not o:
            agg[t["name"]] = t
            continue
        seen = {s["code"] for s in o["stocks"]}
        o["stocks"].extend(s for s in t["stocks"] if s["code"] not in seen)
        o["count"] = max(o["count"], t["count"])
        o["maxB"] = max(o["maxB"], t["maxB"])
        o["amount"] = max(o.get("amount", 0), t.get("amount", 0))
    theme_detail[d] = list(agg.values())

matrix = {}
for d, ts in theme_detail.items():
    for t in ts:
        e = matrix.setdefault(t["name"], {})
        e[d] = {"count": t["count"], "maxB": t["maxB"],
                "amount": t.get("amount", 0)}
matrix = fd.merge_theme_names(matrix)
for d, ts in theme_detail.items():
    for t in ts:
        for r, series in matrix.items():
            if d in series and t["name"] != r and fd._edit_dist(t["name"], r) <= 1:
                t["name"] = r
                break

# 瘦身（与主流程一致）
def _slim(stocks):
    by = {}
    for s in stocks:
        by.setdefault(s["boards"], []).append(s)
    out = []
    for b in sorted(by, reverse=True)[:6]:
        out.extend(by[b][:2])
        for s in by[b][:2]:
            s.pop("reason", None)
    return out

for _d, _ts in theme_detail.items():
    for _t in _ts:
        _t["stocks"] = _slim(_t.get("stocks", []))

print("2026 题材日 %d 天（tgb %d / ths %d）" % (
    len(theme_detail),
    sum(1 for v in theme_source.values() if v == "tgb"),
    sum(1 for v in theme_source.values() if v == "ths")))

# 连板锚点追加到共享文件（build_merged 用其锚定递推口径）
hb_path = os.path.join(OUT, ".hunan_boards.json")
try:
    hb_all = json.load(open(hb_path, encoding="utf-8"))
except Exception:
    hb_all = {}
hb_all.update(hunan_boards)
with open(hb_path, "w", encoding="utf-8") as f:
    json.dump(hb_all, f, ensure_ascii=False)
print("tgb 湖南人锚点 %d 天 -> .hunan_boards.json（共 %d 天）"
      % (len(hunan_boards), len(hb_all)))

# 写回 data.json（当前合并版缺 themeDetail/themeDates，其余字段完好）
data = json.load(open(os.path.join(OUT, "data.json"), encoding="utf-8"))
data["themeDetail"] = theme_detail
data["themeDates"] = sorted(theme_detail)
data["themeSource"] = theme_source
with open(os.path.join(OUT, "data.json"), "w", encoding="utf-8") as f:
    json.dump(data, f, ensure_ascii=False)
print("themeDetail 已恢复 -> data.json；请重跑 build_merged.py")
