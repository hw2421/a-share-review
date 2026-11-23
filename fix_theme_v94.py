# -*- coding: utf-8 -*-
"""v94 补全缺题材个股：对照湖南人复盘题材归类，补全全局题材库 stockTheme。

背景：build_merged.py 3.1 段题材块会因「个股当日必须涨停」清洗、以及
文章快照/补抓逐日覆盖 Excel OCR 清单，丢掉一批个股的题材归类，导致
全局题材库 stockTheme 缺记录，前端 themeOf() 三级反查返回 null → 显示
「无题材」。用户口径：湖南人复盘里所有数据都有、不可能缺失。

本脚本直接从三个原始题材卡源叠加 + 涨停原因归一补全 stockTheme，
不改 theme_at / themeDetail / 题材炒作图入链门槛（明日推荐严格口径不变）。
逻辑与 build_merged.py 3.1b/3.2b 段完全一致，可重复运行（幂等）。
"""
import json
import os
import re

import fetch_data as fd
import theme_alias
import datajs

OUT = os.path.dirname(os.path.abspath(__file__))
P = lambda f: os.path.join(OUT, f)

d = json.load(open(P("data.json"), encoding="utf-8"))
carry = d.setdefault("stockTheme", {})
name_map = d.get("nameMap", {})

# ---- 1) 三源叠加收集湖南人题材卡原始归类（code -> 最新(日期, 题材)） ----
RAW_CARD = {}


def snapshot(src):
    for dt, ts in (src or {}).items():
        for t in (ts or []):
            nm = theme_alias.ALIAS.get(t.get("name", ""), t.get("name", ""))
            if nm in _skip:
                continue
            for s in (t.get("stocks") or []):
                c = str(s.get("code") or "").strip()
                if not c:
                    continue
                prev = RAW_CARD.get(c)
                if prev is None or dt > prev[0]:
                    RAW_CARD[c] = (dt, nm)


# 伪题材（公告/新股/次新等）不算有效题材归类，与 fd.NON_THEME 一并过滤
FAKE_THEME = {"公告", "其他", "其他个股", "新股", "次新股", "其他概念",
              "其他板块", "其他热点", "业绩", "业绩增长", "回购", "增持", "重组"}
_skip = fd.NON_THEME | FAKE_THEME

snapshot(json.load(open(P(".excel_cache.json"), encoding="utf-8")).get("themes", {}))
snapshot(json.load(open(P(".theme_articles.json"), encoding="utf-8")))
if os.path.exists(P(".theme_backfill.json")):
    bf = json.load(open(P(".theme_backfill.json"), encoding="utf-8"))
    snapshot({dt: (o or {}).get("themes") or [] for dt, o in bf.items()})

# ---- 2) 涨停原因归一映射（题材名必须落在已有题材内） ----
_REASON_KW = [
    ("GPU", "半导体"), ("芯片", "半导体"), ("半导体", "半导体"), ("存储", "半导体"),
    ("光刻", "半导体"), ("靶材", "半导体"), ("MicroLED", "半导体"), ("封装", "半导体"),
    ("CPO", "光通信"), ("光芯片", "光通信"), ("MPO", "光通信"), ("硅光", "光通信"),
    ("AI眼镜", "消费电子"), ("消费电子", "消费电子"),
    ("小家电", "消费"), ("家电", "消费"), ("百货", "消费"), ("零售", "消费"),
    ("食品", "消费"), ("免税", "消费"),
    ("AI算力", "算力"), ("算力", "算力"), ("液冷", "算力"),
    ("服务器", "算力"), ("数据中心", "算力"), ("云计算", "算力"),
    ("人形机器人", "机器人"), ("具身", "机器人"), ("机器人", "机器人"),
    ("光伏", "光伏"), ("储能", "光伏"),
    ("固态电池", "固态电池"), ("锂电池", "锂电池"), ("钠电池", "锂电池"),
    ("变压器", "智能电网"), ("电网", "智能电网"),
    ("可控核聚变", "可控核聚变"), ("核聚变", "可控核聚变"), ("核电", "可控核聚变"),
    ("短剧", "短剧"), ("影视", "短剧"), ("游戏", "短剧"),
    ("创新药", "医药"), ("医药", "医药"), ("医疗", "医药"), ("CRO", "医药"),
    ("商业航天", "商业航天"), ("航天", "商业航天"), ("卫星", "商业航天"), ("军工", "商业航天"),
    ("深海", "深海科技"), ("房地产", "房地产"), ("地产", "房地产"),
    ("跨境电商", "跨境电商"), ("跨境支付", "跨境电商"),
    ("光模块", "光通信"), ("光通信", "光通信"),
    ("人工智能", "AI应用"), ("AI应用", "AI应用"), ("AI", "AI应用"), ("数据要素", "AI应用"),
    ("黄金", "黄金"), ("有色金属", "有色金属"), ("有色", "有色金属"),
    ("物流", "物流"), ("航运", "航运"), ("钢铁", "钢铁"), ("铁矿", "钢铁"),
    ("化工", "化工"), ("聚乳酸", "化工"), ("可降解", "化工"), ("乳酸", "化工"),
    ("农业", "农业"), ("养殖", "农业"), ("生猪", "农业"), ("猪", "农业"),
    ("石油", "石油"), ("油气", "石油"), ("天然气", "天然气"),
    ("全钢巨胎", "汽车零部件"), ("汽车", "汽车产业链"),
    ("传媒", "传媒"), ("出版", "传媒"),
]

all_names = {theme_alias.ALIAS.get(t["name"], t["name"])
             for ts in d.get("themeDetail", {}).values() for t in ts}
all_names |= set(carry.values())


def reason_theme(s):
    for tag in (s.get("reason") or "").replace("＋", "+").split("+"):
        tag = tag.strip()
        if not tag:
            continue
        for kw, nm in _REASON_KW:
            if kw in tag and nm in all_names:
                return nm
    return ""


# ---- 3) 收集涨停池（reason 来源，按日期） ----
pools_by_date = {}
ec = json.load(open(P(".excel_cache.json"), encoding="utf-8"))
for dt, arr in ec.get("pools", {}).items():
    pools_by_date.setdefault(dt, []).extend(arr)
if os.path.exists(P(".pool_cache.json")):
    for dt, arr in json.load(open(P(".pool_cache.json"), encoding="utf-8")).items():
        pools_by_date.setdefault(dt, []).extend(arr)

# ---- 4) 补全（只补缺失 code） ----
n_card = n_rsn = 0
for c, (dt, nm) in sorted(RAW_CARD.items(), key=lambda x: x[1][0]):
    if c not in carry:
        carry[c] = nm
        n_card += 1
for dt in sorted(pools_by_date):
    for s in pools_by_date[dt]:
        c = str(s.get("code") or "").strip()
        if not c or c in carry:
            continue
        nm = reason_theme(s)
        if nm:
            carry[c] = nm
            n_rsn += 1

print("v94 题材卡补 %d 只 + 涨停原因补 %d 只 = %d 只" % (n_card, n_rsn, n_card + n_rsn))
print("补全后 stockTheme 共 %d 只（原 %d）" % (len(carry), len(carry) - n_card - n_rsn))

# ---- 5) 写回 data.json / data.js / version.js ----
print("写回：" + datajs.write_obj(d, OUT))

# ---- 6) 自校验：近25日涨停股 themeOf 反查覆盖 ----
td = d.get("themeDetail", {})
lf = d.get("ladderFull", {})
dz = d.get("dayZT", {})


def theme_of(dt, code):
    for t in td.get(dt, []):
        if any(str(s["code"]) == str(code) for s in t.get("stocks", [])):
            return t["name"]
    for th, arr in lf.get(dt, {}).items():
        if any(str(s["code"]) == str(code) for s in arr):
            return th
    return carry.get(str(code))


tot = miss = 0
rem = []
for dt, mp in dz.items():
    for code, row in mp.items():
        tot += 1
        if not theme_of(dt, str(code)):
            miss += 1
            rem.append((dt, str(code), row[0]))
print("近25日涨停股题材覆盖：%d/%d（缺 %d）" % (tot - miss, tot, miss))
print("仍缺题材：", rem)
