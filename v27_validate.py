# -*- coding: utf-8 -*-
"""v27 全量重跑验证器：从原始候选池重新生成三大战法逐笔，验证新门槛/禁入条件
   与「首次符合条件才买入（同一连板周期不重复加仓）」口径。

不复用旧逐笔做筛选（会过拟合），而是复刻 strategies.py 的候选生成 + 卖出模拟，
   对比 v26 基线 vs v27 新规则，输出笔数/胜率/笔均/盈亏比/逐年稳健性。
"""
import json, glob, os, re
from collections import defaultdict, Counter

OUT = os.path.dirname(os.path.abspath(__file__))
D = json.load(open(os.path.join(OUT, "data.json"), encoding="utf-8"))
dates = D["dates"]; DIDX = {d: i for i, d in enumerate(dates)}
stages = {x["date"]: x for x in D["stages"]}
money = D["money"]; LF = D["ladderFull"]
KBARS = {}
for fn in glob.glob(os.path.join(OUT, "klines", "klines_*.json")):
    for c, bars in json.load(open(fn, encoding="utf-8")).items():
        rec = KBARS.setdefault(str(c), {})
        for b in bars:
            if b and len(b) >= 5: rec[b[0]] = b
CLOSE = {c: {d: b[2] for d, b in rec.items()} for c, rec in KBARS.items()}

# 涨停池（真实收盘涨停）
pool = {}
for d, ths in LF.items():
    m = {}
    for nm, arr in ths.items():
        for x in arr:
            m.setdefault(str(x.get("code")), {"name": x.get("name", ""),
                          "boards": x.get("boards") or 1, "theme": nm})
    pool[d] = m
NAME = {}
for d, m in pool.items():
    for c, s in m.items(): NAME[c] = s.get("name", "")

def lim(c):
    nm = NAME.get(c, "")
    if "ST" in nm.upper(): return .05
    if c.startswith(("30", "688")): return .20
    if c.startswith(("8", "4", "92")): return .30
    return .10
def bar(c, d): return (KBARS.get(c) or {}).get(d)
def pc(c, d):
    rec = KBARS.get(c)
    if not rec or d not in rec: return None
    i = DIDX.get(d)
    if i is None: return None
    for j in range(i-1, -1, -1):
        b = rec.get(dates[j])
        if b and len(b) >= 3 and b[2]: return b[2]
    return None
def touch(c, d):
    b, p = bar(c, d), pc(c, d)
    return bool(b and p and len(b) >= 5 and b[4]/p - 1 >= lim(c)-.006)
def yz(c, d):
    b, p = bar(c, d), pc(c, d)
    return bool(b and p and len(b) >= 5 and b[1]==b[2]==b[3]==b[4] and b[2]/p-1 >= lim(c)-.006)
def yzdn(c, d):
    b, p = bar(c, d), pc(c, d)
    return bool(b and p and len(b) >= 5 and b[1]==b[2]==b[3]==b[4] and b[2]/p-1 <= -(lim(c)-.006))
def consec_yz(c, d):
    """连续一字涨停且涨停开盘 → 全程无换手、买不进，不属任何战法标的"""
    b, p = bar(c, d), pc(c, d)
    if not (b and p and len(b) >= 5 and b[1]==b[2]==b[3]==b[4] and b[2]/p-1 >= lim(c)-.006):
        return False
    j = DIDX[d]; cnt = 0
    while j >= 0:
        bb, pp = bar(c, dates[j]), pc(c, dates[j])
        if not (bb and pp and len(bb) >= 5 and bb[1]==bb[2]==bb[3]==bb[4] and bb[2]/pp-1 >= lim(c)-.006):
            break
        cnt += 1
        if cnt >= 2: return True
        j -= 1
    return False

def exsim(c, B):
    for j in range(DIDX[B]+1, len(dates)):
        t = dates[j]; b, p = bar(c, t), pc(c, t)
        if not b or not p: continue
        if yzdn(c, t): continue
        if b[3] and b[3] <= p*.93: return round(p*.93, 2), t, "盘中-7%止损", j-DIDX[B]
        if c in pool.get(t, {}): continue
        return b[2], t, "尾盘未涨停", j-DIDX[B]
    tail = [(x, (KBARS.get(c) or {})[x][2]) for x in sorted(KBARS.get(c) or {}) if x > B and (KBARS.get(c) or {})[x][2]]
    return (tail[-1][1], tail[-1][0], "期末仍涨停·按末收盘", len(tail)) if tail else None

# 连板周期：用于「首次符合条件才买入」——同股同一轮连板只买第一次
CYCLE = {}
for c, runs in (D.get("stockBreak") or {}).items():
    for idx, r in enumerate(runs):
        CYCLE[(str(c), r[0])] = r[1]      # 周期首日 -> 周期末日

def cycle_key(c, d):
    """该股在 d 日所属的连板周期首日（不在连板中则返回 None）"""
    for (cc, s), e in CYCLE.items():
        if cc == str(c) and s <= d <= e:
            return s
    return None

# 题材连续登赚钱榜
mstreak = {}
for d in dates:
    for r in money.get(d, []):
        nm = r.get("theme")
        if not nm: continue
        j, s = DIDX[d], 0
        while j >= 0 and any(x.get("theme") == nm for x in money.get(dates[j], [])):
            s += 1; j -= 1
        mstreak[(d, nm)] = s
mrow = {}
for d, rows in money.items():
    for r in rows: mrow[(d, r["theme"])] = r

def zt_of(d):
    c = set()
    for arr in (LF.get(d) or {}).values():
        for x in arr: c.add(str(x.get("code")))
    return len(c)
def st_of(d): return stages.get(d) or {}
def lf(d, th): return (LF.get(d) or {}).get(th) or []
def c2t(d):
    m = {}
    for nm, arr in (LF.get(d) or {}).items():
        for x in arr: m.setdefault(str(x.get("code")), nm)
    return m

# 炸板候选（盘中触板收盘未封）
burst = {}
allcodes = set(pool.keys()) and {c for m in pool.values() for c in m}
for c in allcodes:
    rec = KBARS.get(c)
    if not rec: continue
    lm = lim(c)
    for d in dates:
        if c in pool.get(d, {}): continue
        b = rec.get(d)
        if not b or len(b) < 5: continue
        p = pc(c, d)
        if not p: continue
        if b[4]/p - 1 < lm - .006: continue
        if b[2]/p - 1 >= lm - .006: continue
        i = DIDX[d]
        pb = pool.get(dates[i-1], {}).get(c) if i > 0 else None
        boards = ((pb or {}).get("boards") or 1) + 1 if pb else 1
        burst.setdefault(d, {})[c] = {"name": NAME.get(c, ""), "boards": boards, "sealed": False}
print("炸板补充 %d 个股票日" % sum(len(v) for v in burst.values()))

def daily_cand(d):
    m = {}
    for c, s in pool.get(d, {}).items():
        m[c] = dict(s, sealed=True)
    for c, s in burst.get(d, {}).items():
        m.setdefault(c, s)
    return m

def board_effect_ok(B, theme):
    """板块效应：复盘日 ≥3板龙头 + ≥1只2板梯队"""
    i = DIDX.get(B)
    if i is None or i == 0: return False
    arr = lf(dates[i-1], theme)
    if not arr: return False
    if max((x.get("boards") or 1) for x in arr) < 3: return False
    return any((x.get("boards") or 1) == 2 for x in arr)

def stat(rows, tag=""):
    p = [r["pnl"] for r in rows]
    if not p: print("%s 无样本" % tag); return
    w = [x for x in p if x > 0]; l = [x for x in p if x <= 0]
    aw = sum(w)/len(w) if w else 0; al = sum(l)/len(l) if l else 0
    print("%-26s 笔%4d 胜%5.1f%% 笔均%+7.2f%% 盈亏比%5.2f 期望%+7.2f%% 最大%+6.1f%% 最小%+.1f%%"
          % (tag, len(p), len(w)/len(p)*100, sum(p)/len(p),
             aw/abs(al) if al else 0, sum(p)/len(p), max(p), min(p)))

def run(v27, only=None):
    """v27=True 用新门槛/禁入条件；False 用 v26 原条件"""
    trades = {"spaceDragon": [], "trialLead": [], "demoLead": []}
    bought = set()          # (method, code, 周期首日) 首次买入去重
    for i in range(1, len(dates)):
        B, T = dates[i], dates[i-1]
        sT, sB = st_of(T), st_of(B)
        stT, stB = sT.get("stage") or "", sB.get("stage") or ""
        topT, supT = sT.get("spaceTop") or 0, sT.get("suppress") or 0
        space_codes = {str(x.get("code")) for x in (sT.get("spaceFull") or [])}
        cand = daily_cand(B)
        t2t = c2t(T)

        # ---- 共用：禁入条件（先验可判定，T日可见）----
        ztT = zt_of(T)
        topP = st_of(dates[i-2]).get("spaceTop") or 0 if i >= 2 else 0
        topDelta = topT - topP
        ban_reason = None
        if v27:
            if ztT >= 100:
                ban_reason = "T日涨停家数≥100（过热，见顶风险）"
            elif topDelta <= -4:
                ban_reason = "T日空间高度骤降%d板（空间崩塌）" % abs(topDelta)

        # ---- 战法一 空间突破 ----
        if supT >= 2 and topT == supT:
            for s in (sT.get("spaceFull") or []):
                code = str(s.get("code") or "")
                if not code: continue
                th = t2t.get(code) or ""
                if not th: continue
                if v27 and ban_reason:
                    continue
                if not board_effect_ok(B, th): continue
                e = cand.get(code)
                if not e: continue
                if not (e.get("sealed") or touch(code, B)): continue
                if yz(code, B) or consec_yz(code, B): continue
                boards = e.get("boards") or 0
                if boards < supT + 1: continue
                if v27:
                    # 门槛：B日须突破阶段（不得在节点日追突破）
                    if stB != "突破": continue
                    # 门槛：龙头板位 ≤6 板（高位接力胜率差）
                    if boards > 6: continue
                    # 门槛：板块 2 板梯队当日要赚钱（真实赚钱示范）
                    arr = lf(T, th)
                    mids = [x for x in arr if (x.get("boards") or 1) == 2]
                    earn = False
                    for x in mids:
                        cc = str(x.get("code"))
                        chg = None
                        cr = CLOSE.get(cc); pp = pc(cc, B)
                        if cr and pp and B in cr and cr[B]: chg = round((cr[B]/pp-1)*100, 2)
                        if cc in (cand.get(B) or {}) and (cand[B][cc].get("sealed")): earn = True; break
                        if chg is not None and chg > 0: earn = True; break
                    if not earn: continue
                ck = cycle_key(code, B)
                key = ("spaceDragon", code, ck)
                if key in bought: continue
                bought.add(key)
                b = bar(code, B)
                buy = b[2] if e.get("sealed") else round(pc(code, B)*(1+lim(code)), 2)
                out = exsim(code, B)
                if not out: continue
                sell, sd, rs, hold = out
                trades["spaceDragon"].append({
                    "d": B, "code": code, "name": NAME.get(code, ""), "theme": th,
                    "stage": stB, "boards": boards, "burst": not e.get("sealed"),
                    "buy": round(buy, 2), "sell": round(sell, 2), "sellD": sd, "hold": hold,
                    "reason": rs, "pnl": round((sell-buy)/buy*100, 2), "sealed": bool(e.get("sealed"))})

        # ---- 战法二 试错龙头 ----
        if stT == "节点":
            groups = {}
            for code, e in cand.items():
                th = t2t.get(code) or ""
                if not th: continue
                groups.setdefault(th, []).append((code, e))
            for th, items in groups.items():
                lbB = max((e.get("boards") or 1) for _, e in items)
                if lbB < 3: continue
                if not board_effect_ok(B, th): continue
                if v27 and ban_reason: continue
                for code, e in [x for x in items if (x[1].get("boards") or 1) == lbB]:
                    if not (e.get("sealed") or touch(code, B)): continue
                    if yz(code, B) or consec_yz(code, B): continue
                    if v27:
                        # 门槛：板块涨停家数 ≥4（板块效应要成势）
                        cntB = len(lf(B, th))
                        if cntB < 4: continue
                        # 门槛：2 板梯队当日赚钱（真实示范）
                        arr = lf(T, th)
                        mids = [x for x in arr if (x.get("boards") or 1) == 2]
                        earn = False
                        for x in mids:
                            cc = str(x.get("code"))
                            chg = None
                            cr = CLOSE.get(cc); pp = pc(cc, B)
                            if cr and pp and B in cr and cr[B]: chg = round((cr[B]/pp-1)*100, 2)
                            if cc in (cand.get(B) or {}) and (cand[B][cc].get("sealed")): earn = True; break
                            if chg is not None and chg > 0: earn = True; break
                        if not earn: continue
                    ck = cycle_key(code, B)
                    key = ("trialLead", code, ck)
                    if key in bought: continue
                    bought.add(key)
                    b = bar(code, B)
                    buy = b[2] if e.get("sealed") else round(pc(code, B)*(1+lim(code)), 2)
                    out = exsim(code, B)
                    if not out: continue
                    sell, sd, rs, hold = out
                    trades["trialLead"].append({
                        "d": B, "code": code, "name": NAME.get(code, ""), "theme": th,
                        "stage": stB, "boards": lbB, "burst": not e.get("sealed"),
                        "buy": round(buy, 2), "sell": round(sell, 2), "sellD": sd, "hold": hold,
                        "reason": rs, "pnl": round((sell-buy)/buy*100, 2), "sealed": bool(e.get("sealed"))})

        # ---- 战法三 示范龙头 ----
        if stB in ("主升", "突破"):
            for s in (sT.get("spaceFull") or []):
                code = str(s.get("code") or "")
                if not code: continue
                th = t2t.get(code) or ""
                if not th: continue
                if mstreak.get((T, th), 0) < 3: continue
                if v27 and ban_reason: continue
                e = cand.get(code)
                if not e: continue
                if not (e.get("sealed") or touch(code, B)): continue
                if yz(code, B) or consec_yz(code, B): continue
                ck = cycle_key(code, B)
                key = ("demoLead", code, ck)
                if key in bought: continue
                bought.add(key)
                b = bar(code, B)
                buy = b[2] if e.get("sealed") else round(pc(code, B)*(1+lim(code)), 2)
                out = exsim(code, B)
                if not out: continue
                sell, sd, rs, hold = out
                trades["demoLead"].append({
                    "d": B, "code": code, "name": NAME.get(code, ""), "theme": th,
                    "stage": stB, "boards": e.get("boards") or 0, "burst": not e.get("sealed"),
                    "buy": round(buy, 2), "sell": round(sell, 2), "sellD": sd, "hold": hold,
                    "reason": rs, "pnl": round((sell-buy)/buy*100, 2), "sealed": bool(e.get("sealed"))})
    return trades

print("\n" + "="*112)
print("A. v26 基线（原条件 + 无首次买入限制）")
print("="*112)
base = run(False)
for m in ("spaceDragon", "trialLead", "demoLead"):
    stat(base[m], m)
tot = sum(len(v) for v in base.values())
stat([t for v in base.values() for t in v], "三战法合计")

print("\n" + "="*112)
print("B. v27 新规则（新门槛 + 禁入条件 + 首次买入不重复加仓）")
print("="*112)
new = run(True)
for m in ("spaceDragon", "trialLead", "demoLead"):
    stat(new[m], m)
tot2 = sum(len(v) for v in new.values())
stat([t for v in new.values() for t in v], "三战法合计")

print("\n" + "="*112)
print("C. v27 逐年稳健性")
print("="*112)
for m in ("spaceDragon", "trialLead", "demoLead"):
    print("▶ %s" % m)
    for y in sorted(set(t["d"][:4] for t in new[m])):
        stat([t for t in new[m] if t["d"][:4] == y], "  " + y)

json.dump(new, open(os.path.join(OUT, "v27_trades.json"), "w", encoding="utf-8"), ensure_ascii=False)
