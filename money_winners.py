# -*- coding: utf-8 -*-
"""v48 个股级「赚钱效应」模块（共享口径模块）。

用户口径（原话要点）：
  赚钱效应 = 昨日换手涨停股今天再次换手涨停。
  一字板当天买不进，故一字当天该股不统计为赚钱效应（昨日、今日都须换手涨停）。

赚钱效应的主要表现形式（用户列举，全部覆盖；另补一类最基础的「换手连板晋级」）：
  1 极致高开全天红 —— 昨日换手连板 + 今日高开 + 全天股价都在涨幅 0% 之上（今日最低 > 昨收）；
  2 断板反包再连板 —— 前期连板后断板，昨日反包涨停、今日再度涨停；
  3 地天板次日涨停 —— 昨日地天板，今日换手涨停；
  4 连续一字后首换手 —— 连续一字后首个换手板（昨日），今日再次换手涨停；
  5 N字反包次日涨停 —— 涨停→回调→昨日反包涨停，今日换手涨停；
  6 题材持续换手连板 —— 该股所属题材连续多日有换手连板股（题材维度持续性）；
  7 趋势股创新高周涨50% —— 今日创近 20 日新高且近 5 交易日涨幅 ≥50%；
  8 普通换手连板晋级 —— 昨日首板/低位换手板，今日换手晋级（最基础形式）。
规模维度：大盘 / 中盘 / 小盘（流通市值 = 成交额 ÷ 换手率 估算），回答「大盘股还是小盘股赚钱」。

数据源：涨停池 pools（含 reason/turnover/first_seal/open_num）+ 日K KBARS + 一字映射 yz + 题材映射。
返回结构由 compute() 生成，build_merged 写入 data.json，verify_space 独立复算同源。
"""
import bisect
from collections import Counter, defaultdict


def lim_of(code, name=""):
    if "ST" in (name or "").upper():
        return .05
    if str(code).startswith(("30", "688")):
        return .20
    if str(code).startswith(("8", "4", "92")):
        return .30
    return .10


def compute(dates, pools, yz, KBARS, theme_at, NAME=None, N=10):
    """返回赚钱效应模块数据 dict。N=统计最近 N 个交易日（默认 10≈两周）。"""
    NAME = NAME or {}
    CD = {c: sorted(r) for c, r in KBARS.items()}

    def dbar(c, d):
        b = (KBARS.get(str(c)) or {}).get(d)
        return b if (b and len(b) >= 6) else None

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

    def prev_close(code, d):
        pb = prev_bar(code, d)
        return pb[2] if pb else None

    def shape(code, d, name):
        """涨停形态六分类（与 build_merged._up_shape 完全同口径）。"""
        b, pv = dbar(code, d), prev_close(code, d)
        if not b or not pv:
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

    def yizi(code, d):
        return code in (yz.get(d) or ())

    pmap = {d: {s["code"]: s for s in pools[d]} for d in dates}
    didx = {d: i for i, d in enumerate(dates)}

    start = dates[-N]
    end = dates[-1]

    # 题材维度：近 5 交易日每日「有换手连板」的题材集合（题材持续换手连板用）
    def theme_active_set(d):
        active = set()
        for code, s in pmap[d].items():
            if (s.get("boards") or 1) >= 2 and not yizi(code, d) and shape(code, d, s["name"]):
                th = theme_at.get(d, {}).get(code) or ""
                if th:
                    active.add(th)
        return active

    events = []
    for i, d in enumerate(dates):
        if d < start or d > end:
            continue
        pd_ = dates[i - 1]
        if pd_ not in pmap:
            continue
        for code, s in pmap[pd_].items():
            cs = pmap[d].get(code)
            if not cs:
                continue
            # 昨日、今日都必须是换手涨停（非一字）
            if yizi(code, pd_) or yizi(code, d):
                continue
            s0 = shape(code, pd_, s["name"])
            s1 = shape(code, d, cs["name"])
            if not s0 or not s1:
                continue
            b0 = s.get("boards") or 1
            b1 = cs.get("boards") or 1
            tb, pv = dbar(code, d), prev_close(code, d)
            if not tb or not pv:
                continue
            open_pct = round((tb[1] / pv - 1) * 100, 2)
            low_pct = round((tb[3] / pv - 1) * 100, 2)
            chg = round((tb[2] / pv - 1) * 100, 2)
            # 换手率 & 流通市值估算（涨停池 turnover 是百分比；日K量为股数）
            to = s.get("turnover")
            capv = None
            if isinstance(to, (int, float)) and to > 0:
                amt = tb[5] * tb[2]                 # 估算成交额（元）
                capv = round(amt / (to / 100) / 1e8, 1)   # 流通市值（亿）
            if capv is None:
                cap = "未知"
            elif capv >= 200:
                cap = "大盘"
            elif capv >= 50:
                cap = "中盘"
            else:
                cap = "小盘"

            tags = []
            # 1 极致高开全天红
            if b0 >= 2 and tb[1] > pv and tb[3] > pv:
                tags.append("极致高开全天红")
            # 3 地天板次日涨停
            if s0 == "地天":
                tags.append("地天板次日涨停")
            # 4 连续一字后首换手：前一日（相对昨日）是一字
            ppd = dates[i - 2] if i >= 2 else None
            if ppd and yizi(code, ppd):
                tags.append("连续一字后首换手涨停")
            # 2 断板反包再连板：近 6 日内曾 ≥3 板、中间断板、昨日反包首板、今日再涨停
            had_high = False
            for k in range(max(0, i - 6), i):
                ss = pmap[dates[k]].get(code)
                if ss and (ss.get("boards") or 1) >= 3:
                    had_high = True
            if had_high and b0 <= 1 and b1 >= 2:
                tags.append("断板反包再连板")
            # 5 N字反包：昨日的前一日收阴下跌（回调），昨日反包涨停
            if ppd:
                pb2, pb3 = dbar(code, ppd), (dbar(code, dates[i - 3]) if i >= 3 else None)
                if pb2 and pb3 and pb2[2] < pb3[2] and pb2[2] < pb2[1]:
                    tags.append("N字反包次日涨停")
            # 7 趋势股创新高 + 近 5 交易日涨幅 ≥50%
            rb = [dbar(code, dates[k]) for k in range(max(0, i - 20), i + 1)]
            rb = [x for x in rb if x]
            if rb and tb[2] >= max(x[2] for x in rb) and i >= 5:
                b5 = dbar(code, dates[i - 5])
                if b5 and b5[2] and (tb[2] / b5[2] - 1) >= .5:
                    tags.append("趋势股创新高周涨50%")
            # 6 题材持续换手连板：近 5 交易日中 ≥3 日该股题材都在「换手连板活跃」集合
            th = theme_at.get(d, {}).get(code) or ""
            if th:
                act = sum(1 for k in range(max(0, i - 4), i + 1)
                          if th in theme_active_set(dates[k]))
                if act >= 3:
                    tags.append("题材持续换手连板")
            # 8 普通换手连板晋级（无任何特殊形态时的基础形式）
            if not tags:
                tags.append("普通换手连板晋级")

            events.append({
                "d0": pd_, "d": d, "code": code, "name": s["name"],
                "theme": th, "b0": b0, "b": b1, "s0": s0, "s1": s1,
                "tags": tags, "open": open_pct, "low": low_pct, "chg": chg,
                "turnover": to, "cap": cap, "capv": capv,
                "why": (s.get("reason") or ""),
            })

    # 主标签：优先级排序，取最高优先级一个（用于明细表单一分类展示）
    PRI = ["极致高开全天红", "地天板次日涨停", "连续一字后首换手涨停",
           "断板反包再连板", "N字反包次日涨停", "趋势股创新高周涨50%",
           "题材持续换手连板", "普通换手连板晋级"]
    for e in events:
        e["prim"] = next((p for p in PRI if p in e["tags"]), e["tags"][0])

    # 分类汇总（每类：笔数、占比、平均今日涨幅、代表个股）
    cats = []
    for p in PRI:
        arr = [e for e in events if p in e["tags"]]
        if not arr:
            continue
        avg = round(sum(x["chg"] for x in arr) / len(arr), 2)
        cats.append({
            "k": p, "n": len(arr), "avg": avg,
            "stocks": [[x["name"], x["code"], x["d"], x["b"], x["theme"]]
                       for x in sorted(arr, key=lambda z: (-z["b"], z["d"]))[:12]],
        })

    # 规模维度汇总
    caps = []
    for c in ("大盘", "中盘", "小盘", "未知"):
        arr = [e for e in events if e["cap"] == c]
        if not arr:
            continue
        caps.append({
            "k": c, "n": len(arr),
            "avg": round(sum(x["chg"] for x in arr) / len(arr), 2),
        })

    # 数据驱动的结论文字
    read = []
    if events:
        n = len(events)
        read.append("最近 %d 个交易日共 %d 个「昨日换手涨停→今日换手涨停」的赚钱效应样本；"
                    "一字板当日买不进，昨日与今日均排除一字。" % (N, n))
        if cats:
            top = max(cats, key=lambda x: x["n"])
            best = max(cats, key=lambda x: x["avg"])
            read.append("出现最多的形式：%s（%d 例）；今日溢价最高：%s（笔均 %+.2f%%）。"
                        % (top["k"], top["n"], best["k"], best["avg"]))
        if caps:
            known = [c for c in caps if c["k"] != "未知"]
            if known:
                bc = max(known, key=lambda x: x["avg"])
                mc = max(known, key=lambda x: x["n"])
                read.append("规模维度：%s股数量最多（%d 例），%s股今日溢价最高（笔均 %+.2f%%）；"
                            "流通市值按「成交额÷换手率」估算，仅作规模分组参考。"
                            % (mc["k"], mc["n"], bc["k"], bc["avg"]))

    return {
        "range": [start, end], "n": N, "total": len(events),
        "events": events, "cats": cats, "caps": caps, "read": read,
        "rule": ("赚钱效应=昨日换手涨停股今日再次换手涨停；一字板当日买不进，"
                 "昨日/今日均排除一字。流通市值=成交额÷换手率估算，仅用于大小盘分组。"),
    }
