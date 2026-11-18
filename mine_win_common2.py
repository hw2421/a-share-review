# -*- coding: utf-8 -*-
"""v27 挖掘 v2：语义化分档 + 先验可判定性验证 + 条件组合搜索。

关键问题：炸板是买入日 B 的后验属性，不能直接当"T日门槛"。必须验证：
是否存在 T 日可见的先验信号，能预测 B 日炸板/大亏。只有先验可判定的才能做禁入条件。
"""
import json, glob, os
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
    for r in rows:
        mrow[(d, r["theme"])] = r
def st_of(d): return stages.get(d) or {}
def lf(d, th): return (LF.get(d) or {}).get(th) or []
def zt_of(d):
    c = set()
    for arr in (LF.get(d) or {}).values():
        for x in arr: c.add(str(x.get("code")))
    return len(c)
def pc(code, d):
    rec = KBARS.get(code)
    if not rec or d not in rec: return None
    i = DIDX.get(d)
    if i is None: return None
    for j in range(i-1, -1, -1):
        b = rec.get(dates[j])
        if b and len(b) >= 3 and b[2]: return b[2]
    return None
def bar(code, d): return (KBARS.get(code) or {}).get(d)

def feat(t):
    B = t["d"]; i = DIDX[B]; T = dates[i-1]
    th = t.get("theme") or ""
    sT, sB = st_of(T), st_of(B)
    sTp = st_of(dates[i-2]) if i >= 2 else {}
    arrT, arrB = lf(T, th), lf(B, th)
    rB = mrow.get((B, th)) or {}
    ttime = ""
    for arr in (LF.get(T) or {}).values():
        for x in arr:
            if str(x.get("code")) == str(t["code"]): ttime = x.get("t") or ""
    topT = sT.get("spaceTop") or 0; supT = sT.get("suppress") or 0
    topP = sTp.get("spaceTop") or 0
    ztT = zt_of(T); ztP = zt_of(dates[i-2]) if i >= 2 else 0
    # T 日该股是否封板（先验可判定）
    code = str(t["code"])
    inT = code in {str(x.get("code")) for a in (LF.get(T) or {}).values() for x in a}
    # T 日该股涨幅/是否炸板过（T 日表现，先验）
    pvT = pc(code, T); bT = bar(code, T)
    chgT = round((bT[2]/pvT-1)*100, 2) if (bT and pvT) else None
    hiT = round((bT[4]/pvT-1)*100, 2) if (bT and pvT) else None
    # T 日该股是否为板块最高板
    boardsT = 0
    for arr in arrT:
        if str(x_code(arr)) if False else False: pass
    maxbT = max((x.get("boards") or 1) for x in arrT) if arrT else 0
    n2T = len([x for x in arrT if (x.get("boards") or 1) == 2])
    return {
        "method": t["method"], "pnl": t["pnl"], "B": B, "T": T, "code": code,
        "name": t.get("name"), "theme": th,
        "stT": sT.get("stage") or "", "stB": sB.get("stage") or "",
        "sup": supT, "top": topT,
        "gap": (topT - supT),
        "topDelta": topT - topP,
        "ztT": ztT, "ztDelta": ztT - ztP,
        "boards": t.get("boards") or 1,
        "burstB": bool(t.get("burst")), "ttime": ttime,
        "inT": inT, "chgT": chgT, "hiT": hiT,
        "ladderT": len([x for x in str(rB.get("ladder") or "").split("-") if x]),
        "countB": rB.get("count") or 0,
        "avg2": rB.get("avg2"), "theme_stage": rB.get("stage") or "",
        "streak": mstreak.get((T, th), 0),
        "maxbT": maxbT, "n2T": n2T,
        "is_spaceT": code in {str(x.get("code")) for x in (sT.get("spaceFull") or [])},
        "hold": t.get("hold") or 0, "reason": t.get("reason") or "",
    }

def x_code(a): return a.get("code")

RV = D["review"]
F = []
for m in ("spaceDragon", "trialLead"):
    for t in RV.get(m, []):
        f = feat(t); f["method"] = m; F.append(f)
for f in F:
    f["grp"] = "W" if f["pnl"] >= 5 else ("L" if f["pnl"] <= -10 else "M")

def sem_buckets(m):
    sub = [f for f in F if f["method"] == m]
    print("="*100)
    print("%s  共%d笔 | W(≥5%%)=%d  M=%d  L(≤-10%%)=%d | 整体胜率%.1f%% 笔均%+.2f%%"
          % (m, len(sub), sum(1 for f in sub if f["grp"]=="W"), sum(1 for f in sub if f["grp"]=="M"),
             sum(1 for f in sub if f["grp"]=="L"),
             sum(1 for f in sub if f["pnl"]>0)/len(sub)*100, sum(f["pnl"] for f in sub)/len(sub)))
    def show(name, fn):
        b = defaultdict(lambda: [0,0,0])
        for f in sub:
            k = fn(f)
            if k is None: continue
            g = b[k]
            g[0 if f["grp"]=="W" else (1 if f["grp"]=="M" else 2)] += 1
        print("\n  ▸ %s" % name)
        print("    %-22s %4s %4s %4s %8s %8s" % ("档","W","M","L","P(W|x)","P(L|x)"))
        for k in sorted(b, key=lambda x: -(b[x][0]+b[x][1]+b[x][2])):
            nW,nM,nL = b[k]; n = nW+nM+nL
            print("    %-22s %4d %4d %4d %7.1f%% %7.1f%%" % (str(k),nW,nM,nL,nW/n*100,nL/n*100))
    show("B日市场阶段", lambda f: f["stB"])
    show("空间关系(T)", lambda f: "已突破(顶>压)" if f["gap"]>0 else ("贴压制(顶=压)" if f["gap"]==0 else "顶<压"))
    show("空间高度变化", lambda f: "空间升1" if f["topDelta"]>0 else ("持平" if f["topDelta"]==0 else ("降1-3" if f["topDelta"]>=-3 else "降4+")))
    show("涨停家数变化", lambda f: "增" if f["ztDelta"]>0 else ("持平" if f["ztDelta"]==0 else "减"))
    show("T日涨停家数", lambda f: "≥100" if f["ztT"]>=100 else ("60-99" if f["ztT"]>=60 else ("30-59" if f["ztT"]>=30 else "<30")))
    show("龙头板位(B日)", lambda f: ("≤6板" if f["boards"]<=6 else ("7-8板" if f["boards"]<=8 else "9板+")))
    show("T日该股封板?", lambda f: "封板" if f["inT"] else "未在梯队")
    show("T日该股涨幅", lambda f: "涨停" if (f["chgT"] or 0)>=9.5 else ("高开冲高" if (f["hiT"] or 0)>=9.5 else "普通"))
    show("T日首封时间", lambda f: "早盘" if (f["ttime"] and f["ttime"]<="10:30") else ("盘中" if (f["ttime"] and f["ttime"]<="13:30") else ("尾盘" if f["ttime"] else "未知")))
    show("T日板块梯队档数", lambda f: "≥3档" if f["maxbT"]>=3 and len([1 for x in lf(f["T"], f["theme"])])>=3 else "2档")
    show("B日炸板", lambda f: "炸板" if f["burstB"] else "封板")
    show("题材连续登榜", lambda f: "≥3" if f["streak"]>=3 else ("1-2" if f["streak"]>=1 else "0"))
    show("板块涨停家数(B)", lambda f: "≥8" if f["countB"]>=8 else ("4-7" if f["countB"]>=4 else "<4"))
    show("2板+赚钱avg2", lambda f: "无" if f["avg2"] is None else ("正" if f["avg2"]>0 else "负/零"))
    show("持仓天数", lambda f: "1日" if f["hold"]<=1 else ("2-3日" if f["hold"]<=3 else "4日+"))

sem_buckets("spaceDragon")
sem_buckets("trialLead")

# ---- 先验可判定性：T 日特征能否预测 B 日炸板（战法一）----
print("\n" + "="*100)
print("先验可判定性验证：T 日可见信号 → 预测 B 日炸板 / 大亏")
for m in ("spaceDragon", "trialLead"):
    sub = [f for f in F if f["method"] == m]
    print("\n▶ %s" % m)
    for fname, fn in [
        ("T日首封尾盘", lambda f: (f["ttime"] or "") > "13:30" if f["ttime"] else False),
        ("T日首封盘中/尾盘", lambda f: (f["ttime"] or "") > "10:30" if f["ttime"] else False),
        ("T日板块涨停家数≤3", lambda f: f["countB"] <= 3),
        ("B日市场阶段=节点", lambda f: f["stB"] == "节点"),
        ("T日空间高度降4+", lambda f: f["topDelta"] <= -4),
        ("T日涨停家数≥100", lambda f: f["ztT"] >= 100),
        ("T日该股非封板", lambda f: not f["inT"]),
        ("T日该股涨幅<5%", lambda f: (f["chgT"] if f["chgT"] is not None else -99) < 5),
    ]:
        hit = [f for f in sub if fn(f)]
        if not hit: continue
        pb = sum(1 for f in hit if f["burstB"])/len(hit)
        pl = sum(1 for f in hit if f["grp"]=="L")/len(hit)
        allb = sum(1 for f in sub if f["burstB"])/len(sub)
        alll = sum(1 for f in sub if f["grp"]=="L")/len(sub)
        print("  %-22s 命中%3d/%3d | B日炸板率%5.1f%%(基准%5.1f%%) | 大亏率%5.1f%%(基准%5.1f%%)"
              % (fname, len(hit), len(sub), pb*100, allb*100, pl*100, alll*100))

json.dump(F, open(os.path.join(OUT, "mine_win_features.json"), "w", encoding="utf-8"), ensure_ascii=False)
print("\n特征已存 mine_win_features.json")
