# -*- coding: utf-8 -*-
"""v29 板块赚钱效应验证（共享模块）。

用户口径：三大战法都要求所买个股板块「有赚钱效应、有梯队」——
  ① 有梯队：当日题材梯队含 ≥3 板龙头 + ≥2 板助攻 + 首板；
  ② 昨日买入龙头的人今日可以获利卖出：昨日(T)题材最高板龙头，今日(B)盘中
     最高价相对 T 日收盘为正（有获利了结机会），且 B 日收盘跌幅 ≤5%；
  ③ 低吸：B 日以当日最低价买入题材内 2 板+ 跟风股、收盘卖出，平均收益 ≥ −5%；
  ④ 半路：B 日以 (开盘+最高)/2 买入、收盘卖出，平均收益 ≥ −5%；
  ⑤ 打板：B 日以涨停价买入、收盘卖出，平均收益 ≥ −5%。

数据源：题材炒作梯队 ladderFull（同花顺涨停池按题材归组） + 同花顺个股日K。
只评估「当日登上赚钱效应榜」（≥3 板个股再涨停 + 板块至少 3 只涨停）的题材日。
返回 {(B, theme): {...}}，供 strategies.py 作为三战法共同门槛、供前端展示。
"""
import os
import json
from collections import defaultdict

MIN_MONEY_STOCKS = 3
MAX_LOSS = -5.0          # 「至少没有大亏」阈值


def lim_of(code, name=""):
    if "ST" in (name or "").upper():
        return .05
    if code.startswith(("30", "688")):
        return .20
    if code.startswith(("8", "4", "92")):
        return .30
    return .10


def compute(dates, LF, MONEY, KBARS, NAME=None):
    """返回 {(B, theme): {ok, ladder, lead, low, mid, board, nFol, reason}}"""
    DIDX = {d: i for i, d in enumerate(dates)}
    NAME = NAME or {}

    def bar(c, d):
        return (KBARS.get(c) or {}).get(d)

    def prev_close(c, d):
        rec = KBARS.get(c)
        i = DIDX.get(d)
        if not rec or i is None:
            return None
        for j in range(i - 1, -1, -1):
            b = rec.get(dates[j])
            if b and len(b) >= 3 and b[2]:
                return b[2]
        return None

    out = {}
    for i in range(1, len(dates)):
        B, T = dates[i], dates[i - 1]
        for row in (MONEY.get(B) or []):
            nm = row.get("theme")
            if not nm:
                continue
            arr = (LF.get(B) or {}).get(nm) or []
            base = {"ok": False, "ladder": row.get("ladder") or "",
                    "lead": "", "low": None, "mid": None, "board": None,
                    "nFol": 0, "reason": ""}
            out[(B, nm)] = base
            if not arr:
                base["reason"] = "B 日无涨停梯队"
                continue
            boards = [(str(x.get("code")), x.get("name") or NAME.get(str(x.get("code")), ""),
                       x.get("boards") or 1) for x in arr]
            mx = max(b for _, _, b in boards)
            has2 = any(2 <= b < mx for _, _, b in boards)
            has_first = any(b == 1 for _, _, b in boards)
            if not (mx >= 3 and has2 and has_first):
                base["reason"] = "梯队不全（最高 %d 板 / 助攻 %s / 首板 %s）" % (
                    mx, "有" if has2 else "无", "有" if has_first else "无")
                continue

            # ② 昨日买龙头者今日可获利卖出
            tarr = (LF.get(T) or {}).get(nm) or []
            lead_ok, lead_desc = False, "无 T 日龙头且无龙头日K"
            if tarr:
                tl = max(tarr, key=lambda x: x.get("boards") or 1)
                c = str(tl.get("code"))
                bT, bB = bar(c, T), bar(c, B)
                if bT and bB and bT[2]:
                    prof = (bB[4] / bT[2] - 1) * 100
                    cl = (bB[2] / bT[2] - 1) * 100
                    lead_ok = prof > 0 and cl >= MAX_LOSS
                    lead_desc = "昨龙%s 今盘中最高%+.1f%% 收盘%+.1f%%" % (
                        tl.get("name") or c, prof, cl)
            if not lead_ok and not tarr:
                # 题材名跨日漂移兜底：直接用 B 日最高板龙头自身的 T→B 表现
                c = next((cc for cc, _, bb in boards if bb == mx), "")
                bT, bB = bar(c, T), bar(c, B)
                if bT and bB and bT[2]:
                    prof = (bB[4] / bT[2] - 1) * 100
                    cl = (bB[2] / bT[2] - 1) * 100
                    lead_ok = prof > 0 and cl >= MAX_LOSS
                    lead_desc = "龙头%s 昨收→今盘中最高%+.1f%% 收盘%+.1f%%" % (
                        NAME.get(c, c), prof, cl)
            base["lead"] = lead_desc
            if not lead_ok:
                base["reason"] = lead_desc
                continue

            # ③④⑤ 低吸 / 半路 / 打板做跟风（B 日题材内 2 板+ 非最高板个股）
            fol = [(c, n, b) for c, n, b in boards if 2 <= b < mx]
            if not fol:
                base["reason"] = "无 2 板+ 助攻跟风股"
                continue
            low_r, mid_r, board_r = [], [], []
            for c, n, b in fol:
                bB, pv = bar(c, B), prev_close(c, B)
                if not bB or not pv:
                    continue
                o, cl, lo, hi = bB[1], bB[2], bB[3], bB[4]
                lp = round(pv * (1 + lim_of(c, n)), 2)
                if lo > 0:
                    low_r.append((cl / lo - 1) * 100)
                mid = (o + hi) / 2
                if mid > 0:
                    mid_r.append((cl / mid - 1) * 100)
                if lp > 0:
                    board_r.append((cl / lp - 1) * 100)
            if not low_r:
                base["reason"] = "跟风股无日K"
                continue
            avg = lambda a: sum(a) / len(a)
            a, b, c = avg(low_r), avg(mid_r), avg(board_r)
            base.update({"low": round(a, 2), "mid": round(b, 2),
                         "board": round(c, 2), "nFol": len(fol)})
            if a >= MAX_LOSS and b >= MAX_LOSS and c >= MAX_LOSS:
                base["ok"] = True
            else:
                base["reason"] = "跟风买法亏超5%%（低吸%+.1f%% 半路%+.1f%% 打板%+.1f%%）" % (a, b, c)
    return out


def from_data_json(path=None):
    """独立验证脚本用：从 data.json + klines 分片加载并计算。"""
    import glob
    here = os.path.dirname(os.path.abspath(__file__))
    path = path or os.path.join(here, "data.json")
    D = json.load(open(path, encoding="utf-8"))
    KB = {}
    for fn in glob.glob(os.path.join(here, "klines", "klines_*.json")):
        for c, bars in json.load(open(fn, encoding="utf-8")).items():
            rec = KB.setdefault(str(c), {})
            for b in bars:
                if b and len(b) >= 5:
                    rec[b[0]] = b
    return compute(D["dates"], D["ladderFull"], D["money"], KB), D, KB
