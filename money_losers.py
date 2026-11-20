# -*- coding: utf-8 -*-
"""个股级「亏钱效应」模块（v55，与 money_winners 赚钱效应成对）。

用户口径（v55 定稿）：
  亏钱效应 = 前一日(涨停买入日👛)为「3 板及以上」的高位连板股，
             在亏钱日(💵/亏钱标记日)出现「买入即亏钱」的剧烈亏钱形态，并显示题材。
  买入成本 = 亏钱日「当日最高价」（用户定：按当日最高价算最惨亏损）。
  亏损率   = (当日收盘 / 当日最高 − 1) × 100（必为负/零）。

四种剧烈亏钱形态（可多标签叠加，prim 取优先级最高者）：
  天地板           —— 当日最高触及涨停、收盘跌停（涨停买入当天亏到跌停）；
  连续跌停         —— 当日跌停，且下一交易日仍跌停（买入后连续两日跌停）；
  断板次日跌停     —— 前一日≥3板涨停，当日断板且跌停收盘；
  冲高回落跌停大阴线 —— 当日明显冲高(最高涨幅≥5%)、收阴线且收盘大跌(≤−7%)。

数据源：涨停池 pools + 日K KBARS + 题材 theme_at。日K bar=[日期,开,收,低,高,量]。
历史日K缺失的区间优雅降级（跳过、不计入）。
"""
import bisect
from collections import defaultdict

PRI = ["天地板", "连续跌停", "断板次日跌停", "冲高回落跌停大阴线"]
WINDOWS = {"week": 5, "twoweek": 10, "month": 20}


def lim_of(code, name=""):
    if "ST" in (name or "").upper():
        return .05
    if str(code).startswith(("30", "688")):
        return .20
    if str(code).startswith(("8", "4", "92")):
        return .30
    return .10


def compute(dates, pools, KBARS, theme_at, NAME=None, ALL_N=140):
    NAME = NAME or {}
    CD = {c: sorted(r) for c, r in KBARS.items()}

    def dbar(c, d):
        b = (KBARS.get(str(c)) or {}).get(d)
        return b if (b and len(b) >= 6) else None

    def prev_close(code, d):
        ds = CD.get(str(code))
        if not ds:
            return None
        rec = KBARS.get(str(code)) or {}
        k = bisect.bisect_left(ds, d)
        for j in range(k - 1, -1, -1):
            b = rec.get(ds[j])
            if b and len(b) >= 5 and b[2]:
                return b[2]
        return None

    didx = {d: i for i, d in enumerate(dates)}
    pmap = {d: {s["code"]: s for s in pools[d]} for d in dates}
    end = dates[-1]
    all_start = dates[max(0, len(dates) - ALL_N)]

    raw = []
    for i, d in enumerate(dates):
        if d < all_start or d > end or i == 0:
            continue
        d0 = dates[i - 1]
        for code, s0 in pmap.get(d0, {}).items():
            b0 = s0.get("boards") or 1
            if b0 < 3:                      # 前一日须 3 板及以上
                continue
            tb = dbar(code, d)
            pv = prev_close(code, d)
            if not tb or not pv:            # 历史无日K → 优雅降级
                continue
            op, cl, lo, hi = tb[1], tb[2], tb[3], tb[4]
            if not cl or not hi or hi <= 0:
                continue
            lim = lim_of(code, s0["name"])
            up, dn = round(pv * (1 + lim), 2), round(pv * (1 - lim), 2)
            high_pct = round((hi / pv - 1) * 100, 2)
            open_pct = round((op / pv - 1) * 100, 2)
            chg = round((cl / pv - 1) * 100, 2)
            loss = round((cl / hi - 1) * 100, 2)   # 按当日最高价买入的收益（负=亏）
            if loss > -0.5:                 # 未剧烈亏钱（收盘≈最高）不算
                continue
            tags = []
            # 天地板：盘中触及涨停、收盘跌停
            if hi >= up - .011 and cl <= dn + .011:
                tags.append("天地板")
            # 跌停收盘（供断板/连续判定）
            is_dn = cl <= dn + .011
            # 连续跌停：下一交易日也跌停
            if is_dn and i + 1 < len(dates):
                nb = dbar(code, dates[i + 1])
                npv = cl
                if nb and npv and nb[2] <= round(npv * (1 - lim), 2) + .011:
                    tags.append("连续跌停")
            # 断板次日跌停：前一日涨停、当日不在涨停池且跌停收盘
            if is_dn and code not in pmap.get(d, {}):
                tags.append("断板次日跌停")
            # 冲高回落跌停大阴线：明显冲高 + 收阴 + 收盘大跌
            if high_pct >= 5 and cl < op and chg <= -7:
                tags.append("冲高回落跌停大阴线")
            if not tags:
                continue
            prim = next((p for p in PRI if p in tags), tags[0])
            th = (theme_at.get(d, {}).get(code)
                  or theme_at.get(d0, {}).get(code) or "")
            ev = {
                "d0": d0, "d": d, "code": code, "name": s0["name"],
                "theme": th, "b0": b0,
                "open": open_pct, "high": high_pct, "chg": chg,
                "loss": loss, "close": cl, "highpx": hi,
                "tags": tags, "prim": prim,
                "why": (s0.get("reason") or ""),
            }
            raw.append(ev)

    def agg(evs, n_label):
        total = len(evs)
        cats = []
        for p in PRI:
            arr = [e for e in evs if p in e["tags"]]
            if not arr:
                continue
            avg = round(sum(x["loss"] for x in arr) / len(arr), 2)
            srt = sorted(arr, key=lambda z: (z["loss"], -z["b0"], z["d"]))
            gm = {}
            for x in srt:
                gm.setdefault(x["theme"] or "其他", []).append(x)
            groups = []
            for t in sorted(gm, key=lambda k: (-max(z["b0"] for z in gm[k]), k)):
                ga = gm[t]
                groups.append({
                    "theme": t,
                    "maxB": max(z["b0"] for z in ga),
                    "stocks": [[z["name"], z["code"], z["d"], z["b0"], z["loss"]]
                               for z in ga[:12]],
                })
            cats.append({"k": p, "n": len(arr),
                         "pct": round(len(arr) / total * 100, 1) if total else 0,
                         "avg": avg, "groups": groups})
        cats.sort(key=lambda c: (-c["pct"], -c["n"], c["k"]))
        read = []
        if evs:
            read.append("最近 %d 个交易日共 %d 个「前一日≥3板买入 → 亏钱日剧烈亏钱」样本"
                        "（按当日最高价买入计亏，N/A 历史无日K日已降级跳过）。" % (n_label, total))
            if cats:
                top = max(cats, key=lambda x: x["n"])
                worst = min(cats, key=lambda x: x["avg"])
                read.append("出现最多：%s（%d 例 / %.1f%%）；平均亏损最深：%s（笔均 %.2f%%）。"
                            % (top["k"], top["n"], top["pct"], worst["k"], worst["avg"]))
        return total, cats, read

    start = dates[-10] if len(dates) >= 10 else dates[0]
    ev_default = [e for e in raw if e["d"] >= start]
    total, cats, read = agg(ev_default, 10)

    return {
        "range": [start, end], "n": 10, "total": total,
        "events": ev_default, "cats": cats, "read": read,
        "eventsAll": raw, "allRange": [all_start, end], "windows": WINDOWS,
        "rule": ("亏钱效应=前一日(买入日👛)3板及以上的高位涨停股，于亏钱日(💵标记)出现"
                 "买入即亏钱的剧烈回撤；买入成本按当日最高价，亏损率=收盘/最高−1。"
                 "四类：天地板(触涨停收跌停)、连续跌停(次日续跌停)、断板次日跌停、"
                 "冲高回落跌停大阴线(冲高≥5%+收阴+收盘≤−7%)；可多标签叠加，按题材展示。"
                 "历史无日K的日期优雅降级跳过。"),
    }
