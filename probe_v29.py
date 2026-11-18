# -*- coding: utf-8 -*-
"""v29 新三战法候选规模探查（不改主代码，先验证门槛不会砍光样本）。"""
import json, glob, os
from collections import defaultdict
OUT = os.path.dirname(os.path.abspath(__file__))
D = json.load(open(os.path.join(OUT, "data.json"), encoding="utf-8"))
dates = D["dates"]; DIDX = {d: i for i, d in enumerate(dates)}
stages = {x["date"]: x for x in D["stages"]}
LF = D["ladderFull"]; MONEY = D["money"]
KB = {}
for fn in glob.glob(os.path.join(OUT, "klines", "klines_*.json")):
    for c, bars in json.load(open(fn, encoding="utf-8")).items():
        rec = KB.setdefault(str(c), {})
        for b in bars:
            if b and len(b) >= 5: rec[b[0]] = b


def lim(code, name=""):
    if "ST" in (name or "").upper(): return .05
    if code.startswith(("30", "688")): return .20
    if code.startswith(("8", "4", "92")): return .30
    return .10


def bar(c, d): return (KB.get(c) or {}).get(d)


def prev_close(c, d):
    rec = KB.get(c); i = DIDX.get(d)
    if not rec or i is None: return None
    for j in range(i - 1, -1, -1):
        b = rec.get(dates[j])
        if b and b[2]: return b[2]
    return None


def high_open(c, d, name=""):
    """高开但不是涨停开盘：开盘 > 昨收 且 开盘 < 涨停价。"""
    b, pv = bar(c, d), prev_close(c, d)
    if not b or not pv: return False
    return b[1] > pv * 1.0001 and b[1] < round(pv * (1 + lim(c, name)), 2) - .001


def open_at_limit(c, d, name=""):
    b, pv = bar(c, d), prev_close(c, d)
    if not b or not pv: return False
    return b[1] / pv - 1 >= lim(c, name) - .006


def touch_limit(c, d, name=""):
    """盘中冲击涨停（含封板/炸板）：B 日最高价达涨停价。"""
    b, pv = bar(c, d), prev_close(c, d)
    if not b or not pv: return False
    return b[4] / pv - 1 >= lim(c, name) - .006


def is_yizi(c, d, name=""):
    """一字涨停：四价合一 + 涨停（买不进）。"""
    b, pv = bar(c, d), prev_close(c, d)
    if not b or not pv: return False
    return (b[1] == b[2] == b[3] == b[4]
            and b[2] / pv - 1 >= lim(c, name) - .006)


def prev_break_ok(T):
    """上一个空间龙头断板日没有大长阴杀跌、断板日次日没有大跌（跌停）。"""
    st = stages.get(T) or {}
    lst = st.get("suppressStocks") or []
    if not lst: return True, "无压制股记录"
    for s in lst:
        c, nm, bd = str(s["code"]), s.get("name", ""), str(s.get("breakDate") or "")
        if not bd or bd > T: continue
        b, pv = bar(c, bd), prev_close(c, bd)
        if not b or not pv: continue
        chg = b[2] / pv - 1
        # 大长阴杀跌：收盘大跌 ≤-7% 或 实体大阴（开>收 且 实体≥5%）
        entity = (b[1] - b[2]) / pv
        if chg <= -.07 or (b[2] < b[1] and entity >= .05):
            return False, "断板日%s大长阴(%.1f%%)" % (nm, chg * 100)
        # 次日不能跌停
        i = DIDX.get(bd)
        if i is None or i + 1 >= len(dates): continue
        d2 = dates[i + 1]
        if d2 > T: continue          # 次日信息须在复盘日 T 可见
        b2, pv2 = bar(c, d2), prev_close(c, d2)
        if not b2 or not pv2: continue
        chg2 = b2[2] / pv2 - 1
        if chg2 <= -(lim(c, nm) - .006):
            return False, "断板次日%s跌停(%.1f%%)" % (nm, chg2 * 100)
    return True, "压制股断板温和"


EFF = {(x["d"], x["theme"]): x["ok"]
       for x in json.load(open(os.path.join(OUT, "money_effect_v29.json"), encoding="utf-8"))}


def board_ok(B, nm):
    """板块赚钱效应：登赚钱效应榜 + 梯队有助攻 + 通过四类实证验证。"""
    if not any(r["theme"] == nm for r in MONEY.get(B, [])):
        return False
    arr = (LF.get(B) or {}).get(nm) or []
    if not arr: return False
    mx = max((x.get("boards") or 1) for x in arr)
    if mx < 3: return False
    if not any(2 <= (x.get("boards") or 1) < mx for x in arr): return False
    return bool(EFF.get((B, nm), False))


def themes_of(B):
    return {r["theme"]: r for r in MONEY.get(B, [])}


def ladder(B, nm):
    return [(str(x["code"]), x.get("name") or "", x.get("boards") or 1)
            for x in ((LF.get(B) or {}).get(nm) or [])]


cnt = defaultdict(int); samples = defaultdict(list)
for i in range(1, len(dates)):
    B, T = dates[i], dates[i - 1]
    stT = stages.get(T) or {}
    N = stT.get("suppress") or 0
    node = (stT.get("stage") or "") == "节点"
    bok, _ = prev_break_ok(T)
    ths = themes_of(B)
    for nm in ths:
        if not board_ok(B, nm): continue
        arr = ladder(B, nm)
        mx = max((b for _, _, b in arr), default=0)
        leads = [(c, n, b) for c, n, b in arr if b == mx]
        # 战法一：N+1 板龙头冲击涨停
        if bok and N >= 2:
            for c, n, b in arr:
                if b == N + 1 and touch_limit(c, B, n):
                    cnt["sd_raw"] += 1
                    if high_open(c, B, n) and not is_yizi(c, B, n):
                        cnt["sd_buy"] += 1
                        if len(samples["sd"]) < 6: samples["sd"].append((B, nm, n, b))
                    elif is_yizi(c, B, n) or open_at_limit(c, B, n):
                        # 补涨触发：龙头符合战法一但一字
                        fol = [(cc, nn, bb) for cc, nn, bb in arr if 2 <= bb < mx]
                        for cc, nn, bb in fol:
                            if touch_limit(cc, B, nn) and high_open(cc, B, nn) \
                                    and not is_yizi(cc, B, nn):
                                cnt["fill_sd"] += 1
                                if len(samples["fill"]) < 6:
                                    samples["fill"].append((B, nm, n, nn, bb))
        # 战法二：节点次日，板块龙头 ≥3 板
        if node:
            for c, n, b in leads:
                if b >= 3 and touch_limit(c, B, n):
                    cnt["tl_raw"] += 1
                    if high_open(c, B, n) and not is_yizi(c, B, n):
                        cnt["tl_buy"] += 1
                        if len(samples["tl"]) < 6: samples["tl"].append((B, nm, n, b))
                    elif is_yizi(c, B, n) or open_at_limit(c, B, n):
                        fol = [(cc, nn, bb) for cc, nn, bb in arr if 2 <= bb < mx]
                        for cc, nn, bb in fol:
                            if touch_limit(cc, B, nn) and high_open(cc, B, nn) \
                                    and not is_yizi(cc, B, nn):
                                cnt["fill_tl"] += 1
                                if len(samples["fill"]) < 6:
                                    samples["fill"].append((B, nm, n, nn, bb))

print("=== 候选规模（B 日板块赚钱效应门槛后）===")
for k in ("sd_raw", "sd_buy", "tl_raw", "tl_buy", "fill_sd", "fill_tl"):
    print("  %-10s %d" % (k, cnt[k]))
print("\n样例 spaceDragon:", samples["sd"][:5])
print("样例 trialLead:", samples["tl"][:5])
print("样例 fillUp:", samples["fill"][:5])
