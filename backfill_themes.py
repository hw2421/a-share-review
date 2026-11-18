# -*- coding: utf-8 -*-
"""按日期补抓湖南人淘股吧复盘文章的题材数据（补 themeDetail 缺口）。

背景：2026-09-28 ~ 2026-10-16 这批文章在 PC 版是"登录可查看全文"，
正文只剩 [图片] 占位、拿不到图片地址；改用移动版 m.tgb.cn 抓取。
产出 .theme_backfill.json（日期 -> 题材块列表），由 build_merged.py 合并。

用法：
  python3 backfill_themes.py                 # 自动补所有缺口日期
  python3 backfill_themes.py 20261008 20261009
"""
import json
import os
import re
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import fetch_data as fd

OUT = os.path.dirname(os.path.abspath(__file__))
BF = os.path.join(OUT, ".theme_backfill.json")

MIN_COUNT = 5        # 题材入链门槛：板块涨停 ≥5 只
MIN_MAXB = 2         # 且板块内最高连板 ≥2 板


def load_bf():
    if os.path.exists(BF):
        try:
            return json.load(open(BF, encoding="utf-8"))
        except Exception:
            return {}
    return {}


def pick_themes(blocks):
    """题材块筛选（与主流程一致）：排除非题材块，涨停≥5 且最高连板≥2"""
    ts = []
    for b in blocks:
        if b["name"] in fd.NON_THEME:
            continue
        stocks = b.get("stocks") or []
        maxb = max([s["boards"] for s in stocks] or [0])
        if b["count"] >= MIN_COUNT and maxb >= MIN_MAXB:
            b["maxB"] = maxb
            b.pop("_rows", None)
            ts.append(b)
    return ts


def fetch_one(date, art):
    imgs = fd.article_images(art["url"])
    if not imgs:
        return None, "无图片"
    lines = []
    for u in imgs:
        lines.extend(fd.ocr_image(u))
    if not lines:
        return None, "OCR 空"
    ov, blocks = fd.parse_article(lines, {})
    return {"blocks": blocks, "overview": ov, "imgs": len(imgs),
            "lines": len(lines)}, ""


def main():
    want = sys.argv[1:]
    bf = load_bf()
    arts = {a["date"]: a for a in fd.tgb_articles()}

    if not want:
        data = json.load(open(os.path.join(OUT, "data.json"), encoding="utf-8"))
        have = set(data.get("themeDates") or [])
        miss = [d for d in data["dates"] if d not in have]
        want = [d for d in miss if d in arts]
        print("缺口 %d 天，其中有文章可抓 %d 天：%s"
              % (len(miss), len(want), " ".join(want)))

    ok = 0
    for d in want:
        a = arts.get(d)
        if not a:
            print("%s 无文章（列表外）" % d)
            continue
        t0 = time.time()
        res, err = fetch_one(d, a)
        if not res:
            print("%s %s" % (d, err))
            continue
        ts = pick_themes(res["blocks"])
        bf[d] = {
            "themes": ts,
            "overview": res.get("overview") or {},
            "src": a["url"],
            "imgs": res["imgs"],
        }
        print("%s 图%d 行%d 题材 %s  (%.0fs)" % (
            d, res["imgs"], res["lines"],
            [(t["name"], t["count"], t["maxB"]) for t in ts] or "无",
            time.time() - t0))
        ok += 1
        # 增量保存，避免中断丢进度
        with open(BF, "w", encoding="utf-8") as f:
            json.dump(bf, f, ensure_ascii=False)
        time.sleep(1.0)

    print("完成 %d 天 -> %s（共 %d 天）" % (ok, os.path.basename(BF), len(bf)))


if __name__ == "__main__":
    main()
