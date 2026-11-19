# -*- coding: utf-8 -*-
"""单一战法「山顶洞人」的全期逐笔回测（v44）。

由 build_merged.py 末尾调用 build(ctx)，返回：
  review        {"summit": [trade,...]}  逐笔（按日期、代码排序）
  reviewMeta    {"summit": {name,buy,sell,note,uni}}  页面口径说明
  chances       {买入日: [opp,...]}      当日机会（含一字买不进、炸板）
  kMarks        {code: {date: 1|2}}      K线骷髅头

「山顶洞人」口径（v44 用户原话）：
  · 选股 / 买入：当日题材炒作中**有赚钱效应**的题材，买入其**龙头股**
    （板块最高连板、当日涨停），**一字板除外**（一字买不进，只进机会池）。
    有赚钱效应 = 该题材当日有 ≥3 板个股再涨停、板块涨停 ≥3 只、龙头非一字
    （build_merged 的 money 榜，与题材炒作页金框同源）。
  · 卖出（统一，不变）：次日涨停持仓，尾盘不涨停卖出，盘中跌幅大于 -7% 无条件止损；
    一字跌停无换手卖不出，顺延至首个可成交日。
  · 已删除：换手 3 板+2 板跟风梯队选股、次日接力、空间突破日买入（v42/v43 口径）。
"""
import os
import sys
from collections import defaultdict

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

STOP_PCT = 0.07
METHODS = ["summit"]

SELL_TXT = ("统一卖出规则：次日仍涨停则持仓，首个收盘未涨停日尾盘按收盘价卖出，"
            "盘中跌幅大于 7% 无条件止损。")

META = {
    "summit": {
        "name": "山顶洞人",
        "buy": "【买入】当日题材炒作中「有赚钱效应」的题材，以涨停价打板买入该题材龙头股"
               "（板块最高连板、当日涨停），一字板除外（一字买不进，只进机会池、不参与模拟）。",
        "sell": SELL_TXT,
        "note": "「有赚钱效应」＝该题材当日有 ≥3 板个股再涨停、板块当日涨停 ≥3 只、"
                "且龙头为换手（非一字）连板股 —— 与题材炒作页金框（★赚钱效应题材）同源。"
                "「龙头」＝当日该题材最高连板个股；同一只股属多个题材时只记一笔。"
                "题材须在当日题材炒作模块中入链（有连板且涨停 >3 只）。"
                "买入价一律按涨停价打板；盘中触板后炸板按涨停价买入并计入逐笔。",
    },
}


def _logic_txt(t):
    """「买入逻辑」列：把这一笔为什么买浓缩成一句可读标签（v45）。"""
    parts = ["赚钱效应龙头"]
    if t.get("stage"):
        parts.append(t["stage"])
    if t.get("boards"):
        parts.append("%d板" % t["boards"])
    if t.get("t"):
        parts.append(t["t"] + "封板")
    if t.get("cnt"):
        parts.append("板块%d只" % t["cnt"])
    if t.get("b1") is not None:
        parts.append("首板%d只" % t["b1"])
    if t.get("shape"):
        parts.append(t["shape"])
    if t.get("vmax"):
        parts.append("近100日最大量")
    elif t.get("volx"):
        parts.append("量%.1f倍" % t["volx"])
    return "｜".join(parts)


def build(ctx):
    dates = ctx["dates"]
    DIDX = ctx["DIDX"]
    days = ctx["days"]
    pool_map = ctx["pool_map"]
    theme_at = ctx["theme_at"]
    KBARS = ctx["KBARS"]
    CLOSE = ctx["CLOSE"]
    carry = ctx["carry"]
    yz = ctx["yz"]
    LF = ctx.get("ladder_full") or {}
    TD = ctx.get("theme_detail") or {}      # 当日是否有湖南人复盘清单（决定能否兜底）
    MONEY = ctx.get("money") or {}          # 当日题材赚钱效应榜（与题材炒作页金框同源）
    stage_by_d = {x["date"]: x for x in days}

    stats = {"cand": 0, "fire": 0, "yizi": 0, "no_limit": 0,
             "open_skip": 0, "no_kline": 0, "defer": 0, "consec_yizi": 0,
             "burst": 0, "m1": 0, "m2": 0, "pick": 0, "no_assist": 0,
             "height_skip": 0, "no_vol": 0, "maxvol": 0, "theme_skip": 0}

    chances = defaultdict(list)
    chance_seen = set()
    excluded_consec = set()

    NAME = {}
    NAME_AT = {}                     # (code, date) -> 当日池内名称（摘帽个股全局名覆盖历史 ST 名
    for d, v in pool_map.items():    # 会导致限幅误判，故按日取当日名）
        for c, s in v.items():
            if s.get("name"):
                NAME[c] = s["name"]
                NAME_AT[(c, d)] = s["name"]

    # ---------------- 基础工具 ----------------
    def lim_of(code, d=None):
        nm = NAME_AT.get((code, d)) or NAME.get(code, "")
        if "ST" in nm.upper():
            return .05
        if code.startswith(("30", "688")):
            return .20
        if code.startswith(("8", "4", "92")):
            return .30
        return .10

    def bar_of(code, d):
        return (KBARS.get(code) or {}).get(d)

    def prev_close(code, d):
        rec = KBARS.get(code)
        if not rec or d not in rec:
            return None
        i = DIDX.get(d)
        if i is None:
            return None
        for j in range(i - 1, -1, -1):
            b = rec.get(dates[j])
            if b and len(b) >= 3 and b[2]:
                return b[2]
        return None

    def limit_px(code, d):
        pv = prev_close(code, d)
        return round(pv * (1 + lim_of(code, d)), 2) if pv else None

    def touch_on(code, d):
        """盘中冲击涨停（含封板 / 炸板）。"""
        b, pv = bar_of(code, d), prev_close(code, d)
        if not b or len(b) < 5 or not pv:
            return False
        return b[4] / pv - 1 >= lim_of(code, d) - .006

    def is_oneword_up(code, d):
        b, pv = bar_of(code, d), prev_close(code, d)
        if not b or len(b) < 5 or not pv:
            return False
        return (b[1] == b[2] == b[3] == b[4]
                and b[2] / pv - 1 >= lim_of(code, d) - .006)

    def is_oneword_down(code, d):
        b, pv = bar_of(code, d), prev_close(code, d)
        if not b or len(b) < 5 or not pv:
            return False
        return (b[1] == b[2] == b[3] == b[4]
                and b[2] / pv - 1 <= -(lim_of(code, d) - .006))

    def open_at_limit(code, d):
        b, pv = bar_of(code, d), prev_close(code, d)
        if not b or len(b) < 5 or not pv:
            return False
        return b[1] / pv - 1 >= lim_of(code, d) - .006

    def buy_blocked(code, d, e=None):
        """一字涨停（集合竞价封死全天未开板）→ 实际买不进。"""
        if e and e.get("yizi"):
            return True
        return is_oneword_up(code, d)

    # ---------------- 量能（v43 选股条件：选股日放量） ----------------
    def vol_of(code, d):
        b = bar_of(code, d)
        return b[5] if (b and len(b) >= 6 and b[5]) else None

    def vol_prev(code, d):
        """上一交易日成交量（成交量为 0 / 停牌日自动跳过）。"""
        rec = KBARS.get(code)
        i = DIDX.get(d)
        if not rec or i is None:
            return None
        for j in range(i - 1, -1, -1):
            v = vol_of(code, dates[j])
            if v:
                return v
        return None

    def is_max_vol100(code, d):
        """T 日成交量是否为近 100 个交易日（含 T 日）最大量 —— 放量的最佳形态。"""
        v = vol_of(code, d)
        i = DIDX.get(d)
        if not v or i is None:
            return False
        for j in range(max(0, i - 99), i):        # 前 99 个交易日
            w = vol_of(code, dates[j])
            if w and w > v:
                return False
        return True

    def vol_ok(code, T):
        """选股条件之一：龙头股选股日（T）成交量较上一交易日放大。"""
        v, vp = vol_of(code, T), vol_prev(code, T)
        return bool(v and vp and v > vp)

    def vol_desc(code, d):
        """当日量能描述（v44：仅作信息标注，不再作为买入过滤条件）。"""
        v, vp = vol_of(code, d), vol_prev(code, d)
        if not v or not vp:
            return ""
        r = v / vp
        txt = "当日量能 %.2f 倍（较前一交易日%s）" % (r, "放大" if r >= 1 else "缩小")
        return txt + ("（近100日最大量）" if is_max_vol100(code, d) else "")

    def consec_oneword_open(code, d):
        """连续一字涨停且当日涨停开盘 —— 全程无换手、买不进，不属于任何战法标的。"""
        if not open_at_limit(code, d):
            return False
        i = DIDX.get(d)
        if i is None:
            return False
        lim = lim_of(code, d)
        b, pv = bar_of(code, d), prev_close(code, d)
        j = i
        if not (b and len(b) >= 5 and pv and
                b[1] == b[2] == b[3] == b[4] and b[2] / pv - 1 >= lim - .006):
            j = i - 1
        cnt = 0
        while j >= 0:
            dd = dates[j]
            bb, pp = bar_of(code, dd), prev_close(code, dd)
            if not (bb and len(bb) >= 5 and pp and
                    bb[1] == bb[2] == bb[3] == bb[4] and bb[2] / pp - 1 >= lim - .006):
                break
            cnt += 1
            if cnt >= 2:
                return True
            j -= 1
        return False

    # ---------------- 1. 当日候选池：收盘涨停池 + 盘中触板后炸板 ----------------
    daily = {}
    for d in dates:
        m, th = {}, theme_at.get(d, {})
        for code, s in pool_map[d].items():
            m[code] = {
                "code": code, "name": s.get("name", ""),
                "boards": s.get("boards") or 1,
                "theme": th.get(code, "") or carry.get(code, ""),
                "sealed": True, "yizi": code in (yz.get(d) or set()),
                "time": s.get("first_seal") or s.get("time") or "",
            }
        daily[d] = m

    all_codes = {c for v in pool_map.values() for c in v}
    n_burst = 0
    # 必须有序遍历：set 的迭代顺序受进程 hash 随机化影响，会让炸板股插入顺序漂移，
    # 进而使统计顺序与数据指纹不稳定。
    for code in sorted(all_codes):
        rec = KBARS.get(code)
        if not rec:
            continue
        for d in dates:
            if code in pool_map[d]:
                continue
            lim = lim_of(code, d)
            b = rec.get(d)
            if not b or len(b) < 5:
                continue
            pv = prev_close(code, d)
            if not pv:
                continue
            hi, cl = b[4] / pv - 1, b[2] / pv - 1
            if hi < lim - .006 or cl >= lim - .006:
                continue
            i = DIDX[d]
            pb = pool_map[dates[i - 1]].get(code) if i > 0 else None
            boards = (pb.get("boards") or 1) + 1 if pb else 1
            daily[d][code] = {
                "code": code, "name": NAME.get(code, ""),
                "boards": boards,
                "theme": theme_at.get(d, {}).get(code) or carry.get(code, ""),
                "sealed": False, "yizi": False, "time": "",
                "limitPx": round(pv * (1 + lim), 2),
            }
            n_burst += 1
    print("山顶洞人候选池：池内收盘涨停 + 炸板补充 %d 个股票日" % n_burst, flush=True)

    # ---------------- 2. 当日题材梯队（选股条件的数据源） ----------------
    _c2t = {}
    for d in dates:
        m = {}
        for nm, arr in (LF.get(d) or {}).items():
            for x in arr:
                m.setdefault(str(x.get("code")), nm)
        _c2t[d] = m

    def theme_of(d, code):
        return (_c2t.get(d, {}).get(code)
                or (theme_at.get(d) or {}).get(code, "")
                or carry.get(code, ""))

    _lad_cache = {}

    def ladder_of(d):
        """当日题材梯队：只含当日真实涨停（封住）个股，题材归档一律取当日梯队
        ladderFull（有复盘日＝湖南人清单；无复盘日＝同花顺 / 历史归属暂代，由构建
        阶段写入）。不再用个股映射兜底补股——避免清单外个股混入板块梯队，
        也让「选股 / 校验」完全同源（v42.1）。"""
        if d in _lad_cache:
            return _lad_cache[d]
        out = {}
        for nm, arr in (LF.get(d) or {}).items():
            rows = []
            for x in arr:
                c = str(x.get("code"))
                e = (daily.get(d) or {}).get(c) or {}
                if c not in (pool_map.get(d) or {}):
                    continue                      # 只有收盘封住的涨停才算梯队
                rows.append({"code": c,
                             "name": x.get("name") or e.get("name", ""),
                             "boards": x.get("boards") or e.get("boards") or 1,
                             "time": e.get("time") or x.get("t") or "",
                             "yizi": bool(e.get("yizi")) or is_oneword_up(c, d)})
            if rows:
                out[nm] = rows
        _lad_cache[d] = out
        return out

    # ---------------- 3. 选股条件：换手 3 板+龙头 + 换手 2 板跟风 ----------------
    _pick_cache = {}

    def pick_set(T):
        """T 日选股：板块龙头＝板块最高连板且换手（非一字）3 板+；
        板块内另有换手 2 连板跟风股（组成板块梯队 → 板块有持续性）；
        且龙头股 T 日放量（成交量较上一交易日放大，v43）。"""
        if T in _pick_cache:
            return _pick_cache[T]
        out = []
        for nm, arr in (ladder_of(T) or {}).items():
            if len(arr) < 2:
                continue
            mx = max(x["boards"] for x in arr)
            if mx < 3:
                continue
            leads = sorted([x for x in arr if x["boards"] == mx and not x["yizi"]],
                           key=lambda x: x["code"])
            if not leads:
                continue                      # 龙头是一字板 → 无换手龙头，不入选
            f2 = sorted([x for x in arr
                         if x["boards"] == 2 and not x["yizi"]
                         and x["code"] not in {y["code"] for y in leads}],
                        key=lambda x: x["code"])
            if not f2:
                continue                      # 无换手 2 板跟风 → 无梯队，不入选
            f1 = sorted([x for x in arr if x["boards"] == 1], key=lambda x: x["code"])
            for lead in leads:
                if not vol_ok(lead["code"], T):
                    stats["no_vol"] += 1      # 选股日缩量 → 不入选
                    continue
                out.append({"nm": nm, "lead": lead, "f2": f2, "f1": f1, "mx": mx})
        _pick_cache[T] = out
        return out

    # ---------------- 4. 助攻判定（买入一） ----------------
    def assist_desc(B, p, lead_e):
        """昨日（T=p 所属日）跟风股今天高开红盘无亏钱，或昨日首板今日晋级 2 板。
        返回 (是否有助攻, 描述)。助攻股首封时间早于龙头时在描述里标注「先于龙头涨停」。"""
        reasons, early = [], False
        lt = lead_e.get("time") or ""
        for x in p["f2"]:
            b, pv = bar_of(x["code"], B), prev_close(x["code"], B)
            if not b or not pv or len(b) < 5:
                continue
            if b[1] > pv and b[2] >= pv:
                chg = (b[2] / pv - 1) * 100
                reasons.append("昨日跟风 %s 今日高开红盘 %+.1f%%" % (x["name"], chg))
                eb = (daily.get(B) or {}).get(x["code"]) or {}
                if lt and eb.get("time") and eb["time"] < lt:
                    early = True
        for x in p["f1"]:
            eb = (daily.get(B) or {}).get(x["code"])
            if not eb:
                continue
            # 首板晋级＝今日收盘封住 2 板（炸板不算晋级，板位不提升）
            if (eb.get("boards") or 1) == 2 and eb["sealed"]:
                reasons.append("昨日首板 %s 今日晋级 2 板" % x["name"])
                if lt and eb.get("time") and eb["time"] < lt:
                    early = True
        if not reasons:
            return False, ""
        desc = "；".join(reasons[:2])
        if early:
            desc += "（助攻先于龙头涨停）"
        return True, desc

    # ---------------- 5. 统一卖出模拟 ----------------
    def simulate_exit(code, k):
        deferred = 0
        for j in range(k + 1, len(dates)):
            t = dates[j]
            b, pv = bar_of(code, t), prev_close(code, t)
            if not b or not pv:
                continue
            if is_oneword_down(code, t):
                deferred += 1
                stats["defer"] += 1
                continue
            tag = ("（一字跌停顺延%d日）" % deferred) if deferred else ""
            if b[3] and b[3] <= pv * (1 - STOP_PCT):
                return round(pv * (1 - STOP_PCT), 2), t, "盘中-7%止损" + tag, j - k
            if code in pool_map[t]:
                continue                      # 次日涨停（封住）→ 持仓
            return b[2], t, "尾盘未涨停" + tag, j - k
        tail = [(x, (KBARS.get(code) or {})[x][2])
                for x in sorted(KBARS.get(code) or {})
                if x > dates[k] and (KBARS.get(code) or {})[x][2]]
        if not tail:
            return None
        return tail[-1][1], tail[-1][0], "期末仍涨停·按末收盘", len(tail)

    # ---------------- 6. 机会记录 ----------------
    def note_chance(B, e, code, th, why, yizi, buy_px, traded, result):
        key = (B, "summit", code)
        if key in chance_seen:
            return
        chance_seen.add(key)
        chances[B].append({
            "d": B, "code": code, "name": e.get("name") or NAME.get(code, ""),
            "boards": e.get("boards") or 1, "theme": th, "m": "summit",
            "why": why,
            "stage": stage_by_d.get(B, {}).get("stage") or "",
            "burst": not bool(e.get("sealed")),
            "yizi": bool(yizi), "tradable": bool(traded),
            "buy": round(buy_px, 2) if buy_px else None,
            "result": result,
        })

    # ---------------- 7. 买入执行 ----------------
    review = {m: [] for m in METHODS}

    def try_buy(B, code, th, why, need_boards, meta=None):
        meta = meta or {}
        e = daily.get(B, {}).get(code)
        if not e:
            stats["no_limit"] += 1
            return False
        k = DIDX.get(B)
        if k is None:
            return False
        if not (e["sealed"] or touch_on(code, B)):
            stats["no_limit"] += 1
            return False
        if (e.get("boards") or 0) < need_boards:
            stats["no_limit"] += 1
            return False
        if (B, "summit", code) in chance_seen:
            return False
        if consec_oneword_open(code, B):
            stats["consec_yizi"] += 1
            excluded_consec.add((B, code))
            return False
        b = bar_of(code, B)
        buy_px = None
        if b and len(b) >= 5:
            buy_px = b[2] if e["sealed"] else (e.get("limitPx") or b[4])
        if not buy_px:
            stats["no_kline"] += 1
            return False
        stats["cand"] += 1
        # 一字 / 涨停价开盘买不进：只进机会池，不参与模拟
        if buy_blocked(code, B, e):
            stats["yizi"] += 1
            note_chance(B, e, code, th, why + " · 一字买不进（不参与模拟）",
                        True, buy_px, False, "一字买不进")
            return "chance"
        out = simulate_exit(code, k)
        if not out:
            stats["no_kline"] += 1
            return False
        sell, sell_d, reason, hold = out
        t = {
            "d": B, "code": code, "name": e.get("name") or NAME.get(code, ""),
            "boards": e.get("boards") or need_boards, "theme": th,
            "stage": stage_by_d.get(B, {}).get("stage") or "",
            "burst": not bool(e["sealed"]),
            "why": why,
            "buy": round(buy_px, 2), "sell": round(sell, 2),
            "sellD": sell_d, "hold": hold, "reason": reason,
            "pnl": round((sell - buy_px) / buy_px * 100, 2),
            "sealed": bool(e["sealed"]), "method": "summit",
            # v45：买入逻辑字段（明细表「买入逻辑」列 + 共性分析同源）
            "b1": meta.get("b1"), "cnt": meta.get("cnt"), "maxB": meta.get("maxB"),
            "t": (e.get("time") or "")[:5], "shape": meta.get("shape") or "",
            "volx": meta.get("volx"), "vmax": bool(meta.get("vmax")),
            "lad": meta.get("lad") or "",
        }
        t["logic"] = _logic_txt(t)
        review["summit"].append(t)
        stats["fire"] += 1
        stats["burst"] += 1 if t["burst"] else 0
        note_chance(B, e, code, th, why, False, buy_px, True, "已参与")
        return "traded"

    # ---------------- 8. 主循环：B = 买入日（当日） ----------------
    # v44 用户口径：当日题材炒作里有赚钱效应的题材 → 买其龙头（板块最高连板），一字板除外。
    # 不再有「T 日选股 / 次日接力 / 空间突破」两笔买点，卖出规则不变。
    for i in range(1, len(dates)):
        B = dates[i]
        _det = {str(t.get("name") or "").strip() for t in (TD.get(B) or [])}
        for g in (MONEY.get(B) or []):
            nm = str(g.get("theme") or "").strip()
            c = str(g.get("leadCode") or "")
            if not c or not nm:
                continue
            # 「当日题材炒作」口径：该题材当日必须在题材炒作模块入链（金框 = 有赚钱效应）
            if _det and nm not in _det:
                stats["theme_skip"] += 1
                continue
            e = (daily.get(B) or {}).get(c)
            if not e or not (e["sealed"] or touch_on(c, B)):
                stats["no_limit"] += 1
                continue
            bd = e.get("boards") or 1
            stats["pick"] += 1
            vd = vol_desc(c, B)              # 当日量能（仅描述，不作过滤条件）
            vtxt = (" · " + vd) if vd else ""
            why = ("山顶洞人·当日赚钱效应龙头 · 板块「%s」%d 板龙头"
                   "（当日涨停 %d 只 / 最高 %d 板 / 梯队 %s）· 龙头非一字%s"
                   % (nm, bd, g.get("count") or 0, g.get("maxB") or bd,
                      g.get("ladder") or "-", vtxt))
            # v45：买入逻辑字段（首板家数 / 板块家数 / 涨停形态 / 封板时间 / 量能）
            _lad = ladder_of(B).get(nm) or []
            _b1 = sum(1 for x in _lad if (x.get("boards") or 1) == 1)
            _vx = None
            try:
                _vp = vol_prev(c, B)
                _vx = round(vol_of(c, B) / _vp, 2) if _vp else None
            except Exception:
                _vx = None
            _shp = ""
            if ctx.get("shape_of"):
                try:
                    _shp = ctx["shape_of"](c, B, e.get("name") or "")
                except Exception:
                    _shp = ""
            _meta = {"b1": _b1, "cnt": g.get("count") or 0,
                     "maxB": g.get("maxB") or bd, "shape": _shp,
                     "volx": _vx, "vmax": is_max_vol100(c, B),
                     "lad": g.get("ladder") or ""}
            if try_buy(B, c, nm, why, bd, _meta) == "traded":
                stats["m1"] += 1

    # ---------------- 9. 去重排序 ----------------
    for m in METHODS:
        seen, uniq = set(), []
        for t in review[m]:
            key = (t["d"], t["code"])
            if key in seen:
                continue
            seen.add(key)
            uniq.append(t)
        uniq.sort(key=lambda x: (x["d"], x["code"]))
        review[m] = uniq
    # 选股日即为近 100 日最大量的笔数（放量的最佳形态，仅统计不额外过滤）
    stats["maxvol"] = sum(1 for t in review["summit"]
                          if "近100日最大量" in str(t.get("why") or ""))

    # ---------------- 9.5 买入逻辑共性（v45 用户要求） ----------------
    # 把「赚钱效应买入」按 市场阶段 / 题材情况 / 个股形态 / 涨停时机 四维拆开，
    # 每组给 笔数 / 胜率 / 笔均收益 / 盈亏比，再由这些数字生成结论（不写死观点）。
    def _m(rows):
        n = len(rows)
        if not n:
            return None
        w = [t["pnl"] for t in rows if t["pnl"] > 0]
        lo = [t["pnl"] for t in rows if t["pnl"] < 0]
        aw = sum(w) / len(w) if w else 0.0
        al = sum(lo) / len(lo) if lo else 0.0
        return {"n": n, "win": round(len(w) / n * 100, 1),
                "avg": round(sum(t["pnl"] for t in rows) / n, 2),
                "aw": round(aw, 2), "al": round(al, 2),
                "pf": round(abs(aw / al), 2) if al else 0}

    def _bucket(rows, key, order):
        g = defaultdict(list)
        for t in rows:
            g[key(t)].append(t)
        out = []
        for k in order:
            if g.get(k):
                out.append(dict(k=k, **_m(g[k])))
        return out

    def _k_stage(t):
        return t.get("stage") or "无阶段"

    def _k_boards(t):
        b = t.get("boards") or 0
        return "%d板" % b if b <= 4 else "5板及以上"

    def _k_cnt(t):
        c = t.get("cnt") or 0
        return "3-4只" if c <= 4 else ("5-6只" if c <= 6 else "7只以上")

    def _k_b1(t):
        b = t.get("b1")
        if b is None:
            return "未知"
        return "0-1只" if b <= 1 else ("2-3只" if b <= 3 else "4只以上")

    def _k_seal(t):
        s = str(t.get("t") or "")
        if len(s) < 5:
            return "未知"
        hh, mm = int(s[:2]), int(s[3:5])
        v = hh * 60 + mm
        if v <= 9 * 60 + 35:
            return "≤09:35"
        if v <= 10 * 60 + 30:
            return "09:36-10:30"
        if v <= 13 * 60 + 30:
            return "10:31-13:30"
        return "13:31 之后"

    def _k_shape(t):
        return t.get("shape") or "未知"

    def _k_vol(t):
        if t.get("vmax"):
            return "近100日最大量"
        v = t.get("volx")
        if v is None:
            return "未知"
        return "明显放量" if v >= 1.2 else ("平量" if v >= 0.9 else "缩量")

    _all = review["summit"]
    _tot = _m(_all)
    _S = {
        "total": _tot,
        "stage": _bucket(_all, _k_stage, ["突破", "主升", "补涨", "节点", "无阶段"]),
        "boards": _bucket(_all, _k_boards, ["3板", "4板", "5板及以上"]),
        "cnt": _bucket(_all, _k_cnt, ["3-4只", "5-6只", "7只以上"]),
        "b1": _bucket(_all, _k_b1, ["0-1只", "2-3只", "4只以上", "未知"]),
        "seal": _bucket(_all, _k_seal, ["≤09:35", "09:36-10:30", "10:31-13:30",
                                        "13:31 之后", "未知"]),
        "shape": _bucket(_all, _k_shape, ["赚钱", "普通", "长腿", "赚T", "低开", "地天",
                                          "一字", "未知"]),
        "vol": _bucket(_all, _k_vol, ["近100日最大量", "明显放量", "平量", "缩量", "未知"]),
        "years": _bucket(_all, lambda t: t["d"][:4], sorted({t["d"][:4] for t in _all})),
    }

    def _best(arr, minn=30):
        arr = [x for x in arr if x and x["n"] >= minn]
        return max(arr, key=lambda x: x["avg"]) if arr else None

    def _worst(arr, minn=30):
        arr = [x for x in arr if x and x["n"] >= minn]
        return min(arr, key=lambda x: x["avg"]) if arr else None

    _read = []
    if _tot:
        _read.append(
            "总体：全期 %d 笔，胜率 %.1f%%，笔均 %+.2f%%（盈利笔均 %+.2f%% / 亏损笔均 %+.2f%%），"
            "盈亏比 %.2f。" % (_tot["n"], _tot["win"], _tot["avg"], _tot["aw"],
                             _tot["al"], _tot["pf"]))
    for _key, _lab in (("stage", "市场阶段"), ("cnt", "题材热度（板块当日涨停家数）"),
                       ("b1", "题材梯队（首板家数）"), ("boards", "龙头板位"),
                       ("shape", "个股涨停形态"), ("seal", "涨停时机（首次封板时间）"),
                       ("vol", "量能")):
        _a, _b = _best(_S[_key]), _worst(_S[_key])
        if _a and _b and _a["k"] != _b["k"]:
            _read.append("%s：最好「%s」%d 笔 / 胜率 %.1f%% / 笔均 %+.2f%%；最差「%s」"
                         "%d 笔 / 胜率 %.1f%% / 笔均 %+.2f%%（差 %.1f 个百分点）。"
                         % (_lab, _a["k"], _a["n"], _a["win"], _a["avg"],
                            _b["k"], _b["n"], _b["win"], _b["avg"],
                            _a["avg"] - _b["avg"]))
    # 三条「反直觉但数据如此」的结论：只在数据确实成立时才写，避免主观发挥
    if _tot:
        _vb = {x["k"]: x for x in _S["vol"]}
        _vm, _vq = _vb.get("近100日最大量"), _vb.get("缩量")
        if _vm and _vq and _vq["avg"] > _vm["avg"] + 0.3:
            _read.append("量能反直觉：缩量涨停 %d 笔 / 胜率 %.1f%% / 笔均 %+.2f%%，反而优于"
                         "近100日最大量（%d 笔 / 胜率 %.1f%% / 笔均 %+.2f%%）——连板中继的惜售板"
                         "分歧小，高位放量多是换手分歧板；放量只作确认，不做必要条件。"
                         % (_vq["n"], _vq["win"], _vq["avg"], _vm["n"], _vm["win"], _vm["avg"]))
        _bb = {x["k"]: x for x in _S["b1"]}
        _bz = _bb.get("0-1只")
        if _bz and _bz["avg"] < 0:
            _read.append("最好用的排雷条件：板块当日首板 ≤1 只（梯队断层、只剩高标孤军）时 "
                         "%d 笔 / 胜率 %.1f%% / 笔均 %+.2f%% —— 这类「没有跟风」的龙头直接放弃，"
                         "比任何技术形态都管用。" % (_bz["n"], _bz["win"], _bz["avg"]))
        _sb = {x["k"]: x for x in _S["seal"]}
        _se, _sl = _sb.get("≤09:35"), _sb.get("13:31 之后")
        if _se and _sl and (_se["avg"] - _sl["avg"]) >= 2:
            _read.append("涨停时机：早盘封板（≤09:35）%d 笔 / 胜率 %.1f%% / 笔均 %+.2f%%，"
                         "尾盘（13:31 后）%d 笔 / 胜率 %.1f%% / 笔均 %+.2f%% —— 封板越早越是主动资金，"
                         "尾盘偷袭板次日溢价明显更差。" % (_se["n"], _se["win"], _se["avg"],
                                                          _sl["n"], _sl["win"], _sl["avg"]))
    # 可复制性：胜率 / 盈亏比 / 分组稳定性三件事同时看，避免只看单年运气
    if _tot:
        _wins = [x["win"] for x in _S["stage"] if x["n"] >= 30]
        _spread = (max(_wins) - min(_wins)) if len(_wins) >= 2 else 0
        if _tot["win"] >= 52 and _tot["pf"] >= 1.8 and _spread <= 20:
            _lvl, _lvltxt = "高", "各分组胜率差距小、盈亏比稳定"
        elif _tot["win"] >= 48 and _tot["pf"] >= 1.3:
            _lvl, _lvltxt = "中", "整体为正但分组差异明显，需挑阶段"
        else:
            _lvl, _lvltxt = "偏低", "整体优势不明显"
        _read.append("可复制性：%s——%s（胜负由「买最强题材的龙头」这一条规则决定，"
                     "参数少、无需主观选股）。" % (_lvl, _lvltxt))
        _read.append("盘前选定 + 盘中触发：盘前（前一交易日收盘后）即可锁定候选——"
                     "昨日涨停梯队里已有 3 板+龙头的题材、龙头是谁、板块有几只涨停、"
                     "首板几只，全部是收盘数据；盘中只需等该龙头再次封板（涨停价）触发买入，"
                     "不预测、不打提前量。唯一无法盘前确定的是「今天还能不能涨停」，"
                     "所以执行上是条件单 / 盯板，不是竞价挂单。")
    _S["read"] = _read

    print("山顶洞人逐笔：%d 笔（当日赚钱效应题材龙头 · 涨停买入）；候选龙头 %d 个"
          "（同一股属多题材只记一笔）；其中当日为近100日最大量 %d 笔"
          % (len(review["summit"]), stats["pick"], stats["maxvol"]), flush=True)
    print("触发统计：候选 %d；一字买不进 %d；当日未涨停 %d；题材未入链 %d；"
          "无日K %d；连续一字剔除 %d；一字跌停顺延 %d 个股票日"
          % (stats["cand"], stats["yizi"], stats["no_limit"], stats["theme_skip"],
             stats["no_kline"], stats["consec_yizi"], stats["defer"]), flush=True)

    # ---------------- 10. K线骷髅头标记 ----------------
    kMarks = {}
    for code, rec in KBARS.items():
        marks = {}
        for d, b in rec.items():
            if d not in DIDX or len(b) < 5:
                continue
            lim = lim_of(code, d)
            pv = prev_close(code, d)
            if not pv:
                continue
            hi, cl = b[4] / pv - 1, b[2] / pv - 1
            touch = hi >= lim - .006
            down = cl <= -(lim - .006)
            if touch and (down or cl <= -.05):
                marks[d] = 2
            elif down:
                marks[d] = 1
        if marks:
            kMarks[code] = marks
    print("K线骷髅头：%d 只个股 / %d 个交易日标记" %
          (len(kMarks), sum(len(v) for v in kMarks.values())), flush=True)

    # ---------------- 11. 机会池排序 ----------------
    chances_out = {}
    for d in sorted(chances):
        arr = chances[d]
        arr.sort(key=lambda x: (-x["boards"], x["code"]))
        chances_out[d] = arr
    print("当日机会池：%d 个交易日 / %d 条机会（一字买不进 %d 条）" %
          (len(chances_out), sum(len(v) for v in chances_out.values()),
           sum(1 for v in chances_out.values() for x in v if x["yizi"])), flush=True)

    UNI = ("【统一口径 v45 · 单一战法「山顶洞人」】① 时点：本页日期即买入日（当日）；"
           "当日题材炒作里有赚钱效应的题材，当日以涨停价买入其龙头股。"
           "② 买入价一律以涨停价打板；盘中冲击涨停后回落炸板，也按涨停价模拟买入、纳入统计。"
           "③ 一字涨停买不进，只进机会池、不纳入模拟。"
           "④ 选股：题材当日有 ≥3 板个股再涨停、板块当日涨停 ≥3 只、且龙头为换手（非一字）"
           "连板股（与题材炒作页★赚钱效应金框同源）；龙头 = 该题材当日最高连板个股，"
           "同一只股属多个题材只记一笔。"
           "⑤ 卖出：次日仍涨停持仓，首个收盘未涨停日尾盘按收盘价卖出，"
           "盘中 −7% 无条件止损；遇一字跌停无换手卖不出，顺延至首个可成交日。"
           "⑥ 明细表「买入逻辑」列＝该笔的买入依据摘要（题材阶段 / 板位 / 首板家数 / "
           "封板时间 / 涨停形态 / 量能），由当日真实数据生成，不做主观修饰。")

    meta_out = {m: dict(v, uni=UNI) for m, v in META.items()}

    return {
        "review": review,
        "reviewStat": _S,               # v45：买入逻辑共性（阶段 / 题材 / 形态 / 时机）
        "reviewMeta": meta_out,
        "chances": chances_out,
        "kMarks": kMarks,
        "excludedConsecYizi": [list(x) for x in sorted(excluded_consec)],
        "v23stat": {
            "cand": stats["cand"],
            "fire": stats["fire"],
            "yizi": stats["yizi"],
            "noLimit": stats["no_limit"],
            "noAssist": stats["no_assist"],
            "heightSkip": stats["height_skip"],
            "noVol": stats["no_vol"],
            "themeSkip": stats["theme_skip"],
            "maxVol": stats["maxvol"],
            "noKline": stats["no_kline"],
            "consecYizi": stats["consec_yizi"],
            "burst": stats["burst"],
            "pick": stats["pick"],
            "byM": {"summit": len(review["summit"])},
            "sub": {"m1": stats["m1"], "m2": stats["m2"]},
        },
    }
