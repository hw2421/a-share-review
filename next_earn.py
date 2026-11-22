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

v86 新增 nx（次日＝今日复盘看昨推荐股时的"今日表现"，用全量KBARS算）：
      chg(次日涨幅%) / high(次日最高涨幅%) / zt(次日是否收涨停0/1) /
      low(次日最低涨幅%) / sh(次日是否触及涨停0/1，含炸板) / op(次日开盘涨幅%)。
      无次日bar则 nx=None（今日待验证）。
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
            # v86：次日（＝站在次日看就是"今日"）表现，用全量日K算，覆盖远好于前端 klines
            nx = None
            bd1 = dbar(c, d1) if d1 else None
            if bd1 and bt:
                prevc = bt[2]                      # 今日（候选日）收盘＝次日昨收
                if prevc > 0:
                    o1, c1, l1, h1 = bd1[1], bd1[2], bd1[3], bd1[4]
                    lim = (.05 if "ST" in nm.upper() else
                           .20 if str(c).startswith(("30", "688")) else
                           .30 if str(c).startswith(("8", "4", "92")) else .10)
                    limp = lim * 100
                    chg1 = round((c1 / prevc - 1) * 100, 2)
                    hgp = (h1 / prevc - 1) * 100
                    # 用涨幅%判定，容差0.3pct（涨停价四舍五入到分会使实际涨幅略偏离理论值）；
                    # 前端对「ok=1兑现」强制按涨停显示，兼容个别涨停价四舍五入边界。
                    nx = {
                        "chg": chg1,
                        "op": round((o1 / prevc - 1) * 100, 2),
                        "low": round((l1 / prevc - 1) * 100, 2),
                        "high": round(hgp, 2),
                        "zt": 1 if chg1 >= limp - 0.3 else 0,
                        "sh": 1 if hgp >= limp - 0.3 else 0,   # 盘中触及涨停（含炸板）
                    }
            arr.append({
                "code": c, "name": nm, "b": b, "theme": th,
                "s": shape, "t": t if t[:2].isdigit() else "",
                "hi": hi, "gap": 0, "ok": ok,
                "b1": (ev or {}).get("b") if ev else None,
                "d1": d1, "nx": nx,
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
