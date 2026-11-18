# -*- coding: utf-8 -*-
"""两个战法的全期逐笔回测（v32：空间突破精简为「N 板压制 + 昨日板块助攻 + 当日 N+1 板触板即买」）。

由 build_merged.py 末尾调用 build(ctx)，返回：
  review        {method: [trade,...]}  逐笔（按日期、代码排序）
  reviewMeta    {method: {name,buy,sell,note,uni}}  页面口径说明
  chances       {买入日: [opp,...]}    当日机会（含一字买不进、炸板）
  kMarks        {code: {date: 1|2}}    K线骷髅头

v32 两大战法（B = 买入日 / 当日，T = B−1 复盘日 / 昨日）：
  · 战法一 空间突破：面对空间压制 N 板（N ≥ 2）；
    昨日（T）板块已有助攻股（该题材昨日梯队中另有 2 板及以上跟风连板股）；
    当日有 N+1 板龙头盘中冲击涨停那一刻即判定为突破买点、按涨停价买入此股；
    随后回落炸板＝突破失败，该笔同样纳入模拟统计；只在突破当日参与（不等到次日）。
    其余条件（断板杀跌、当日赚钱效应榜、当日梯队、高开非涨停开盘）已全部删除。
  · 战法二 试错龙头：昨日市场为节点阶段，今日板块龙头（3 板起）涨停买入，
    博弈成为新的空间龙头；龙头高开但不是涨停开盘。
  · 战法二前置：所买个股板块有赚钱效应（≥3 板个股再涨停 + 板块至少 3 只涨停）、
    有梯队（≥3 板龙头 + ≥2 板助攻 + 首板），且经「昨日买龙头者今日可获利了结 /
    低吸·半路·打板做跟风均不亏超 5%」实证验证（money_effect.compute）。
  · 已删除（v31）：战法三 补涨 fillUp 整条战法（含一字触发补涨、改买助攻股）。
  · 卖出（统一）：次日仍涨停持仓，首个收盘未涨停日尾盘按收盘价卖出，
    盘中 −7% 无条件止损；一字跌停无换手顺延至首个可成交日。
  · 已删除（v29 清空）：示范龙头 demoLead、v27 门槛/禁入/首次买入去重、
    凹凸曼/黑盒/板块助攻门禁/赚钱效应示范门禁/距压制≥2板/阶段白名单等全部历史口径。
"""
import os
import re
import sys
from collections import defaultdict

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

STOP_PCT = 0.07
METHODS = ["spaceDragon", "trialLead"]

SELL_TXT = ("统一卖出规则：次日仍涨停则持仓，首个收盘未涨停日尾盘按收盘价卖出，"
            "盘中跌幅大于 7% 无条件止损。")

MONEY_TXT = ("【板块赚钱效应·两战法共同前置】板块当日有赚钱效应（≥3 板个股再涨停且板块至少 3 只涨停）、"
             "有梯队（≥3 板龙头 + ≥2 板助攻跟风 + 首板）；并实证验证：昨日买入龙头的人今日"
             "可获利了结（盘中最高价高于昨收、收盘跌幅 ≤5%），低吸／半路／打板做跟风股"
             "当日平均收益均不低于 −5%（用题材炒作梯队 + 同花顺个股日K 逐日验证）。")

META = {
    "spaceDragon": {
        "name": "战法一 · 空间突破",
        "buy": "【买入】面对空间压制 N 板（上一个空间龙头整批断板时改写的高度，N ≥ 2）；"
               "昨日（T）板块已有助攻股（该题材昨日梯队中另有 2 板及以上的跟风连板股）；"
               "当日出现 N+1 板龙头，盘中冲击涨停的那一刻即判定为突破买点，按涨停价买入此股；"
               "随后回落炸板＝突破失败，该笔同样纳入模拟统计；只在突破当日参与，不等到次日。",
        "sell": SELL_TXT,
        "note": "空间压制＝上一个空间龙头整批断板时改写的压制高度（取自 stages.suppress）。"
                "买点用日K最高价判定「盘中是否触及涨停」，不看收盘是否封住："
                "炸板（盘中触板、收盘未封住）同样按涨停价买入并计入逐笔。"
                "一字涨停（集合竞价封死、全天未开板）实际买不进，只进机会池、不纳入模拟。"
                "v32：除上述三点外，断板日大长阴/次日跌停、当日板块赚钱效应榜、当日梯队、"
                "高开但不是涨停开盘等条件已全部删除。",
    },
    "trialLead": {
        "name": "战法二 · 试错龙头",
        "buy": "【买入】昨日市场为节点阶段；今天关注的龙头所在板块当日有赚钱效应"
               "（≥3 板个股再涨停且板块至少 3 只涨停）；龙头 3 板开始关注；"
               "板块有 2 连板或以上的跟风股（板块有助攻，证明板块有持续性）；"
               "买入龙头，博弈成为新的空间龙头；龙头高开但不是涨停开盘。",
        "sell": SELL_TXT,
        "note": "「节点」＝市场空间龙头整批断板日（stages.stage＝节点，取复盘日 T）；"
                "「龙头」＝买入当日所属板块内连板板位最高、且 ≥3 板的个股（可并列）。"
                + MONEY_TXT +
                "一字涨停买不进则只进机会池、不参与模拟；炸板按涨停价买入、纳入统计。",
    },
}


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
    MONEY = ctx.get("money") or {}
    stage_by_d = {x["date"]: x for x in days}

    # 板块赚钱效应验证：由 money_effect.compute 统一口径（构建/校验/报告同源）
    BOARD_EARN = ctx.get("board_earn")
    if BOARD_EARN is None:
        import money_effect as ME
        BOARD_EARN = ME.compute(dates, LF, MONEY, KBARS)

    stats = {"cand": 0, "fire": 0, "yizi": 0, "no_limit": 0,
             "board_skip": 0, "money_skip": 0, "break_skip": 0,
             "open_skip": 0, "no_kline": 0, "defer": 0, "consec_yizi": 0,
             "burst": 0, "m1": 0, "m2": 0, "prev_skip": 0}

    chances = defaultdict(list)
    chance_seen = set()
    excluded_consec = set()

    NAME = {}
    for d, v in pool_map.items():
        for c, s in v.items():
            if s.get("name"):
                NAME[c] = s["name"]

    # ---------------- 基础工具 ----------------
    def lim_of(code):
        nm = NAME.get(code, "")
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

    def chg_on(code, d):
        rec = CLOSE.get(code)
        if not rec or d not in rec or not rec[d]:
            return None
        pv = prev_close(code, d)
        return round((rec[d] / pv - 1) * 100, 2) if pv else None

    def limit_px(code, d):
        pv = prev_close(code, d)
        return round(pv * (1 + lim_of(code)), 2) if pv else None

    def touch_on(code, d):
        """盘中冲击涨停（含封板 / 炸板）。"""
        b, pv = bar_of(code, d), prev_close(code, d)
        if not b or len(b) < 5 or not pv:
            return False
        return b[4] / pv - 1 >= lim_of(code) - .006

    def is_oneword_up(code, d):
        b, pv = bar_of(code, d), prev_close(code, d)
        if not b or len(b) < 5 or not pv:
            return False
        return (b[1] == b[2] == b[3] == b[4]
                and b[2] / pv - 1 >= lim_of(code) - .006)

    def is_oneword_down(code, d):
        b, pv = bar_of(code, d), prev_close(code, d)
        if not b or len(b) < 5 or not pv:
            return False
        return (b[1] == b[2] == b[3] == b[4]
                and b[2] / pv - 1 <= -(lim_of(code) - .006))

    def open_at_limit(code, d):
        b, pv = bar_of(code, d), prev_close(code, d)
        if not b or len(b) < 5 or not pv:
            return False
        return b[1] / pv - 1 >= lim_of(code) - .006

    def high_open_ok(code, d, e=None):
        """高开但不是涨停开盘：开盘 > 昨收 且 开盘 < 涨停价。
        涨停价以当日封板收盘价（除权后权威价）优先；炸板用 limitPx 推算，
        避免未复权前收盘价在除权日造成误判（如 2022-06-28 金奥博）。"""
        b = bar_of(code, d)
        if not b or len(b) < 5:
            return False
        lim = lim_of(code)
        if e and e.get("sealed"):
            lp = b[2]
        else:
            lp = (e or {}).get("limitPx") or limit_px(code, d)
        if not lp or lp <= 0:
            return False
        prev_adj = lp / (1 + lim)
        return b[1] > prev_adj * 1.0001 and b[1] < lp - .001

    def buy_blocked(code, d, e=None):
        """一字涨停（集合竞价封死全天未开板）→ 实际买不进。
        注意：涨停价开盘但盘中打开（T 字板）不算一字，但它不满足
        「高开但不是涨停开盘」，会在 high_open_ok 处被拦下。"""
        if e and e.get("yizi"):
            return True
        return is_oneword_up(code, d)

    def consec_oneword_open(code, d):
        """连续一字涨停且当日涨停开盘 —— 全程无换手、买不进，不属于任何战法标的。"""
        if not open_at_limit(code, d):
            return False
        i = DIDX.get(d)
        if i is None:
            return False
        lim = lim_of(code)
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
    # v30：必须有序遍历。set 的迭代顺序受进程 hash 随机化影响，会让 daily[d]
    # 里炸板股的插入顺序每次不同，进而使 cand 顺序漂移、逐笔 why 文案不稳，
    # 文案里的「一字龙头名」在两次构建间随机变化（数据指纹也跟着变）。
    for code in sorted(all_codes):
        rec = KBARS.get(code)
        if not rec:
            continue
        lim = lim_of(code)
        for d in dates:
            if code in pool_map[d]:
                continue
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
    print("战法候选：池内收盘涨停 + 炸板补充 %d 个股票日" % n_burst, flush=True)

    # ---------------- 2. 当日真实题材反查（与题材梯队浮层同源） ----------------
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

    # ---------------- 4. 板块赚钱效应（两战法共同前置） ----------------
    def board_money_ok(B, nm):
        rec = BOARD_EARN.get((B, nm))
        if rec is None:
            return False, "板块「%s」未登当日赚钱效应榜" % nm
        v = rec.get("ok") if isinstance(rec, dict) else bool(rec)
        if not v:
            stats["money_skip"] += 1
            reason = rec.get("reason", "") if isinstance(rec, dict) else ""
            return False, "板块「%s」赚钱效应验证未过：%s" % (nm, reason)
        desc = "板块「%s」梯队 %s · 昨龙可获利（%s）· 低吸%+.1f%% / 半路%+.1f%% / 打板%+.1f%%" % (
            nm, rec.get("ladder", ""), rec.get("lead", ""),
            rec.get("low") or 0, rec.get("mid") or 0, rec.get("board") or 0)
        return True, desc

    def board_assist(B, nm, mx):
        """板块有 2 连板或以上的跟风股（板块有助攻）。"""
        arr = (LF.get(B) or {}).get(nm) or []
        return any(2 <= (x.get("boards") or 1) < mx for x in arr)

    def board_assist_prev(T, nm, exclude=None):
        """昨日（T）板块已有助攻股：该题材昨日梯队中，除今日目标股（昨日 N 板）之外，
        还存在 2 板及以上的跟风连板股 —— 证明板块在突破前一日已连板成梯队。
        v32：不再要求「低于当日最高板」，直接排除目标股本身，避免 N=2 时永远无解。
        题材归属优先取 T 日梯队，缺失时用 T 日个股题材兜底。"""
        arr = (LF.get(T) or {}).get(nm) or []
        if not arr:
            arr = [{"code": c, "boards": e.get("boards") or 1, "name": e.get("name")}
                   for c, e in (daily.get(T) or {}).items() if theme_of(T, c) == nm]
        if not arr:
            return False, ""
        assists = [x for x in arr
                   if str(x.get("code")) != str(exclude) and (x.get("boards") or 1) >= 2]
        if not assists:
            return False, ""
        amx = max((x.get("boards") or 1) for x in assists)
        names = [str(x.get("name") or NAME.get(str(x.get("code")), ""))
                 for x in assists if (x.get("boards") or 1) == amx]
        return True, "昨日助攻 %d 板 %s（%d 只）" % (
            amx, "、".join([n for n in names if n][:3]) or "—", len(assists))

    # ---------------- 5. 统一卖出模拟 ----------------
    def simulate_exit(code, k, buy):
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
                continue
            return b[2], t, "尾盘未涨停" + tag, j - k
        tail = [(x, (KBARS.get(code) or {})[x][2])
                for x in sorted(KBARS.get(code) or {})
                if x > dates[k] and (KBARS.get(code) or {})[x][2]]
        if not tail:
            return None
        return tail[-1][1], tail[-1][0], "期末仍涨停·按末收盘", len(tail)

    # ---------------- 6. 机会记录 ----------------
    def note_chance(B, m, e, code, th, why, yizi, buy_px, traded, result):
        key = (B, m, code)
        if key in chance_seen:
            return
        chance_seen.add(key)
        chances[B].append({
            "d": B, "code": code, "name": e.get("name") or NAME.get(code, ""),
            "boards": e.get("boards") or 1, "theme": th, "m": m,
            "why": why,
            "stage": stage_by_d.get(B, {}).get("stage") or "",
            "burst": not bool(e.get("sealed")),
            "yizi": bool(yizi), "tradable": bool(traded),
            "buy": round(buy_px, 2) if buy_px else None,
            "result": result,
        })

    # ---------------- 7. 统一触发判定 ----------------
    review = {m: [] for m in METHODS}

    def try_buy(m, B, code, th, why, need_boards, check_open=True):
        """B 日：盘中触板（封板或炸板）+ 目标板位 → 记录。
        check_open=False 时不做「高开但不是涨停开盘」校验（战法一 v32 已删除该条件）。"""
        e = daily.get(B, {}).get(code)
        if not e:
            stats["no_limit"] += 1
            return False
        k = DIDX.get(B)
        if k is None:
            return False
        ok_limit = bool(e["sealed"] or touch_on(code, B))
        if not ok_limit:
            stats["no_limit"] += 1
            return False
        if (e.get("boards") or 0) < need_boards:
            stats["no_limit"] += 1
            return False
        if (B, m, code) in chance_seen:
            return False
        if consec_oneword_open(code, B):
            stats["consec_yizi"] += 1
            excluded_consec.add((B, code))
            return False
        # 高开但不是涨停开盘（战法一 v32 已删除该条件，仅保留「一字买不进」可成交性）
        if check_open and not high_open_ok(code, B, e):
            stats["open_skip"] += 1
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
            note_chance(B, m, e, code, th, why + " · 一字买不进（不参与模拟）",
                        True, buy_px, False, "一字买不进")
            return True
        out = simulate_exit(code, k, buy_px)
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
            "sealed": bool(e["sealed"]), "method": m,
        }
        review[m].append(t)
        stats["fire"] += 1
        stats["burst"] += 1 if t["burst"] else 0
        note_chance(B, m, e, code, th, why, False, buy_px, True, "已参与")
        return True

    # ---------------- 8. 两战法主循环：B = 买入日，T = B−1 复盘日 ----------------
    for i in range(1, len(dates)):
        B, T = dates[i], dates[i - 1]
        stT = stage_by_d.get(T) or {}
        N = stT.get("suppress") or 0
        node_T = (stT.get("stage") or "") == "节点"

        # ---------- 战法一 空间突破（v32 精简口径，独立于题材榜）----------
        # 只保留三点：① 面对空间压制 N 板（N ≥ 2）；② 昨日（T）板块已有助攻股；
        # ③ 当日出现 N+1 板个股、盘中冲击涨停那一刻即买、按涨停价成交（炸板同样计入）。
        # 断板杀跌、当日赚钱效应榜、当日梯队、高开非涨停开盘等条件全部删除。
        if N >= 2:
            for c, e in sorted((daily.get(B) or {}).items()):
                if (e.get("boards") or 0) != N + 1:
                    continue
                th = theme_of(B, c)
                ok_prev, prev_desc = board_assist_prev(T, th, c) if th else (False, "")
                if not ok_prev:
                    stats["prev_skip"] += 1
                    continue
                why = ("突破买点 · 空间压制 %d 板 → 今日 %d 板盘中冲击涨停 · 题材「%s」· %s"
                       % (N, N + 1, th, prev_desc))
                if buy_blocked(c, B, e):
                    stats["cand"] += 1
                    stats["yizi"] += 1
                    note_chance(B, "spaceDragon", e, c, th,
                                why + " · 一字买不进（不参与模拟）",
                                True, limit_px(c, B), False, "一字买不进")
                    continue
                try_buy("spaceDragon", B, c, th, why, N + 1, check_open=False)

        # 当日登上赚钱效应榜的题材（≥3 板个股再涨停 + 板块至少 3 只涨停）
        for row in (MONEY.get(B) or []):
            nm = row.get("theme")
            if not nm:
                continue
            okm, mdesc = board_money_ok(B, nm)
            if not okm:
                continue
            # 当日该题材梯队（收盘涨停）
            lf_arr = (LF.get(B) or {}).get(nm) or []
            if not lf_arr:
                stats["board_skip"] += 1
                continue
            mx = max((x.get("boards") or 1) for x in lf_arr)
            if not board_assist(B, nm, mx):
                stats["board_skip"] += 1
                continue
            # 当日该题材候选（含盘中触板炸板），题材归属取 B 日
            cand = [(c, e) for c, e in (daily.get(B) or {}).items()
                    if theme_of(B, c) == nm]
            if not cand:
                continue
            cand_mx = max((e.get("boards") or 1) for _, e in cand)

            # ---------- 战法二 试错龙头 ----------
            # 昨日节点 → 今日板块最高板龙头（≥3 板）涨停买入，博弈新空间龙头
            if node_T:
                for c, e in cand:
                    if (e.get("boards") or 0) < 3 or (e.get("boards") or 0) < cand_mx:
                        continue
                    why = ("昨日「节点」→ 今日板块「%s」最高 %d 板龙头 · 博弈新空间龙头 · %s"
                           % (nm, cand_mx, mdesc))
                    if buy_blocked(c, B, e):
                        stats["cand"] += 1
                        stats["yizi"] += 1
                        note_chance(B, "trialLead", e, c, nm,
                                    why + " · 一字买不进（不参与模拟）",
                                    True, limit_px(c, B), False, "一字买不进")
                        continue
                    try_buy("trialLead", B, c, nm, why, cand_mx)

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
        stats[{"spaceDragon": "m1", "trialLead": "m2"}[m]] = len(uniq)

    print("两战法逐笔：" + " / ".join(
        "%s %d笔" % (META[m]["name"].split("·")[-1].strip(), len(review[m]))
        for m in METHODS), flush=True)
    print("触发统计：候选 %d；一字买不进 %d；未涨停/未晋级 %d；昨日无助攻 %d；"
          "板块梯队不足 %d；赚钱效应验证未过 %d；高开条件不符 %d；"
          "无日K %d；连续一字剔除 %d；一字跌停顺延 %d 个股票日"
          % (stats["cand"], stats["yizi"], stats["no_limit"], stats["prev_skip"],
             stats["board_skip"], stats["money_skip"], stats["open_skip"],
             stats["no_kline"], stats["consec_yizi"], stats["defer"]), flush=True)

    # ---------------- 10. K线骷髅头标记 ----------------
    kMarks = {}
    for code, rec in KBARS.items():
        lim = lim_of(code)
        marks = {}
        for d, b in rec.items():
            if d not in DIDX or len(b) < 5:
                continue
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
        arr.sort(key=lambda x: (METHODS.index(x["m"]), -x["boards"], x["code"]))
        chances_out[d] = arr
    print("当日机会池：%d 个交易日 / %d 条机会（一字买不进 %d 条）" %
          (len(chances_out), sum(len(v) for v in chances_out.values()),
           sum(1 for v in chances_out.values() for x in v if x["yizi"])), flush=True)

    UNI = ("【统一口径 v32】① 时点：B 为买入日（当日），T=B−1 为复盘日（昨日）；"
           "本页日期为买入日。② 买入价一律以涨停价打板；当日盘中冲击涨停后回落炸板，"
           "也按涨停价模拟买入、纳入统计（战法一「突破失败」同样计入）。"
           "③ 一字涨停（集合竞价封死、全天未开板）实际买不进，只进机会池、不纳入模拟。"
           "④ 战法一（空间突破）只保留三点：面对空间压制 N 板（N ≥ 2）、昨日板块已有助攻股、"
           "当日 N+1 板盘中冲击涨停那一刻即买；断板杀跌、当日赚钱效应、当日梯队、"
           "高开非涨停开盘等其余条件全部删除，且只在突破当日参与。"
           "⑤ 战法二（试错龙头）：昨日节点，今日板块 ≥3 板最高板龙头涨停买入，"
           "博弈新空间龙头；仍要求板块当日赚钱效应、板块有助攻、高开非涨停开盘。"
           "⑥ 卖出（统一）：次日仍涨停持仓，首个收盘未涨停日尾盘按收盘价卖出，"
           "盘中 −7% 无条件止损；遇一字跌停无换手卖不出，顺延至首个可成交日。"
           "⑦ v32 仅保留空间突破、试错龙头两战法与统一卖出规则，补涨战法已删除。")

    meta_out = {}
    for m, v in META.items():
        meta_out[m] = dict(v, uni=UNI)

    return {
        "review": review,
        "reviewMeta": meta_out,
        "chances": chances_out,
        "kMarks": kMarks,
        "excludedConsecYizi": [list(x) for x in sorted(excluded_consec)],
        "v23stat": {
            "cand": stats["cand"],
            "fire": stats["fire"],
            "yizi": stats["yizi"],
            "noLimit": stats["no_limit"],
            "boardSkip": stats["board_skip"],
            "moneySkip": stats["money_skip"],
            "breakSkip": stats["break_skip"],
            "openSkip": stats["open_skip"],
            "noKline": stats["no_kline"],
            "consecYizi": stats["consec_yizi"],
            "burst": stats["burst"],
            "prevSkip": stats["prev_skip"],
            "byM": {"spaceDragon": stats["m1"], "trialLead": stats["m2"]},
        },
    }
