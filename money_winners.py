# -*- coding: utf-8 -*-
"""个股级「赚钱效应」模块（共享口径模块，v50 支持前端动态窗口）。

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
  9 强势股 —— 近10交易日涨停≥5 次 且 每日收盘价在5日均线之上 且 近3日至少创一次(20日)新高。
            （叠加标签：可与上述形态共存，不参与 prim 主模式抢占）
规模维度：大盘 / 中盘 / 小盘（流通市值 = 成交额 ÷ 换手率 估算），回答「大盘股还是小盘股赚钱」。

v49 口径收紧：单纯「首板连板」的普通晋级不算赚钱效应，除非该股近 10 交易日
涨停 ≥5 次（活跃妖股）或带 N 字涨停形态。

v50 数据契约：一次生成覆盖最近 ALL_N(≈120) 个交易日的全量事件池 eventsAll
（已按 v49 过滤），连同被剔除事件 droppedEvents；前端按「结束日期 + 窗口
（一周5 / 两周10 / 一个月20 交易日）」从事件池动态切分并重算分类(按占比降序)、
规模、结论与明细。默认两周快照 events/cats/caps/read/total 保留供兼容与 verify 复算。

数据源：涨停池 pools（含 reason/turnover/first_seal/open_num）+ 日K KBARS + 一字映射 yz + 题材映射。
"""
import bisect
from collections import Counter, defaultdict

PRI = ["极致高开全天红", "地天板次日涨停", "连续一字后首换手涨停",
       "断板反包再连板", "N字反包次日涨停", "趋势股创新高周涨50%",
       "题材持续换手连板", "普通换手连板晋级", "强势股"]
# 主模式候选（强势股为叠加标签，不参与 prim 主模式抢占）
PRI_PRIM = [p for p in PRI if p != "强势股"]
WINDOWS = {"week": 5, "twoweek": 10, "month": 20}


def lim_of(code, name=""):
    if "ST" in (name or "").upper():
        return .05
    if str(code).startswith(("30", "688")):
        return .20
    if str(code).startswith(("8", "4", "92")):
        return .30
    return .10


def compute(dates, pools, yz, KBARS, theme_at, ladders=None, NAME=None,
            N=10, ALL_N=140):
    """返回赚钱效应模块数据 dict。

    N   = 默认两周快照窗口（10 交易日）；ALL_N = 全量事件池覆盖的交易日数。
    eventsAll 覆盖 dates[-ALL_N:]，前端按结束日期+窗口动态切分。
    """
    NAME = NAME or {}
    ladders = ladders or {}
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

    def zt_count_10(code, d):
        """截止今日 d 的近 10 个交易日（含当日）内，该股出现在涨停池的天数。"""
        i = didx.get(d)
        if i is None:
            return 0
        return sum(1 for k in range(max(0, i - 9), i + 1)
                   if code in pmap.get(dates[k], ()))

    def strong_stock(code, d):
        """v53 强势股叠加标签：
           ① 近 10 交易日涨停 ≥5 次；
           ② 近 5 个交易日「每日收盘价在 5 日均线之上」（可计算 MA5 的交易日全部满足，至少 3 天可核验）；
           ③ 近 3 个交易日至少一次创近 20 日收盘新高。
           日K不足（如 2017-2018 历史无日K）返回 False，优雅降级。
           K线 bar 结构：[日期, 开, 收, 低, 高, 量]，收盘 = b[2]。
        """
        if zt_count_10(code, d) < 5:
            return False
        ds = CD.get(str(code))
        if not ds:
            return False
        k = bisect.bisect_left(ds, d)
        if k < 0 or k >= len(ds):
            return False
        rec = KBARS.get(str(code)) or {}
        # ② 近 5 交易日每日收盘 > 当日 MA5
        checked = 0
        for j in range(max(4, k - 4), k + 1):
            closes = []
            for t in range(j - 4, j + 1):
                bt = rec.get(ds[t])
                if not bt or len(bt) < 5 or not bt[2]:
                    closes = []
                    break
                closes.append(bt[2])
            if len(closes) < 5:
                continue
            bj = rec.get(ds[j])
            if not bj or len(bj) < 5:
                continue
            if bj[2] <= sum(closes) / 5:
                return False
            checked += 1
        if checked < 3:
            return False
        # ③ 近 3 交易日至少一次创近 20 日收盘新高
        for j in range(max(1, k - 2), k + 1):
            bj = rec.get(ds[j])
            if not bj or len(bj) < 5:
                continue
            prior = [rec[ds[t]][2] for t in range(max(0, j - 20), j)
                     if rec.get(ds[t]) and len(rec[ds[t]]) >= 5 and rec[ds[t]][2]]
            if prior and bj[2] >= max(prior):
                return True
        return False

    end = dates[-1]
    all_start = dates[max(0, len(dates) - ALL_N)]
    # 主题材活跃集合：按当日判断，缓存避免重复扫描
    _taset = {}

    def theme_active_set(d):
        if d in _taset:
            return _taset[d]
        active = set()
        for code, s in pmap[d].items():
            if (s.get("boards") or 1) >= 2 and not yizi(code, d) and shape(code, d, s["name"]):
                th = theme_at.get(d, {}).get(code) or ""
                if th:
                    active.add(th)
        _taset[d] = active
        return active

    raw = []
    for i, d in enumerate(dates):
        if d < all_start or d > end:
            continue
        if i == 0:
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
            to = s.get("turnover")
            capv = None
            if isinstance(to, (int, float)) and to > 0:
                amt = tb[5] * tb[2]
                capv = round(amt / (to / 100) / 1e8, 1)
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
            # 4 连续一字后首换手
            ppd = dates[i - 2] if i >= 2 else None
            if ppd and yizi(code, ppd):
                tags.append("连续一字后首换手涨停")
            # 2 断板反包再连板
            had_high = False
            for k in range(max(0, i - 6), i):
                ss = pmap[dates[k]].get(code)
                if ss and (ss.get("boards") or 1) >= 3:
                    had_high = True
            if had_high and b0 <= 1 and b1 >= 2:
                tags.append("断板反包再连板")
            # 5 N字反包次日涨停
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
            # 题材（v47 口径）：当日抓到优先，否则沿用昨日复盘归类
            th = (theme_at.get(d, {}).get(code)
                  or theme_at.get(pd_, {}).get(code) or "")
            if th:
                act = sum(1 for k in range(max(0, i - 4), i + 1)
                          if th in theme_active_set(dates[k]))
                if act >= 3:
                    tags.append("题材持续换手连板")
            if not tags:
                tags.append("普通换手连板晋级")
            # v53 强势股叠加标签（不影响 prim 主模式）
            if strong_stock(code, d):
                tags.append("强势股")

            ev = {
                "d0": pd_, "d": d, "code": code, "name": s["name"],
                "theme": th, "b0": b0, "b": b1, "s0": s0, "s1": s1,
                "tags": tags, "open": open_pct, "low": low_pct, "chg": chg,
                "turnover": to, "cap": cap, "capv": capv,
                "why": (s.get("reason") or ""),
                "zt10": zt_count_10(code, d),
            }
            ev["prim"] = next((p for p in PRI_PRIM if p in tags), tags[0])
            raw.append(ev)

    # v55 口径收紧（用户定稿）：
    #   ① 板位门槛：昨日（买入日）板位 b0>=3 才计为赚钱效应股；
    #   ② 赚钱效应日（今日 d）三条件须同时满足：
    #      高开 = 开盘价 > 昨收（open_pct>0）；
    #      全天无下 0% 线 = 全天最低价 >= 昨收（low_pct>=0）；
    #      炸板最后回封 = 收盘价达到涨停（由 shape(s1) 非空保证，见上方 s1 校验）。
    #   例外：N字反包次日涨停 / 断板反包再连板 —— 不受板位与高开/全天红门槛限制，
    #        但仍须「昨日、今日均换手涨停且收盘涨停」（s0/s1 非空、非一字，已保证）。
    def qualifies_v55(e):
        # 赚钱效应日三条件对所有样本均为硬门槛：
        if e["open"] <= 0:          # 高开：开盘 > 昨收
            return False
        if e["low"] < 0:            # 全天最低价不得低于昨收（0% 线）
            return False
        # 收盘涨停由 shape(s1) 保证（raw 阶段已过滤）
        tags = e["tags"]
        # 板位门槛：昨日≥3板；N字反包/断板反包仅豁免板位，不豁免上述三条件
        if ("N字反包次日涨停" in tags) or ("断板反包再连板" in tags):
            return True
        return e["b0"] >= 3

    events_all = []
    dropped_all = []
    for e in raw:
        if qualifies_v55(e):
            events_all.append(e)
        else:
            why = []
            if e["b0"] < 3:
                why.append("昨日%d板<3" % e["b0"])
            if e["open"] <= 0:
                why.append("非高开%.2f%%" % e["open"])
            if e["low"] < 0:
                why.append("下破0%%线%.2f%%" % e["low"])
            dropped_all.append({"d": e["d"], "code": e["code"],
                                "name": e["name"], "why": "、".join(why) or "不满足赚钱效应日条件"})

    def th_strength(d, th):
        g = (ladders.get(d) or {}).get(th) or {}
        return (g.get("maxB") or 0, g.get("count") or 0)

    def summary(evs, n_label, dropped_n):
        """由一组事件生成 cats(按占比降序)/caps/read。"""
        total = len(evs)
        cats = []
        for p in PRI:
            arr = [e for e in evs if p in e["tags"]]
            if not arr:
                continue
            avg = round(sum(x["chg"] for x in arr) / len(arr), 2)
            srt = sorted(arr, key=lambda z: (-th_strength(z["d"], z["theme"])[0],
                                             -th_strength(z["d"], z["theme"])[1],
                                             -z["b"], z["d"]))
            cats.append({
                "k": p, "n": len(arr),
                "pct": round(len(arr) / total * 100, 1) if total else 0,
                "avg": avg,
                "stocks": [[x["name"], x["code"], x["d"], x["b"], x["theme"]]
                           for x in srt[:12]],
            })
        # v50：分类按占比降序
        cats.sort(key=lambda c: (-c["pct"], -c["n"], c["k"]))
        caps = []
        for c in ("大盘", "中盘", "小盘", "未知"):
            arr = [e for e in evs if e["cap"] == c]
            if not arr:
                continue
            caps.append({"k": c, "n": len(arr),
                         "avg": round(sum(x["chg"] for x in arr) / len(arr), 2)})
        read = []
        if evs:
            read.append("最近 %d 个交易日共 %d 个赚钱效应样本（前一日买入日≥3板 + 赚钱效应日高开、"
                        "全天不下0%%线、炸板回封收涨停；N字/断板反包例外）；已按新口径剔除 %d 例。"
                        "一字板当日买不进，昨日与今日均排除一字。"
                        % (n_label, total, dropped_n))
            if cats:
                top = max(cats, key=lambda x: x["n"])
                best = max(cats, key=lambda x: x["avg"])
                read.append("出现最多的形式：%s（%d 例，占比 %.1f%%）；今日溢价最高：%s（笔均 %+.2f%%）。"
                            % (top["k"], top["n"], top["pct"], best["k"], best["avg"]))
            if caps:
                known = [c for c in caps if c["k"] != "未知"]
                if known:
                    bc = max(known, key=lambda x: x["avg"])
                    mc = max(known, key=lambda x: x["n"])
                    read.append("规模维度：%s股数量最多（%d 例），%s股今日溢价最高（笔均 %+.2f%%）；"
                                "流通市值按「成交额÷换手率」估算，仅作规模分组参考。"
                                % (mc["k"], mc["n"], bc["k"], bc["avg"]))
        return total, cats, caps, read

    # 默认两周快照（兼容旧字段与 verify）
    start = dates[-N]
    ev_default = [e for e in events_all if e["d"] >= start]
    drop_default = sum(1 for e in dropped_all if e["d"] >= start)
    total, cats, caps, read = summary(ev_default, N, drop_default)

    return {
        "range": [start, end], "n": N, "total": total,
        "events": ev_default, "cats": cats, "caps": caps, "read": read,
        "droppedPlain": drop_default,
        # v50：全量事件池 + 前端动态窗口参数
        "eventsAll": events_all,
        "droppedEvents": dropped_all,
        "allRange": [all_start, end],
        "windows": WINDOWS,
        "rule": ("赚钱效应=前一日(买入日👛)板位≥3 的换手涨停股，今日(赚钱效应日💵)高开、"
                 "全天最低价不低于昨收(不下0%线)、炸板后回封收于涨停；N字反包/断板反包为例外"
                 "(不受板位与高开/全天红门槛限制，但仍须两日换手涨停)。一字买不进，两日均排一字。"
                 "题材当日抓不到则沿用昨日复盘；市值=成交额÷换手率估算仅作规模分组；"
                 "可按结束日+一周/两周/一月动态切分，分类按占比降序、同类按题材。"),
    }
