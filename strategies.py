# -*- coding: utf-8 -*-
"""两个战法的全期逐笔回测（v32：空间突破精简为「N 板压制 + 昨日板块助攻 + 当日 N+1 板触板即买」）。

由 build_merged.py 末尾调用 build(ctx)，返回：
  review        {method: [trade,...]}  逐笔（按日期、代码排序）
  reviewMeta    {method: {name,buy,sell,note,uni}}  页面口径说明
  chances       {买入日: [opp,...]}    当日机会（含一字买不进、炸板）
  kMarks        {code: {date: 1|2}}    K线骷髅头

v38 两大战法（B = 买入日 / 当日，T = B−1 复盘日 / 昨日）：
  · 共同前置：上一空间龙头断板后无大亏（跌停 / 连续杀跌：断板日大长阴、
    跌停日、连续两日跌 ≥6% 任一出现即视为大亏）——大亏窗口内两战法均不买。
  · 战法一 空间突破：面对空间压制 N 板（N ≥ 2）；昨日（T）板块已有助攻股
    （该题材昨日梯队中另有 2 板及以上跟风连板股）；当日有 N+1 板龙头盘中冲击
    涨停那一刻即判定为突破买点、按涨停价买入此股；随后回落炸板＝突破失败，
    该笔同样纳入模拟统计；只在突破当日参与（不等到次日）。
  · 战法二 试错龙头：昨日市场为节点阶段；买入日龙头所在板块有赚钱效应的延续
    （昨日首板今日连板，或昨日连板今日断板但处于红盘）；买入板块最高板龙头
    （≥3 板），博弈成为新的空间龙头；龙头连续缩量或一字时不买——除非板块今天
    有多个连板或昨日连板换手晋级；3-5 板阶段首次放出近期最大成交量的龙头
    为最佳标的（why 留痕）；龙头高开但不是涨停开盘。
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

META = {
    "spaceDragon": {
        "name": "战法一 · 空间突破",
        "buy": "【买入】面对空间压制 N 板（上一个空间龙头整批断板时改写的高度，N ≥ 2）；"
               "上一空间龙头断板后无大亏（无跌停、无连续杀跌）——断板直接大亏则不买、提示风险；"
               "当日出现任意 N+1 板个股，盘中冲击涨停的那一刻即判定为空间突破买点，按涨停价买入；"
               "所有非一字板的空间突破一律纳入（含涨停炸板＝突破失败），不设昨日助攻等其它门槛；"
               "只在突破当日参与，不等到次日。",
        "sell": SELL_TXT,
        "note": "空间压制＝上一个空间龙头整批断板时改写的压制高度（取自 stages.suppress）。"
                "「无大亏」口径：断板日起至买入日，龙头无跌停、无断板日大长阴、"
                "无连续两日跌幅 ≥6% 的连续杀跌；断板直接大亏 = 情绪受损，不买并提示风险。"
                "买点用日K最高价判定「盘中是否触及涨停」，不看收盘是否封住："
                "炸板（盘中触板、收盘未封住）同样按涨停价买入并计入逐笔。"
                "一字涨停（集合竞价封死、全天未开板）实际买不进，只进机会池、不纳入模拟。",
    },
    "trialLead": {
        "name": "战法二 · 试错龙头",
        "buy": "【买入】昨日市场为节点阶段（空间龙头整批断板日），且昨日该板块已登赚钱效应榜、"
               "经实证验证（昨买龙头者今日可获利、跟风低吸/半路/打板不亏超 5%）；"
               "标的必须是当日收盘封板的板块最高板龙头（≥3 板）——"
               "盘中触板、收盘未封的炸板股不算龙头连板，只进机会池、不买入；"
               "3b 带 2b：板块内除标的以外仍有 ≥2 板封板跟风股；"
               "板块有赚钱效应的延续：昨日首板今日连板，或昨日连板今日断板但处于红盘"
               "（证据不得是标的自身）；"
               "买入日前一交易日（T）龙头成交量须放量或平量（相对 T−1），缩量不买；"
               "3-5 板阶段首次放出近期最大成交量的龙头为最佳标的；"
               "买入板块最高板龙头，博弈成为新的空间龙头；龙头高开但不是涨停开盘。",
        "sell": SELL_TXT,
        "note": "「节点」＝市场空间龙头整批断板日（stages.stage＝节点，取复盘日 T）；"
                "「昨日板块有赚钱效应」＝BOARD_EARN[(T,题材)] 经验证 ok（money_effect.compute 独立复算）；"
                "「3b 带 2b」＝板块最高板 ≥3 板，且除标的以外还有 ≥2 板封板跟风股，"
                "证明板块是梯队而不是单只个股独走；"
                "「赚钱效应延续」＝板块昨日首板今日晋级连板，或板块昨日连板股今日断板但收盘仍红盘，"
                "且该证据不能是标的自己（否则等于「龙头断板自证延续」）；"
                "「前一日放量/平量」＝买入日前一日（T）成交量 ≥ 再前一日（T−1），不放量不买。"
                "一字涨停买不进则只进机会池、不参与模拟。"
                "口径说明：标的以「当日收盘封板」确认（日K无盘中封板时刻数据），"
                "因此不包含「打板买入后尾盘炸板」的损失，胜率略偏乐观；"
                "战法一（突破）仍按盘中触板成交、炸板照计，两者口径不同请勿横向比较。",
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
             "burst": 0, "m1": 0, "m2": 0, "prev_skip": 0, "shrink_skip": 0,
             "cont_skip": 0}

    chances = defaultdict(list)
    chance_seen = set()
    excluded_consec = set()

    NAME = {}
    NAME_AT = {}                     # (code, date) -> 当日池内名称（v38 修复：
    for d, v in pool_map.items():    # 摘帽个股全局名覆盖历史 ST 名导致限幅误判）
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

    def vol_of(code, d):
        b = bar_of(code, d)
        return (b[5] or 0) if (b and len(b) >= 6) else 0

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

    def high_open_ok(code, d, e=None):
        """高开但不是涨停开盘：开盘 > 昨收 且 开盘 < 涨停价。
        涨停价以当日封板收盘价（除权后权威价）优先；炸板用 limitPx 推算，
        避免未复权前收盘价在除权日造成误判（如 2022-06-28 金奥博）。"""
        b = bar_of(code, d)
        if not b or len(b) < 5:
            return False
        lim = lim_of(code, d)
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
    # v30：必须有序遍历。set 的迭代顺序受进程 hash 随机化影响，会让 daily[d]
    # 里炸板股的插入顺序每次不同，进而使 cand 顺序漂移、逐笔 why 文案不稳，
    # 文案里的「一字龙头名」在两次构建间随机变化（数据指纹也跟着变）。
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

    # ---------------- 4. 板块赚钱效应（v39：战法二恢复为「昨日板块有赚钱效应」前置） ----------------
    def board_money_ok(B, nm):
        """板块 B 日是否有赚钱效应（由 money_effect.compute 独立复算的 BOARD_EARN）。"""
        rec = BOARD_EARN.get((B, nm))
        if rec is None:
            return False, "板块「%s」未登当日赚钱效应榜" % nm
        v = rec.get("ok") if isinstance(rec, dict) else bool(rec)
        if not v:
            stats["money_skip"] += 1
            reason = rec.get("reason", "") if isinstance(rec, dict) else ""
            return False, "板块「%s」赚钱效应验证未过：%s" % (nm, reason)
        desc = ("昨日板块「%s」有赚钱效应（梯队 %s · 昨龙可获利 %s）"
                % (nm, rec.get("ladder", ""), rec.get("lead", "")))
        return True, desc

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

    # ---------------- 4.5 上一空间龙头断板后大亏判定（v38 两战法共同前置） ----------------
    def dragon_loss(B):
        """最近一次空间龙头整批断板（suppressStocks 批次）至 B 的大亏原因列表。
        大亏 = 跌停 或 连续杀跌（断板日大长阴 / 连续两日跌 ≥6%）。空列表 = 无大亏。"""
        ev = None
        for x in days:                       # days 按日期升序
            if x["date"] > B:
                break
            if x.get("suppressStocks"):
                ev = x
        if not ev:
            return []
        reasons = []
        for s0 in ev.get("suppressStocks") or []:
            c, nm = str(s0.get("code") or ""), s0.get("name", "")
            bd = str(s0.get("breakDate") or "")
            if not c or not bd or bd > B:
                continue
            rec = KBARS.get(c) or {}
            bb, pv = rec.get(bd), prev_close(c, bd)
            if bb and pv:
                chg = bb[2] / pv - 1
                # 大长阴：收盘跌幅 ≥7%，或收阴且实体 ≥5%（须当日收跌）
                if chg <= -.07 or (chg < 0 and bb[2] < bb[1] and (bb[1] - bb[2]) / pv >= .05):
                    reasons.append("%s 断板日大长阴" % nm)
            i0, i1 = DIDX.get(bd), DIDX.get(B)
            if i0 is None or i1 is None:
                continue
            consec = 0
            for j in range(i0 + 1, i1 + 1):
                dd = dates[j]
                b2, p2 = rec.get(dd), prev_close(c, dd)
                if not b2 or not p2:
                    continue
                chg = b2[2] / p2 - 1
                if chg <= -(lim_of(c, dd) - .006):
                    reasons.append("%s %s 跌停" % (nm, dd))
                consec = consec + 1 if chg <= -.06 else 0
                if consec >= 2:
                    reasons.append("%s 连续杀跌（连两日跌≥6%%）" % nm)
                    consec = -999
        out = []
        for r in reasons:
            if r not in out:
                out.append(r)
        return out

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

        # ---------- 战法一 空间突破（v38：新增「断板后无大亏」前置）----------
        # ① 面对空间压制 N 板（N ≥ 2）；② 上一空间龙头断板后无大亏（跌停 / 连续杀跌）；
        # ③ 昨日（T）板块已有助攻股；④ 当日出现 N+1 板个股、盘中冲击涨停那一刻即买、
        #   按涨停价成交（炸板同样计入）。只在突破当日参与，不等到次日。
        if N >= 2:
            dloss = dragon_loss(B)
            if dloss:
                stats["break_skip"] += 1
            else:
                for c, e in sorted((daily.get(B) or {}).items()):
                    if (e.get("boards") or 0) != N + 1:
                        continue
                    th = theme_of(B, c)
                    # v39：所有非一字板空间突破一律纳入（含盘中触板后回落的炸板），
                    # 不再要求「昨日板块有助攻股」。一字买不进仍只进机会池。
                    why = ("突破买点 · 空间压制 %d 板 → 今日 %d 板盘中冲击涨停 · "
                           "上一空间龙头断板后无大亏 · 题材「%s」"
                           % (N, N + 1, th))
                    if buy_blocked(c, B, e):
                        stats["cand"] += 1
                        stats["yizi"] += 1
                        note_chance(B, "spaceDragon", e, c, th,
                                    why + " · 一字买不进（不参与模拟）",
                                    True, limit_px(c, B), False, "一字买不进")
                        continue
                    try_buy("spaceDragon", B, c, th, why, N + 1, check_open=False)

        # ---------- 战法二 试错龙头（v39）----------
        # ① 昨日（T）市场阶段为节点，且昨日该板块登「赚钱效应榜」并经实证验证；
        # ② 标的必须是「当日收盘封板」的板块最高板龙头（≥3 板）——
        #    炸板股只是盘中触板、收盘未封，是断板不是龙头连板，只进机会池观察、不买入；
        # ③ 3b 带 2b：板块内除标的以外仍有 ≥2 板封板跟风股（资金方向真实确认）；
        # ④ 赚钱效应延续：昨日首板今日连板，或昨日连板今日断板仍红盘，
        #    证据不得是标的自身（否则等于「龙头自己断板」自证延续）；
        # ⑤ 量能：买入日前一交易日（T）龙头成交量须放量或平量（相对 T−1），缩量不买；
        # ⑥ 3-5 板阶段首次放出近期最大成交量的龙头为最佳标的。
        if node_T:
            poolT, poolB = pool_map.get(T) or {}, pool_map.get(B) or {}
            dayB = daily.get(B) or {}
            for nm, arr in sorted((LF.get(B) or {}).items()):
                if not arr:
                    continue
                # 板块最高板只按「封板梯队」计算，炸板虚增的板位不参与
                lf_mx = max((x.get("boards") or 1) for x in arr)
                if lf_mx < 3:
                    continue
                # 前置：昨日该板块必须有赚钱效应（BOARD_EARN[(T,nm)].ok）
                ok_m, mdesc = board_money_ok(T, nm)
                if not ok_m:
                    continue
                # 板块今日的封板连板股（3b 带 2b 里的「2b」必须真实封住）
                lb_b = [x for x in arr if (x.get("boards") or 1) >= 2]
                # 只遍历封板梯队：炸板股不进战法二标的
                for x in arr:
                    c = str(x.get("code"))
                    e = dayB.get(c)
                    if not e:
                        e = {"code": c, "name": x.get("name"),
                             "boards": x.get("boards") or 1,
                             "sealed": True, "yizi": False, "time": x.get("t") or ""}
                    if not e.get("sealed"):
                        continue
                    if (e.get("boards") or 0) < 3 or (e.get("boards") or 0) < lf_mx:
                        continue
                    # ③ 3b 带 2b：除标的以外还有 ≥2 板封板跟风股
                    assist = [y for y in lb_b if str(y.get("code")) != c]
                    if not assist:
                        stats["board_skip"] += 1
                        continue
                    # ④ 赚钱效应延续（证据一律排除标的自身）
                    cont = ""
                    others = [y for y in arr if str(y.get("code")) != c]
                    y1b = [y for y in others
                           if (y.get("boards") or 1) == 2
                           and (poolT.get(str(y.get("code"))) or {}).get("boards") == 1]
                    if y1b:
                        cont = "昨日首板今日连板（%s）" % "、".join(
                            [str(y.get("name") or "") for y in y1b][:2])
                    else:
                        for y in ((LF.get(T) or {}).get(nm) or []):
                            cy = str(y.get("code"))
                            if cy == c or (y.get("boards") or 1) < 2 or cy in poolB:
                                continue
                            chg = chg_on(cy, B)
                            if chg is not None and chg > 0:
                                cont = ("昨日连板今日断板仍红盘（%s %+.1f%%）"
                                        % (y.get("name") or NAME.get(cy, ""), chg))
                                break
                    if not cont:
                        stats["cont_skip"] += 1
                        continue
                    # ⑤ 量能：买入日前一日（T）相对 T−1 必须放量或平量，缩量不买
                    vb, vt = vol_of(c, B), vol_of(c, T)
                    vt1 = vol_of(c, dates[i - 2]) if i >= 2 else 0
                    if not (vt and vt1 and vt >= vt1):
                        stats["shrink_skip"] += 1
                        continue
                    if is_oneword_up(c, B):
                        stats["yizi"] += 1
                        stats["cand"] += 1
                        note_chance(B, "trialLead", e, c, nm,
                                    "昨日「节点」· 板块「%s」最高 %d 板龙头 · 一字买不进（不参与模拟）"
                                    % (nm, lf_mx), True, limit_px(c, B), False, "一字买不进")
                        continue
                    why = ("昨日「节点」→ 今日板块「%s」最高 %d 板龙头（%d板带%d板）· "
                           "%s · 赚钱效应延续：%s · 前一日放量/平量 · 博弈成为新的空间龙头"
                           % (nm, lf_mx, lf_mx,
                              max((y.get("boards") or 1) for y in assist), mdesc, cont))
                    # 最佳标的：3-5 板阶段今日放出近 20 日最大成交量
                    if 3 <= (e.get("boards") or 0) <= 5:
                        v20 = [vol_of(c, dates[j]) for j in range(max(0, i - 19), i + 1)]
                        v20 = [v for v in v20 if v]
                        if vb and v20 and vb >= max(v20):
                            why += " · 最佳标的：3-5板放出近期最大量"
                    if buy_blocked(c, B, e):
                        stats["cand"] += 1
                        stats["yizi"] += 1
                        note_chance(B, "trialLead", e, c, nm,
                                    why + " · 一字买不进（不参与模拟）",
                                    True, limit_px(c, B), False, "一字买不进")
                        continue
                    try_buy("trialLead", B, c, nm, why, lf_mx)

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
    print("触发统计：候选 %d；一字买不进 %d；未涨停/未晋级 %d；"
          "断板大亏禁买 %d；昨日板块无赚钱效应 %d；板块无 2b 助攻 %d；延续证据不足 %d；"
          "前一日缩量不买 %d；高开条件不符 %d；无日K %d；连续一字剔除 %d；一字跌停顺延 %d 个股票日"
          % (stats["cand"], stats["yizi"], stats["no_limit"],
             stats["break_skip"], stats["money_skip"], stats["board_skip"],
             stats["cont_skip"], stats["shrink_skip"], stats["open_skip"],
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
        arr.sort(key=lambda x: (METHODS.index(x["m"]), -x["boards"], x["code"]))
        chances_out[d] = arr
    print("当日机会池：%d 个交易日 / %d 条机会（一字买不进 %d 条）" %
          (len(chances_out), sum(len(v) for v in chances_out.values()),
           sum(1 for v in chances_out.values() for x in v if x["yizi"])), flush=True)

    UNI = ("【统一口径 v39】① 时点：B 为买入日（当日），T=B−1 为复盘日（昨日）；"
           "本页日期为买入日。② 买入价一律以涨停价打板；盘中冲击涨停后回落炸板，"
           "也按涨停价模拟买入、纳入统计（战法一「突破失败」同样计入）。"
           "③ 一字涨停买不进，只进机会池、不纳入模拟。"
           "④ 战法一（空间突破）：面对空间压制 N 板（N ≥ 2）、上一空间龙头断板后无大亏；"
           "当日任意 N+1 板非一字股盘中冲击涨停那一刻即买，所有空间突破（含炸板失败）一律纳入，"
           "不设昨日助攻等其它门槛，只在突破当日参与。"
           "⑤ 战法二（试错龙头）：昨日市场为节点阶段，且昨日该板块已登赚钱效应榜并经验证 ok；"
           "标的必须是当日收盘封板的板块最高板龙头（≥3 板），炸板股只进机会池不买入；"
           "3b 带 2b（板块除标的以外仍有 ≥2 板封板跟风股）；板块有赚钱效应延续"
           "（昨日首板今日连板，或昨日连板今日断板但红盘，证据不得是标的自身）；"
           "买入日前一交易日（T）成交量须放量或平量（相对 T−1），缩量不买；"
           "3-5 板首次放出近期最大量的龙头为最佳标的；仍要求高开但不是涨停开盘。"
           "⑥ 卖出（统一）：次日仍涨停持仓，首个收盘未涨停日尾盘按收盘价卖出，"
           "盘中 −7% 无条件止损；遇一字跌停无换手卖不出，顺延至首个可成交日。"
           "⑦ 仅保留空间突破、试错龙头两战法与统一卖出规则，补涨战法已删除。")

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
            "shrinkSkip": stats["shrink_skip"],
            "contSkip": stats["cont_skip"],
            "byM": {"spaceDragon": stats["m1"], "trialLead": stats["m2"]},
        },
    }
