# -*- coding: utf-8 -*-
"""v67 连续两日换手赚钱机会个股（3板+）—— 今日复盘页新赚钱效应统计口径。

用户定稿口径（原话要点）：
  统计「t-2 日买入、t-1 日可以赚钱（换手连板非一字）；t-1 日买入、t 日可以赚钱」
  即：t-1、t 连续两个交易日均为换手涨停（非一字，买得进、有换手），
  至少连续两日都有换手赚钱机会，且个股板位为 3 板或以上（按 t 日板位）。

  换手赚钱机会 = 换手连板（收盘涨停 + 非一字）。一字板买不进，两日均排除。
  t-2 买入 → t-1 换手连板 → 赚钱；t-1 买入 → t 换手连板 → 赚钱。
  样本锚定 t 日（最近达标日），t 日板位 ≥3。

辅助标注：
  · 跳空缺口：t 日或 t-1 日「开盘价 > 前一交易日最高价」（真实向上跳空），
    日K形态有跳空缺口更佳，前端单独亮标。
  · 题材：当日复盘归类（theme_at[t]），当日缺失沿用 t-1 复盘归类。
  · 涨停形态：t 日收盘涨停形态（与 build_merged._up_shape 同口径六分类）。

窗口：近 1 日（默认）/ 3 日 / 5 日。结束日为最新交易日，往前取 w 个交易日，
窗口内任一交易日 t 达标的个股均计入（同一股票按最近达标日归组，前端去重展示）。

数据契约（v67）：eventsAll 全量事件池覆盖最近 ALL_N 个交易日；windows 供前端
动态切分。字段：d(t) / d1(t-1) / d0(t-2，可空) / code / name / theme / b(t板位)
/ s(t日形态) / open(t日开盘涨幅) / chg(t日涨幅) / chg1(t-1日涨幅)
/ gapT(t日缺口) / gap1(t-1日缺口) / gap(任一)。

日K bar 结构：[日期, 开, 收, 低, 高, 量]（收盘=b[2]、开盘=b[1]、最高=b[4]）。
"""
import bisect

WINDOWS = {"w1": 1, "w3": 3, "w5": 5}


def lim_of(code, name=""):
    if "ST" in (name or "").upper():
        return .05
    if str(code).startswith(("30", "688")):
        return .20
    if str(code).startswith(("8", "4", "92")):
        return .30
    return .10


def compute(dates, pools, yz, KBARS, theme_at, NAME=None, ALL_N=140):
    """返回 earnStreak 模块数据 dict（eventsAll + windows + rule）。"""
    NAME = NAME or {}
    CD = {c: sorted(r) for c, r in KBARS.items()}

    def dbar(c, d):
        b = (KBARS.get(str(c)) or {}).get(d)
        return b if (b and len(b) >= 6 and b[2]) else None

    def prev_bar(code, d):
        ds = CD.get(str(code))
        if not ds:
            return None
        rec = KBARS.get(str(code)) or {}
        k = bisect.bisect_left(ds, d)
        for j in range(k - 1, -1, -1):
            b = rec.get(ds[j])
            if b and len(b) >= 5 and b[2]:
                return b
        return None

    def shape(code, d, name):
        """涨停形态六分类（与 build_merged._up_shape 完全同口径）。"""
        b = dbar(code, d)
        pb = prev_bar(code, d)
        if not b or not pb:
            return ""
        pv = pb[2]
        if not pv:
            return ""
        lim = lim_of(code, name)
        up, dn = round(pv * (1 + lim), 2), round(pv * (1 - lim), 2)
        if b[2] < up - .011:
            return ""
        amp = (b[4] - b[3]) / pv * 100
        if b[1] <= dn + .011:
            return "地天"
        if b[3] >= up - .004:
            return "一字"
        if b[1] >= up - .011:
            return "赚T" if amp < 8 else ("普通" if amp <= 12 else "长腿")
        if amp > 12:
            return "长腿"
        if amp >= 8:
            return "普通"
        return "赚钱" if b[1] > pv else "低开"

    pmap = {d: {s["code"]: s for s in pools[d]} for d in dates}
    n = len(dates)
    end = dates[-1]
    all_start = dates[max(0, n - ALL_N)]

    evs = []
    for i in range(2, n):
        t, t1, t2 = dates[i], dates[i - 1], dates[i - 2]
        if t < all_start:
            continue
        for code, s in pmap[t].items():
            b = s.get("boards") or 1
            if b < 3:
                continue                       # t 日板位 ≥3
            s1 = pmap[t1].get(code)
            if not s1 or (s1.get("boards") or 1) != b - 1:
                continue                       # t-1 日须连板晋级（板位恰好 b-1）
            # 两日均须换手（非一字，买得进）
            if code in (yz.get(t) or ()) or code in (yz.get(t1) or ()):
                continue
            bt, b1 = dbar(code, t), dbar(code, t1)
            if not bt or not b1:
                continue
            b2 = dbar(code, t2)
            nm = s.get("name") or NAME.get(code) or code
            chg = round((bt[2] / b1[2] - 1) * 100, 2)
            chg1 = round((b1[2] / b2[2] - 1) * 100, 2) if (b2 and b2[2]) else None
            # 跳空缺口：开盘 > 前一交易日最高价（真实向上跳空）
            gap_t = bool(b1 and b1[4] and bt[1] > b1[4])
            gap_1 = bool(b2 and b2[4] and b1[1] > b2[4])
            th = (theme_at.get(t, {}).get(code)
                  or theme_at.get(t1, {}).get(code) or "")
            evs.append({
                "d": t, "d1": t1, "d0": t2, "code": code, "name": nm,
                "theme": th, "b": b, "s": shape(code, t, nm),
                "open": round((bt[1] / b1[2] - 1) * 100, 2),
                "chg": chg, "chg1": chg1,
                "gapT": 1 if gap_t else 0, "gap1": 1 if gap_1 else 0,
                "gap": 1 if (gap_t or gap_1) else 0,
            })

    evs.sort(key=lambda e: (-int(e["d"]), -e["b"], e["code"]))
    return {
        "eventsAll": evs,
        "windows": WINDOWS,
        "allRange": [all_start, end],
        "rule": ("赚钱效应统计口径：t-2 日买入、t-1 日换手连板赚钱；t-1 日买入、t 日换手连板赚钱"
                 "——即 t-1、t 连续两日均为换手涨停（非一字，买得进），且 t 日板位 ≥3 板。"
                 "「换手」＝收盘涨停且非一字（一字买不进，两日均排除）。"
                 "跳空缺口＝当日开盘价高于前一交易日最高价（真实向上跳空），有缺口更佳、单独亮标。"
                 "题材取当日复盘归类，当日缺失沿用昨日。统计口径为近 1 日（默认）/ 3 日 / 5 日，"
                 "窗口内同一个股按最近达标日归组去重。"),
    }
