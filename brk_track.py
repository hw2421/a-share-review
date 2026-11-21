# -*- coding: utf-8 -*-
"""v67 高位断板跟踪（3日）—— 连板梯队页当日梯队最下一行。

用户定稿口径（原话要点）：
  每日题材梯队：如果有「昨日 3 板或以上」个股在统计日断板，
  在当日梯队最下一行显示其涨跌幅，并跟踪显示 3 日（断板日开始统计）。

判定：
  断板 = 前一交易日板位 ≥3 的个股，统计日不在涨停池（未涨停）。
  跟踪 3 日 = 断板日、断板+1、断板+2（按交易日，共 3 天），
  每天记录：当日涨跌幅（vs 前收）+ 断板以来累计涨跌幅（vs 断板日前收）。

数据契约：days = {展示日: [{code, name, b(断板前板位), bd(断板日),
n(断板日起第几日 0/1/2), chg(当日涨跌幅), cum(累计涨跌幅), th(所属题材)}]}。
前端在「题材炒作」页对应题材的当日梯队最下一行渲染（v68 从连板梯队页移入题材页）。

日K bar 结构：[日期, 开, 收, 低, 高, 量]（收盘=b[2]）。
"""
import bisect

TRACK_DAYS = 3


def compute(dates, pools, KBARS, NAME=None, days_num=TRACK_DAYS, THEME=None, CARRY=None):
    NAME = NAME or {}
    THEME = THEME or {}          # 日期 -> {code: 题材}（断板前一日口径）
    CARRY = CARRY or {}          # code -> 最近一次题材（兜底）
    CD = {c: sorted(r) for c, r in KBARS.items()}

    def theme_of(code, d):
        """断板股所属题材：优先取「断板前一日（仍 ≥3 板时）」的复盘题材归类，
        缺失时回退到该股最近一次题材。"""
        t = (THEME.get(d) or {}).get(str(code))
        return t or CARRY.get(str(code)) or ""

    def close_of(c, d):
        b = (KBARS.get(str(c)) or {}).get(d)
        return b[2] if (b and len(b) >= 5 and b[2]) else None

    pmap = {d: {s["code"] for s in pools[d]} for d in dates}
    smap = {d: {s["code"]: s for s in pools[d]} for d in dates}
    n = len(dates)
    out = {}
    n_ev = 0
    for i in range(1, n):
        p, d0 = dates[i - 1], dates[i]
        # 昨日 ≥3 板个股今日断板（不在涨停池）
        for s in pools.get(p, []):
            b0 = s.get("boards") or 1
            if b0 < 3:
                continue
            code = s["code"]
            cur = smap.get(d0, {}).get(code)
            # 断板 = 今日未涨停；保险起见「今日在池但板位未晋级」也视为断板
            if cur and (cur.get("boards") or 1) > b0:
                continue
            pc = close_of(code, p)          # 断板日前收（累计涨跌幅基准）
            if pc is None:
                continue                    # 历史无日K，优雅降级
            for k in range(days_num):
                j = i + k
                if j >= n:
                    break
                dj = dates[j]
                cc = close_of(code, dj)
                if cc is None:
                    continue
                pcd = close_of(code, dates[j - 1])
                out.setdefault(dj, []).append({
                    "code": code,
                    "name": s.get("name") or NAME.get(code) or code,
                    "b": b0, "bd": d0, "n": k,
                    "chg": (round((cc / pcd - 1) * 100, 2) if pcd else None),
                    "cum": round((cc / pc - 1) * 100, 2),
                    "th": theme_of(code, p),
                })
                n_ev += 1
    return {
        "days": out,
        "rule": ("高位断板跟踪：昨日 ≥3 板个股统计日断板（未涨停）→ 题材炒作页该题材当日梯队"
                 "最下一行显示其涨跌幅，自断板日起跟踪 3 个交易日（断板日、+1、+2）。"
                 "当日%＝收盘/昨收−1；累计%＝收盘/断板日前收−1；题材取断板前一日的复盘归类。"),
    }, n_ev
