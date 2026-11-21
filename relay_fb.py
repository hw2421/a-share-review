# -*- coding: utf-8 -*-
"""v69 昨日梯队 → 今日盈亏传导（逐股反馈）。

用户口径：
  「看上一交易日梯队的今日反馈」——按题材，把昨日梯队分层后逐股给出今日表现：
  昨日板位、今日涨幅、是否晋级；并给层统计（平均涨幅 / 赚钱率 / 晋级率）。

角色定义（用户定稿）：
  最高板 = 龙头；低于龙头的连板（≥2 板）= 助攻；首板（1 板）= 跟风。

数据契约：
  days = {展示日 d: [
     {"th": 题材, "top": 昨日最高板, "cnt": 昨日梯队只数,
      "L": [ {"b": 昨日板位, "role": "龙头|助攻|跟风",
              "cn": 有今日价格的只数, "avg": 平均涨幅, "win": 赚钱率%, "pr": 晋级只数,
              "st": [[股票名, 代码, 昨日板位, 今日涨幅(%), 晋级标记], ...]}, ... ] }
  ]}
  晋级标记：1=今日涨停且板位递增，0=未晋级，-1=今日无数据（停牌/退市）。
"""
import bisect

ALL_N = 90          # 覆盖最近 90 个交易日的展示日（够 1/3/5 日窗口 + 回测近端）


def _lim_pct(code, name):
    if name and "ST" in name:
        return 5.0
    if str(code).startswith(("30", "688")):
        return 20.0
    if str(code).startswith(("83", "87", "43", "92")):
        return 30.0
    return 10.0


def compute(dates, pools, KBARS, ladder_full, days_num=ALL_N, NAME=None):
    NAME = NAME or {}
    n = len(dates)
    if n < 2:
        return {"days": {}, "allRange": [], "rule": ""}, 0

    def bar(c, d):
        return (KBARS.get(str(c)) or {}).get(d)

    def close(c, d):
        b = bar(c, d)
        return b[2] if (b and len(b) >= 5 and b[2]) else None

    pmap = {d: {str(s["code"]): s for s in pools.get(d, [])} for d in dates}
    out = {}
    start = max(1, n - days_num)
    for i in range(start, n):
        d, dp = dates[i], dates[i - 1]
        prev_lf = ladder_full.get(dp) or {}
        day_rows = []
        for th, arr in prev_lf.items():
            if not arr:
                continue
            top = max((s.get("boards") or 1) for s in arr)
            layers = {}
            for s in arr:
                yb = s.get("boards") or 1
                role = "龙头" if yb == top else ("助攻" if yb >= 2 else "跟风")
                code = str(s.get("code") or "")
                chg = None
                cur = pmap.get(d, {}).get(code)
                pv = close(code, dp)
                cc = close(code, d)
                if pv and cc:
                    chg = round((cc / pv - 1) * 100, 2)
                if chg is None:
                    jb = -1
                else:
                    jb = 1 if (cur and (cur.get("boards") or 1) > yb) else 0
                layers.setdefault((yb, role), []).append(
                    [s.get("name") or NAME.get(code) or code, code, yb, chg, jb])
            L = []
            for (yb, role), st in sorted(layers.items(), key=lambda kv: -kv[0][0]):
                st.sort(key=lambda x: (x[4] != 1, -(x[3] if x[3] is not None else -99)))
                chgs = [x[3] for x in st if x[3] is not None]
                L.append({
                    "b": yb, "role": role, "cn": len(chgs),
                    "avg": round(sum(chgs) / len(chgs), 1) if chgs else None,
                    "win": round(sum(1 for x in chgs if x >= 0) / len(chgs) * 100) if chgs else None,
                    "pr": sum(1 for x in st if x[4] == 1),
                    "st": st,
                })
            day_rows.append({"th": th, "top": top, "cnt": len(arr), "L": L})
        if day_rows:
            day_rows.sort(key=lambda x: (-x["top"], -x["cnt"], x["th"]))
            out[d] = day_rows
    return {
        "days": out,
        "allRange": [dates[start], dates[-1]],
        "rule": ("昨日梯队今日反馈：昨日（t-1）各题材梯队逐股，给出今日（t）涨幅与是否晋级。"
                 "最高板＝龙头，低于龙头的连板＝助攻，首板＝跟风；"
                 "层平均涨幅＝该层个股今日涨跌幅均值，赚钱率＝涨幅≥0 占比，晋级＝今日板位高于昨日。"),
    }, sum(len(v) for v in out.values())
