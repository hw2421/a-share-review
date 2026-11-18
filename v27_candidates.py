# -*- coding: utf-8 -*-
"""v27 特征工程（严格 T 日可判定）：
  1) 枚举三大战法在「核心条件」下的全部候选（T 日复盘可见 + B 日打板）
  2) 为每个候选提取 T 日可见特征 + 实际 pnl（用于拟合门槛/禁入条件）
输出 candidates_v27.json，供组合搜索使用。
注意：B日阶段、B日炸板等均为后验信息，不能作为门槛条件（未来函数）。
"""
import json, glob, os
from collections import defaultdict

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

CYCLE = {}
for c, runs in (D.get("stockBreak") or {}).items():
    for r in runs: CYCLE[(str(c), r[0])] = r[1]
def cycle_key(c, d):
    for (cc, s), e in CYCLE.items():
        if cc == str(c) and s <= d <= e: return s
    return None

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
def board_effect_ok(B, theme):
    i = DIDX.get(B)
    if i is None or i == 0: return False
    arr = lf(dates[i-1], theme)
    if not arr: return False
    if max((x.get("boards") or 1) for x in arr) < 3: return False
    return any((x.get("boards") or 1) == 2 for x in arr)

# 炸板候选
burst = {}
allcodes = {c for m in pool.values() for c in m}
for c in allcodes:
    rec = KBARS.get(c)
    if not rec: continue
    lm = lim(c)
    for d in dates:
        if c in pool.get(d, {}): continue
        b = rec.get(d)
        if not b or len(b) < 5: continue
        p = pc(c, d)
        if not p or b[4]/p - 1 < lm - .006 or b[2]/p - 1 >= lm - .006: continue
        i = DIDX[d]
        pb = pool.get(dates[i-1], {}).get(c) if i > 0 else None
        burst.setdefault(d, {})[c] = {"name": NAME.get(c, ""),
            "boards": ((pb or {}).get("boards") or 1) + 1 if pb else 1, "sealed": False}
def daily_cand(d):
    m = {}
    for c, s in pool.get(d, {}).items(): m[c] = dict(s, sealed=True)
    for c, s in burst.get(d, {}).items(): m.setdefault(c, s)
    return m

def feats_T(T, B, code, th, method):
    """T 日可见特征（严格无未来函数）"""
    sT = st_of(T); sTp = st_of(dates[DIDX[T]-1]) if DIDX[T] >= 1 else {}
    arrT = lf(T, th)
    # T 日该股在板块内的板位与是否板块龙头
    boardsT = 0
    for x in arrT:
        if str(x.get("code")) == str(code): boardsT = x.get("boards") or 0
    maxbT = max((x.get("boards") or 1) for x in arrT) if arrT else 0
    isThemeLeadT = (boardsT == maxbT and boardsT > 0)
    # 2 板梯队 T 日是否赚钱（真实赚钱示范）
    mids = [x for x in arrT if (x.get("boards") or 1) == 2]
    earn2 = None
    if mids:
        chgs = []
        for x in mids:
            cc = str(x.get("code"))
            cr = CLOSE.get(cc); pv = pc(cc, T)
            if cr and pv and T in cr and cr[T]:
                chgs.append(round((cr[T]/pv - 1) * 100, 2))
        earn2 = (sum(chgs)/len(chgs)) if chgs else None
    # T 日该股首封时间
    ttime = ""
    for arr in (LF.get(T) or {}).values():
        for x in arr:
            if str(x.get("code")) == str(code): ttime = x.get("t") or ""
    ztT = zt_of(T)
    ztP = zt_of(dates[DIDX[T]-1]) if DIDX[T] >= 1 else 0
    topT = sT.get("spaceTop") or 0; supT = sT.get("suppress") or 0
    topP = sTp.get("spaceTop") or 0
    return {
        "stT": sT.get("stage") or "",
        "ztT": ztT, "ztDelta": ztT - ztP,
        "top": topT, "sup": supT, "gap": topT - supT,
        "topDelta": topT - topP,
        "boardsT": boardsT, "maxbT": maxbT, "isThemeLeadT": isThemeLeadT,
        "earn2": earn2,
        "ttime": ttime,
        "ladderT": len(arrT),
        "cntT": len(arrT),
        "streak": mstreak.get((T, th), 0),
        "theme_stage": (mrow.get((T, th)) or {}).get("stage") or "",
        "is_spaceT": str(code) in {str(x.get("code")) for x in (sT.get("spaceFull") or [])},
        "supStocks": len(sT.get("suppressStocks") or []),
    }

cands = []
for i in range(1, len(dates)):
    B, T = dates[i], dates[i-1]
    sT = st_of(T); stT = sT.get("stage") or ""
    topT, supT = sT.get("spaceTop") or 0, sT.get("suppress") or 0
    cand = daily_cand(B); t2t = c2t(T)

    def mk(method, code, th, boardsB, why_extra=""):
        e = cand.get(code)
        if not e: return
        if not (e.get("sealed") or touch(code, B)): return
        if yz(code, B) or consec_yz(code, B): return
        b = bar(code, B)
        buy = b[2] if e.get("sealed") else round(pc(code, B)*(1+lim(code)), 2)
        out = exsim(code, B)
        if not out: return
        sell, sd, rs, hold = out
        row = {"method": method, "d": B, "T": T, "code": str(code), "name": NAME.get(code, ""),
               "theme": th, "boardsB": boardsB, "burst": not e.get("sealed"),
               "buy": round(buy, 2), "sell": round(sell, 2), "sellD": sd, "hold": hold,
               "reason": rs, "pnl": round((sell-buy)/buy*100, 2),
               "cycle": cycle_key(code, B) or ("%s#%s" % (code, B))}
        row.update(feats_T(T, B, code, th, method))
        cands.append(row)

    # 战法一 核心：T 日空间龙头板数 == 空间压制高度
    if supT >= 2 and topT == supT:
        for s in (sT.get("spaceFull") or []):
            code = str(s.get("code") or "")
            if not code: continue
            th = t2t.get(code) or ""
            if not th or not board_effect_ok(B, th): continue
            e = cand.get(code)
            boardsB = (e or {}).get("boards") or 0
            if boardsB < supT + 1: continue
            mk("spaceDragon", code, th, boardsB)
    # 战法二 核心：T 日节点 → B 日板块龙头（≥3板）
    if stT == "节点":
        groups = {}
        for code, e in cand.items():
            th = t2t.get(code) or ""
            if not th: continue
            groups.setdefault(th, []).append((code, e))
        for th, items in groups.items():
            lbB = max((e.get("boards") or 1) for _, e in items)
            if lbB < 3 or not board_effect_ok(B, th): continue
            for code, e in [x for x in items if (x[1].get("boards") or 1) == lbB]:
                mk("trialLead", code, th, lbB)
    # 战法三 核心：T 日空间龙头 + 题材连续≥3日登榜 + B 日阶段主升/突破
    stage_B = st_of(B).get("stage") or ""
    if stage_B in ("主升", "突破"):
        for s in (sT.get("spaceFull") or []):
            code = str(s.get("code") or "")
            if not code: continue
            th = t2t.get(code) or ""
            if not th or mstreak.get((T, th), 0) < 3: continue
            e = cand.get(code)
            mk("demoLead", code, th, (e or {}).get("boards") or 0)

json.dump(cands, open(os.path.join(OUT, "candidates_v27.json"), "w", encoding="utf-8"), ensure_ascii=False)
print("候选总数 %d" % len(cands))
from collections import Counter
print(Counter(c["method"] for c in cands))
for m in ("spaceDragon", "trialLead", "demoLead"):
    p = [c["pnl"] for c in cands if c["method"] == m]
    if p:
        w = [x for x in p if x > 0]
        print("%-12s %4d笔 胜%5.1f%% 均%+6.2f%%" % (m, len(p), len(w)/len(p)*100, sum(p)/len(p)))
