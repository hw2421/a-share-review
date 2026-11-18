# -*- coding: utf-8 -*-
"""v27 挖掘：空间突破 / 试错龙头「单笔盈利≥5%」共性 → 门槛条件；
   「单笔亏损≤-10%」共性 → 禁止参与条件。全部只用 T/B 日已知信息，无未来函数。
   再叠加「首次符合条件才买入、不重复加仓」口径评估。"""
import json, glob, os
from collections import defaultdict, Counter

OUT = os.path.dirname(os.path.abspath(__file__))
D = json.load(open(os.path.join(OUT, "data.json"), encoding="utf-8"))
dates = D["dates"]; DIDX = {d: i for i, d in enumerate(dates)}
stages = {x["date"]: x for x in D["stages"]}
money = D["money"]; LF = D["ladderFull"]

# 题材连续登赚钱榜天数
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
    """当日涨停家数（梯队浮层去重计数）"""
    c = set()
    for arr in (LF.get(d) or {}).values():
        for x in arr: c.add(str(x.get("code")))
    return len(c)

def feat(t):
    B = t["d"]; i = DIDX[B]; T = dates[i-1]
    th = t.get("theme") or ""
    sT, sB = st_of(T), st_of(B)
    sTp = st_of(dates[i-2]) if i >= 2 else {}
    arrT, arrB = lf(T, th), lf(B, th)
    rB = mrow.get((B, th)) or {}
    # 龙头首封时间（T 日该股）
    ttime = ""
    for arr in (LF.get(T) or {}).values():
        for x in arr:
            if str(x.get("code")) == str(t["code"]): ttime = x.get("t") or ""
    topT = sT.get("spaceTop") or 0
    supT = sT.get("suppress") or 0
    topP = sTp.get("spaceTop") or 0
    ztT = zt_of(T); ztB = zt_of(B)
    ztP = zt_of(dates[i-2]) if i >= 2 else 0
    n2T = len([x for x in arrT if (x.get("boards") or 1) == 2])
    maxbT = max((x.get("boards") or 1) for x in arrT) if arrT else 0
    return {
        "method": t["method"], "pnl": t["pnl"], "B": B, "T": T, "code": str(t["code"]),
        "name": t.get("name"), "theme": th,
        "stT": sT.get("stage") or "", "stB": sB.get("stage") or "",
        "sup": supT, "top": topT, "topPrev": topP,
        "supGap": topT - supT, "topDelta": topT - topP,
        "ztT": ztT, "ztB": ztB, "ztDelta": ztT - ztP,
        "boards": t.get("boards") or 1,
        "burst": bool(t.get("burst")), "ttime": ttime,
        "ladderT": len([x for x in str(rB.get("ladder") or "").split("-") if x]),
        "countB": rB.get("count") or 0,
        "avg2": rB.get("avg2"), "theme_stage": rB.get("stage") or "",
        "streak": mstreak.get((T, th), 0),
        "maxbT": maxbT, "n2T": n2T,
        "is_spaceT": str(t["code"]) in {str(x.get("code")) for x in (sT.get("spaceFull") or [])},
        "hold": t.get("hold") or 0,
        "reason": t.get("reason") or "",
    }

RV = D["review"]
F = []
for m in ("spaceDragon", "trialLead"):
    for t in RV.get(m, []):
        f = feat(t); f["method"] = m; F.append(f)
for f in F:
    f["grp"] = "W" if f["pnl"] >= 5 else ("L" if f["pnl"] <= -10 else "M")

for m in ("spaceDragon", "trialLead"):
    sub = [f for f in F if f["method"] == m]
    print("="*104)
    print("%s：共 %d 笔 | 大赚W(≥5%%) %d | 中间M %d | 大亏L(≤-10%%) %d"
          % (m, len(sub), sum(1 for f in sub if f["grp"]=="W"),
             sum(1 for f in sub if f["grp"]=="M"), sum(1 for f in sub if f["grp"]=="L")))
    print("  整体 胜率%.1f%% 笔均%+.2f%%" % (
        sum(1 for f in sub if f["pnl"]>0)/len(sub)*100, sum(f["pnl"] for f in sub)/len(sub)))

    def bucket(name, fn):
        b = defaultdict(lambda: [0,0,0])   # key -> [nW, nM, nL]
        for f in sub:
            k = fn(f)
            if k is None: continue
            g = b[k]
            if f["grp"]=="W": g[0]+=1
            elif f["grp"]=="M": g[1]+=1
            else: g[2]+=1
        print("\n  ▸ %s" % name)
        print("    %-20s %5s %5s %5s %8s %8s" % ("分档","W","M","L","P(W|x)","P(L|x)"))
        for k in sorted(b, key=lambda x: -sum(b[x])):
            nW,nM,nL = b[k]; n = nW+nM+nL
            print("    %-20s %5d %5d %5d %7.1f%% %7.1f%%"
                  % (str(k), nW, nM, nL, nW/n*100, nL/n*100))

    bucket("T日市场阶段", lambda f: f["stT"])
    bucket("B日市场阶段", lambda f: f["stB"])
    bucket("空间顶-压制差", lambda f: "顶=压" if f["supGap"]==0 else ("顶>压" if f["supGap"]>0 else "顶<压"))
    bucket("空间高度变化(T vs T-1)", lambda f: "持平" if f["topDelta"]==0 else ("升%d"%f["topDelta"] if f["topDelta"]>0 else "降%d"%abs(f["topDelta"])))
    bucket("涨停家数变化(T vs T-1)", lambda f: "持平" if f["ztDelta"]==0 else ("增%d"%f["ztDelta"] if f["ztDelta"]>0 else "减%d"%abs(f["ztDelta"])))
    bucket("T日涨停家数分档", lambda f: "≥100" if f["ztT"]>=100 else ("60-99" if f["ztT"]>=60 else ("30-59" if f["ztT"]>=30 else "<30")))
    bucket("龙头板位(B日)", lambda f: "%d板"%min(f["boards"],12))
    bucket("买日是否炸板", lambda f: "炸板" if f["burst"] else "封板")
    bucket("龙头T日首封时间", lambda f: ("早盘" if f["ttime"] and f["ttime"]<="10:30" else ("盘中" if f["ttime"] and f["ttime"]<="13:30" else ("尾盘" if f["ttime"] else "未知"))))
    bucket("板块梯队档数", lambda f: "%d档"%min(f["ladderT"],5))
    bucket("板块涨停家数(B)", lambda f: "≥8" if f["countB"]>=8 else "%d"%f["countB"])
    bucket("题材阶段(B)", lambda f: f["theme_stage"] or "无")
    bucket("2板+当日赚钱avg2", lambda f: "无" if f["avg2"] is None else ("正" if f["avg2"]>0 else "负/零"))
    bucket("题材连续登榜", lambda f: "≥3" if f["streak"]>=3 else str(f["streak"]))
    bucket("T日是否空间龙头", lambda f: "是" if f["is_spaceT"] else "否")
    bucket("卖出原因", lambda f: f["reason"].split("（")[0])
    bucket("持仓天数", lambda f: "%d日"%min(f["hold"],8))
    print()

json.dump(F, open(os.path.join(OUT, "mine_win_features.json"), "w", encoding="utf-8"), ensure_ascii=False)
print("特征样本已存 mine_win_features.json")
