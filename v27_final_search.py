# -*- coding: utf-8 -*-
"""v27 定稿搜索：年份切分防过拟合
   训练段 2021-2024 选条件 → 验证段 2025-2026 检查泛化。
   只用 T 日可判定条件；含「首次符合条件才买入」去重。
"""
import json, os, itertools
from collections import defaultdict

OUT = os.path.dirname(os.path.abspath(__file__))
C = json.load(open(os.path.join(OUT, "candidates_v27.json"), encoding="utf-8"))
TR = [r for r in C if r["d"][:4] <= "2024"]
TE = [r for r in C if r["d"][:4] >= "2025"]

def dedup(rows):
    seen = set(); out = []
    for r in sorted(rows, key=lambda x: (x["d"], x["code"])):
        k = "%s|%s|%s" % (r["method"], r["code"], r.get("cycle"))
        if k in seen: continue
        seen.add(k); out.append(r)
    return out

def stat(rows):
    p = [r["pnl"] for r in rows]
    if not p: return None
    w = [x for x in p if x > 0]; l = [x for x in p if x <= 0]
    aw = sum(w)/len(w) if w else 0; al = sum(l)/len(l) if l else 0
    return {"n": len(p), "wr": len(w)/len(p)*100, "avg": sum(p)/len(p),
            "pl": aw/abs(al) if al else 0, "w5": sum(1 for x in p if x >= 5)/len(p)*100,
            "l10": sum(1 for x in p if x <= -10)/len(p)*100}

def apply(rows, keeps, bans):
    for fn in bans: rows = [r for r in rows if not fn(r)]
    for fn in keeps: rows = [r for r in rows if fn(r)]
    return rows

def negyears(rows):
    ys = []
    for y in sorted(set(r["d"][:4] for r in rows)):
        s = stat([r for r in rows if r["d"][:4] == y])
        if s: ys.append((y, s["n"], s["avg"]))
    return sum(1 for _, _, a in ys if a < 0), ys

# ---------- 语义化条件库（T 日可判定）----------
def c_zt_ge(v):
    return lambda r: r["ztT"] >= v
def c_topdrop(v):
    return lambda r: r["topDelta"] <= -v
def c_boards(v):
    return lambda r: r["boardsT"] == v
def c_boards_le(v):
    return lambda r: r["boardsT"] <= v
def c_boards_ge(v):
    return lambda r: r["boardsT"] >= v
def c_sup(v):
    return lambda r: r["sup"] == v
def c_sup_le(v):
    return lambda r: 0 < r["sup"] <= v
def c_cnt_lt(v):
    return lambda r: r["cntT"] < v
def c_cnt_ge(v):
    return lambda r: r["cntT"] >= v
def c_theme(q):
    return lambda r: r["theme_stage"] == q
def c_wei():
    return lambda r: bool(r["ttime"]) and r["ttime"] > "13:30"
def c_streak_ge(v):
    return lambda r: r["streak"] >= v
def c_gap_gt():
    return lambda r: r["gap"] > 0
def c_ztdelta_pos():
    return lambda r: r["ztDelta"] > 0
def c_leader():
    return lambda r: r["isThemeLeadT"]

LIB = {
    "禁:涨停家数≥100": c_zt_ge(100), "禁:涨停家数≥80": c_zt_ge(80),
    "禁:空间骤降≥4": c_topdrop(4), "禁:空间骤降≥3": c_topdrop(3),
    "禁:板块涨停<4只": c_cnt_lt(4), "禁:板块涨停<5只": c_cnt_lt(5),
    "禁:题材转弱": c_theme("转弱"), "禁:题材反复": c_theme("反复"),
    "禁:尾盘封板": c_wei(), "禁:压制高度>6": c_sup(7),
    "正:板位=3": c_boards(3), "正:板位=4": c_boards(4), "正:板位=5": c_boards(5),
    "正:板位≤4": c_boards_le(4), "正:板位≤5": c_boards_le(5), "正:板位≤6": c_boards_le(6),
    "正:板位≥4": c_boards_ge(4), "正:板位≥5": c_boards_ge(5),
    "正:压制=4": c_sup(4), "正:压制=5": c_sup(5), "正:压制≤5": c_sup_le(5),
    "正:压制≤6": c_sup_le(6), "正:压制≥5": lambda r: r["sup"] >= 5,
    "正:板块≥5只": c_cnt_ge(5), "正:板块≥6只": c_cnt_ge(6),
    "正:连榜≥3": c_streak_ge(3), "正:空间已突破": c_gap_gt(),
    "正:涨停家数增加": c_ztdelta_pos(), "正:是板块龙头": c_leader(),
    "正:题材加强": c_theme("加强"), "正:题材启动": c_theme("启动"),
}

def evaluate(rows, keeps, bans, min_n=25):
    r = apply(rows, keeps, bans)
    if not r: return None
    r = dedup(r)
    s = stat(r)
    if not s or s["n"] < min_n: return None
    return {"rows": r, "s": s}

METHODS = ("spaceDragon", "trialLead")
for m in METHODS:
    print("="*150)
    print("▶ %s  单条件：训练段(2021-24) 与 验证段(2025-26) 分别评估（要求两段都改善）" % m)
    print("="*150)
    tr0 = [r for r in TR if r["method"] == m]
    te0 = [r for r in TE if r["method"] == m]
    b_tr, b_te = stat(dedup(tr0)), stat(dedup(te0))
    print("基线  训练 n%4d 胜%5.1f%% 均%+6.2f%% | 验证 n%4d 胜%5.1f%% 均%+6.2f%%"
          % (b_tr["n"], b_tr["wr"], b_tr["avg"], b_te["n"], b_te["wr"], b_te["avg"]))
    rows = []
    for name, fn in LIB.items():
        for mode in ("keep", "ban"):
            keeps = [fn] if mode == "keep" else []
            bans = [fn] if mode == "ban" else []
            a = evaluate(tr0, keeps, bans); b = evaluate(te0, keeps, bans)
            if not a or not b: continue
            dtr = a["s"]["avg"] - b_tr["avg"]; dte = b["s"]["avg"] - b_te["avg"]
            rows.append((dtr + dte, dtr, dte, name, mode, a, b))
    rows.sort(reverse=True)
    print("%-20s %-4s %-34s %-34s" % ("条件","方向","训练段(Δ均值)","验证段(Δ均值)"))
    for tot, dtr, dte, name, mode, a, b in rows:
        print("%-20s %-4s n%4d 均%+6.2f%%(%+.2f) 比%4.2f | n%4d 均%+6.2f%%(%+.2f) 比%4.2f"
              % (name, "保留" if mode=="keep" else "剔除",
                 a["s"]["n"], a["s"]["avg"], dtr, a["s"]["pl"],
                 b["s"]["n"], b["s"]["avg"], dte, b["s"]["pl"]))
