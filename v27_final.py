# -*- coding: utf-8 -*-
"""v27 最终组合验证：定稿三战法条件，输出全期/逐年统计。"""
import json, os
from collections import Counter

OUT = os.path.dirname(os.path.abspath(__file__))
C = json.load(open(os.path.join(OUT, "candidates_v27.json"), encoding="utf-8"))

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

def show(rows, tag):
    s = stat(rows)
    if not s: print("%-30s 无样本" % tag); return None
    ys = []
    for y in sorted(set(r["d"][:4] for r in rows)):
        ss = stat([r for r in rows if r["d"][:4] == y])
        ys.append((y, ss["n"], ss["avg"]))
    print("%-30s n%4d 胜%5.1f%% 均%+6.2f%% 比%4.2f 赢5+:%4.1f%% 亏10-:%4.1f%% 负年%d/6"
          % (tag, s["n"], s["wr"], s["avg"], s["pl"], s["w5"], s["l10"],
             sum(1 for _, _, a in ys if a < 0)))
    return s

# ===== 定稿条件 =====
# 战法一 空间突破（核心：T日空间龙头板数 == 空间压制高度 → B日冲压制+1板）
SD_KEEP = [lambda r: r["boardsT"] <= 5]                       # 门槛：龙头板位 ≤5 板
SD_BAN  = [lambda r: r["ztT"] >= 80,                          # 禁入：T日涨停家数≥80（过热见顶）
           lambda r: bool(r["ttime"]) and r["ttime"] > "13:30"]  # 禁入：T日尾盘才封板（弱强度）
# 战法二 试错龙头（核心：T日节点 → B日新崛起板块龙头≥3板）
TL_KEEP = [lambda r: r["sup"] == 5,                           # 门槛：空间压制高度=5板
           lambda r: r["theme_stage"] == "加强"]              # 门槛：题材阶段加强
TL_BAN  = [lambda r: r["topDelta"] <= -3]                     # 禁入：空间高度骤降≥3板（空间崩塌）
# 战法三 示范龙头（核心：T日空间龙头 + 题材连续≥3日登赚钱榜 → B日主升/突破阶段涨停）
DL_KEEP = [lambda r: r["gap"] > 0]                           # 门槛：T日空间已突破压制
DL_BAN  = [lambda r: r["theme_stage"] == "转弱"]              # 禁入：题材已转弱

def pick(m, keeps, bans):
    rows = [r for r in C if r["method"] == m]
    for fn in bans: rows = [r for r in rows if not fn(r)]
    for fn in keeps: rows = [r for r in rows if fn(r)]
    return dedup(rows)

CFG = {"spaceDragon": (SD_KEEP, SD_BAN), "trialLead": (TL_KEEP, TL_BAN),
       "demoLead": (DL_KEEP, DL_BAN)}

print("="*140)
print("v27 定稿：条件 + 首次买入去重")
print("="*140)
final = {}
for m in ("spaceDragon", "trialLead", "demoLead"):
    k, b = CFG[m]
    base = dedup([r for r in C if r["method"] == m])
    rows = pick(m, k, b)
    final[m] = rows
    print("▶ %s" % m)
    show(base, "  v26核心条件")
    show(rows, "  v27定稿")
    print()

allv = [r for v in final.values() for r in v]
allb = [r for v in (dedup([r for r in C if r["method"] == m]) for m in CFG) for r in v]
print("="*140)
show(allb, "三战法合计(v26核心)")
show(allv, "三战法合计(v27定稿)")
print()
print("去重后同一(战法,股票,连板周期)仅一笔 —— 校验：",
      len(allv) == len(set((r["method"], r["code"], r.get("cycle")) for r in allv)))
print("跨战法同股同日仍各自记录（各战法独立模拟）：",
      sum(1 for d, cs in __import__("collections").defaultdict(list).items()) )
dups = Counter((r["d"], r["code"]) for r in allv)
print("同日同股跨战法并列笔数：", sum(1 for v in dups.values() if v > 1))
print()
print("=== v27 定稿 逐年 ===")
for m in ("spaceDragon", "trialLead", "demoLead"):
    print("▶ %s" % m)
    for y in sorted(set(r["d"][:4] for r in final[m])):
        show([r for r in final[m] if r["d"][:4] == y], "  " + y)
print()
print("=== 大赚/大亏结构对比 ===")
for m in ("spaceDragon", "trialLead", "demoLead"):
    b = dedup([r for r in C if r["method"] == m])
    f = final[m]
    sb, sf = stat(b), stat(f)
    print("%-12s 核心: 赢≥5%% %4.1f%% 亏≤-10%% %4.1f%% | 定稿: 赢≥5%% %4.1f%% 亏≤-10%% %4.1f%%"
          % (m, sb["w5"], sb["l10"], sf["w5"], sf["l10"]))

json.dump(final, open(os.path.join(OUT, "v27_final.json"), "w", encoding="utf-8"), ensure_ascii=False)
print("\n已存 v27_final.json")
