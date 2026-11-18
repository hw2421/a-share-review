# -*- coding: utf-8 -*-
"""v27 条件搜索：在候选集上，用「T 日可判定特征」搜索门槛(来自大赚共性)与
   禁入(来自大亏共性)，并叠加「首次符合条件才买入」去重，输出各战法最优组合。
判据：胜率、笔均、盈亏比、逐年稳健、样本量下限。"""
import json, os, itertools
from collections import defaultdict, Counter

OUT = os.path.dirname(os.path.abspath(__file__))
C = json.load(open(os.path.join(OUT, "candidates_v27.json"), encoding="utf-8"))

def first_dedup(rows, cross=False):
    """首次符合条件才买入：同股同一轮连板周期内只买第一次（不加仓）。
       cross=True 时三战法共用一把锁（同一账户不加仓同一标的）。"""
    seen = set(); out = []
    for r in sorted(rows, key=lambda x: (x["d"], x["code"])):
        ck = r.get("cycle")
        k = ("ALL|%s|%s" % (r["code"], ck)) if cross else ("%s|%s|%s" % (r["method"], r["code"], ck))
        if k in seen: continue
        seen.add(k); out.append(r)
    return out

def stat(rows):
    p = [r["pnl"] for r in rows]
    if not p: return None
    w = [x for x in p if x > 0]; l = [x for x in p if x <= 0]
    aw = sum(w)/len(w) if w else 0; al = sum(l)/len(l) if l else 0
    return {"n": len(p), "wr": len(w)/len(p)*100, "avg": sum(p)/len(p),
            "pl": aw/abs(al) if al else 0, "exp": sum(p)/len(p),
            "w5": sum(1 for x in p if x >= 5)/len(p)*100,
            "l10": sum(1 for x in p if x <= -10)/len(p)*100}

def show(rows, tag):
    s = stat(rows)
    if not s: print("%-40s 无样本" % tag); return None
    ys = []
    for y in sorted(set(r["d"][:4] for r in rows)):
        ss = stat([r for r in rows if r["d"][:4] == y])
        ys.append((y, ss["n"], ss["avg"]))
    neg = sum(1 for _, _, a in ys if a < 0)
    print("%-40s n%4d 胜%5.1f%% 均%+6.2f%% 比%4.2f 赢≥5%:%4.1f%% 亏≤10%:%4.1f%% | 负年%d/6 %s"
          % (tag, s["n"], s["wr"], s["avg"], s["pl"], s["w5"], s["l10"], neg,
             " ".join("%s:%d/%+.1f" % (y, n, a) for y, n, a in ys)))
    return s

# ---------- 特征分档：大赚/大亏共性（T日可见）----------
def bucket_profile(method, feat, buckets):
    rows = [r for r in C if r["method"] == method]
    print("\n▶ %s · %s" % (method, feat))
    b = defaultdict(lambda: [0, 0, 0])
    for r in rows:
        k = buckets(r)
        if k is None: continue
        g = b[k]
        if r["pnl"] >= 5: g[0] += 1
        elif r["pnl"] <= -10: g[2] += 1
        else: g[1] += 1
    print("  %-22s %4s %4s %4s %8s %8s %8s" % ("档","W","M","L","P(W|x)","P(L|x)","均%"))
    for k in sorted(b, key=lambda x: -(b[x][0]+b[x][1]+b[x][2])):
        nW, nM, nL = b[k]; n = nW+nM+nL
        sub = [r for r in rows if buckets(r) == k]
        avg = sum(r["pnl"] for r in sub)/n if n else 0
        print("  %-22s %4d %4d %4d %7.1f%% %7.1f%% %7.2f%%"
              % (str(k), nW, nM, nL, nW/n*100, nL/n*100, avg))

for m in ("spaceDragon", "trialLead", "demoLead"):
    bucket_profile(m, "T日市场阶段", lambda r: r["stT"])
    bucket_profile(m, "T日涨停家数", lambda r: "≥100" if r["ztT"]>=100 else ("60-99" if r["ztT"]>=60 else ("30-59" if r["ztT"]>=30 else "<30")))
    bucket_profile(m, "T日涨停家数变化", lambda r: "增" if r["ztDelta"]>0 else ("持平" if r["ztDelta"]==0 else "减"))
    bucket_profile(m, "空间高度变化", lambda r: "升" if r["topDelta"]>0 else ("持平" if r["topDelta"]==0 else ("降1-3" if r["topDelta"]>=-3 else "降4+")))
    bucket_profile(m, "空间顶-压制", lambda r: "顶>压" if r["gap"]>0 else ("顶=压" if r["gap"]==0 else "顶<压"))
    bucket_profile(m, "T日该股板位", lambda r: "%d板"%min(r["boardsT"],12))
    bucket_profile(m, "T日是否为板块龙头", lambda r: "是龙头" if r["isThemeLeadT"] else "非龙头")
    bucket_profile(m, "T日2板梯队avg2", lambda r: "无" if r["earn2"] is None else ("正" if r["earn2"]>0 else "负/零"))
    bucket_profile(m, "T日首封时间", lambda r: "早盘" if (r["ttime"] and r["ttime"]<="10:30") else ("盘中" if (r["ttime"] and r["ttime"]<="13:30") else ("尾盘" if r["ttime"] else "未知")))
    bucket_profile(m, "T日板块涨停家数", lambda r: "≥8" if r["cntT"]>=8 else ("4-7" if r["cntT"]>=4 else "<4"))
    bucket_profile(m, "T日板块梯队档数", lambda r: "%d档"%min(r["ladderT"],5))
    bucket_profile(m, "题材连续登榜", lambda r: "≥3" if r["streak"]>=3 else ("1-2" if r["streak"]>=1 else "0"))
    bucket_profile(m, "T日题材阶段", lambda r: r["theme_stage"] or "无")
    bucket_profile(m, "T日断板龙头数", lambda r: "有断板" if r["supStocks"]>0 else "无")
