# -*- coding: utf-8 -*-
"""v27 组合搜索：在候选集上枚举门槛/禁入组合，按「期望 + 逐年稳健 + 样本量」选优。"""
import json, os, itertools
from collections import defaultdict

OUT = os.path.dirname(os.path.abspath(__file__))
C = json.load(open(os.path.join(OUT, "candidates_v27.json"), encoding="utf-8"))

def dedup(rows, cross=False):
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
    return {"n": len(p), "wr": len(w)/len(p)*100, "avg": sum(p)/len(p), "pl": aw/abs(al) if al else 0,
            "w5": sum(1 for x in p if x >= 5)/len(p)*100, "l10": sum(1 for x in p if x <= -10)/len(p)*100}

def negyears(rows):
    ys = []
    for y in sorted(set(r["d"][:4] for r in rows)):
        s = stat([r for r in rows if r["d"][:4] == y])
        ys.append((y, s["n"], s["avg"]))
    return sum(1 for _, _, a in ys if a < 0), ys

# ---- 候选条件库（全部 T 日可判定）----
def cond_zt100(r): return r["ztT"] >= 100              # 过热禁入
def cond_topdrop(r): return r["topDelta"] <= -4        # 空间崩塌禁入
def cond_gap_gt(r): return r["gap"] > 0                # 空间已突破（正面）
def cond_ztequal(r): return r["ztDelta"] == 0          # 涨停家数持平
def cond_cnt_lt4(r): return r["cntT"] < 4              # 板块未成势
def cond_ts_qiang(r): return r["theme_stage"] == "转弱"
def cond_ts_fanfu(r): return r["theme_stage"] == "反复"
def cond_b3(r): return r["boardsT"] == 3               # 板位门槛（战法一）
def cond_b4(r): return r["boardsT"] == 4
def cond_b5(r): return r["boardsT"] == 5
def cond_b_le5(r): return r["boardsT"] <= 5
def cond_b_le6(r): return r["boardsT"] <= 6
def cond_b_ge6(r): return r["boardsT"] >= 6
def cond_b_ge7(r): return r["boardsT"] >= 7
def cond_tt_wei(r): return (r["ttime"] or "") > "13:30" if r["ttime"] else False
def cond_leader(r): return r["isThemeLeadT"]
def cond_streak3(r): return r["streak"] >= 3
def cond_notleader(r): return not r["isThemeLeadT"]

LIB = {
    "禁:涨停≥100": cond_zt100, "禁:空间骤降4+": cond_topdrop,
    "禁:板块<4只": cond_cnt_lt4, "禁:题材转弱": cond_ts_qiang,
    "禁:题材反复": cond_ts_fanfu, "禁:尾盘封板": cond_tt_wei,
    "禁:非板块龙头": cond_notleader,
    "正:空间已突破": cond_gap_gt, "正:涨停数持平": cond_ztequal,
    "正:板位=3": cond_b3, "正:板位=4": cond_b4, "正:板位=5": cond_b5,
    "正:板位≤5": cond_b_le5, "正:板位≤6": cond_b_le6,
    "正:板位≥6": cond_b_ge6, "正:板位≥7": cond_b_ge7,
    "正:是板块龙头": cond_leader, "正:连榜≥3": cond_streak3,
}

METHODS = ("spaceDragon", "trialLead", "demoLead")

def evaluate(m, keep_fns, ban_fns, cross, min_n=40):
    rows = [r for r in C if r["method"] == m]
    for fn in ban_fns:
        rows = [r for r in rows if not fn(r)]
    for fn in keep_fns:
        rows = [r for r in rows if fn(r)]
    if not rows: return None
    rows = dedup(rows, cross)
    s = stat(rows)
    if not s or s["n"] < min_n: return None
    ny, ys = negyears(rows)
    return {"rows": rows, "s": s, "negyears": ny, "ys": ys}

def fmt(r):
    s = r["s"]
    return "n%4d 胜%5.1f%% 均%+6.2f%% 比%4.2f 赢5+:%4.1f%% 亏10-:%4.1f%% 负年%d %s" % (
        s["n"], s["wr"], s["avg"], s["pl"], s["w5"], s["l10"], r["negyears"],
        " ".join("%s:%d/%+.1f" % (y, n, a) for y, n, a in r["ys"]))

print("="*140)
print("各战法基线（核心条件 + 首次买入去重）")
print("="*140)
for m in METHODS:
    rows = dedup([r for r in C if r["method"] == m], False)
    s = stat(rows); ny, ys = negyears(rows)
    print("%-12s n%4d 胜%5.1f%% 均%+6.2f%% 比%4.2f 负年%d %s" % (
        m, s["n"], s["wr"], s["avg"], s["pl"], ny, " ".join("%s:%d/%+.1f"%(y,n,a) for y,n,a in ys)))

for m in METHODS:
    print("\n" + "="*140)
    print("▶ %s 条件搜索（单条件：正=门槛保留 / 禁=剔除）" % m)
    print("="*140)
    res = []
    for name, fn in LIB.items():
        for mode in ("keep", "ban"):
            if mode == "keep":
                r = evaluate(m, [fn], [], False)
            else:
                r = evaluate(m, [], [fn], False)
            if r: res.append((r["s"]["avg"], name, mode, r))
    res.sort(reverse=True)
    print("%-18s %-4s %s" % ("条件","方向","效果"))
    for avg, name, mode, r in res:
        print("%-18s %-4s %s" % (name, "保留" if mode=="keep" else "剔除", fmt(r)))

    print("\n--- 两两组合 Top12 ---")
    res2 = []
    for (n1, f1), (n2, f2) in itertools.combinations(LIB.items(), 2):
        for m1, m2 in (("keep","keep"), ("keep","ban"), ("ban","ban"), ("ban","keep")):
            keeps = [f1] if m1=="keep" else ([f2] if m2=="keep" else [])
            bans  = [f1] if m1=="ban"  else ([f2] if m2=="ban"  else [])
            r = evaluate(m, keeps, bans, False)
            if r and r["s"]["n"] >= 40:
                res2.append((r["s"]["avg"], r["s"]["n"], n1, m1, n2, m2, r))
    res2.sort(reverse=True)
    for avg, n, n1, m1, n2, m2, r in res2[:12]:
        print("%-16s%-3s + %-16s%-3s | %s" % (n1, "保留" if m1=="keep" else "剔除",
              n2, "保留" if m2=="keep" else "剔除", fmt(r)))
