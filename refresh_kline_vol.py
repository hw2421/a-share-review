# -*- coding: utf-8 -*-
"""一次性：重抓日K缓存，补充成交量字段 [date,o,c,l,h,vol]"""
import json
import os
import sys
import time

OUT = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, OUT)
import fetch_data as fd

log = lambda m: print(m, flush=True)

for f, n in ((".kline_cache.json", 60), (".stock_kline_cache.json", 520)):
    p = os.path.join(OUT, f)
    if not os.path.exists(p):
        continue
    c = json.load(open(p, encoding="utf-8"))
    log("刷新 %s（%d 只，%d 根）..." % (f, len(c), n))
    done = 0
    for code in list(c):
        bars = fd.sina_kline(code, n)
        if bars:
            c[code] = bars
            done += 1
        time.sleep(0.22)
        if done % 200 == 0:
            log("  %d" % done)
    with open(p, "w", encoding="utf-8") as fp:
        json.dump(c, fp, ensure_ascii=False, separators=(",", ":"))
    log("完成 %s：%d 只带量" % (f, done))
    if f == ".kline_cache.json":          # 同步进 data.json 的 klines
        dp = os.path.join(OUT, "data.json")
        d = json.load(open(dp, encoding="utf-8"))
        d["klines"] = c
        with open(dp, "w", encoding="utf-8") as fp:
            json.dump(d, fp, ensure_ascii=False)
log("全部完成")
