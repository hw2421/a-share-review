# -*- coding: utf-8 -*-
"""v82 明日赚钱效应候选——把赚钱效应股底层逻辑前推一天做交易参考。

赚钱效应股口径（v77）：t 日换手涨停、板位≥3，t-1 日换手涨停，
且 t 日最低价 > t-1 日最高价（真跳空，昨日买入者全部获利）。

本模块站在「今日 d」筛选候选：
  · 今日 d 换手涨停（非一字，买得进）；
  · 今日板位 b ≥ 2 —— 这样明日 d1 晋级后即 ≥3 板；
  · 于是「明日 d1 只要换手涨停 + 跳空（d1 最低 > d 最高）+ 晋级到 b+1」
    就恰好构成赚钱效应股。候选即「明日具备成为赚钱效应股潜质」的个股。

兑现判定（回看用）：明日 d1 该股是否真的出现在 earnStreak 事件池
（eventsAll 已编码「换手+跳空+板位≥3」全部条件），命中即 ok=1；
明日无数据（今天）ok=None 待验证；否则 ok=0 未兑现。

字段：d / code / name / b(今日板位) / theme / s(形态) / t(首封) /
      gap(今日缺口0/1) / hi(今日最高=明日须超过门槛) /
      ok(1兑现/0未兑现/None待验证) / b1(明日板位) / d1(明日日期)。
"""
import bisect


def compute(dates, pools, yz, KBARS, theme_at, earn_events, shape_of,
            NAME=None, ALL_N=140):
    NAME = NAME or {}
    CD = {c: sorted(r) for c, r in KBARS.items()}

    def dbar(c, d):
        b = (KBARS.get(str(c)) or {}).get(d)
        return b if (b and len(b) >= 6 and b[2]) else None

    evmap = {}
    for e in earn_events or []:
        evmap[(e.get("d"), e.get("code"))] = e

    n = len(dates)
    all_start = dates[max(0, n - ALL_N)]
    byDate = {}
    for i, d in enumerate(dates):
        if d < all_start:
            continue
        d1 = dates[i + 1] if i + 1 < n else ""
        yzd = set(yz.get(d) or ())
        arr = []
        for s in pools.get(d, []):
            c = str(s["code"])
            b = s.get("boards") or 1
            if b < 2:
                continue
            if c in yzd:
                continue                       # 今日须换手（非一字，买得进）
            bt = dbar(c, d)
            if not bt:
                continue
            hi = bt[4]                         # 今日最高 = 明日须超过的跳空门槛
            nm = s.get("name") or NAME.get(c) or c
            th = theme_at.get(d, {}).get(c) or ""
            t = str(s.get("first_seal") or s.get("t") or "")[:5]
            shape = shape_of(c, d, nm) if shape_of else ""
            ev = evmap.get((d1, c)) if d1 else None
            ok = 1 if ev else (0 if d1 else None)
            arr.append({
                "code": c, "name": nm, "b": b, "theme": th,
                "s": shape, "t": t if t[:2].isdigit() else "",
                "hi": hi, "gap": 0, "ok": ok,
                "b1": (ev or {}).get("b") if ev else None,
                "d1": d1,
            })
        if arr:
            arr.sort(key=lambda x: (-x["b"], x["code"]))
            byDate[d] = arr

    return {
        "days": sorted(byDate.keys()),
        "byDate": byDate,
        "rule": (
            "明日赚钱效应候选（交易参考）：今日换手涨停（非一字、买得进）且板位≥2 的个股。"
            "明日只要换手涨停、板位晋级到≥3 板，且明日最低价＞今日最高价（真跳空，"
            "今日任意价位买入者明日全部获利），即成为赚钱效应股。"
            "「兑现」＝历史回看时明日确实成为赚钱效应股（与赚钱效应股同口径）。"
        ),
    }
