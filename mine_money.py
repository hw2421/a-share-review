# -*- coding: utf-8 -*-
"""实证挖掘：全期赚钱效应模块龙头 → T日复盘/T+1打板 → 三维度分组共性统计。

维度：
  A 市场阶段(stage)：节点/补涨/突破/主升
  B 市场空间股情况：spaceTop、suppress、spaceTop相对压制关系、是否空间龙头本身
  C 板块梯队(赚钱示范持续性)：maxB、题材阶段、ladder梯队档数、avg2(2板+赚钱)、
     连续出现天数(题材/龙头连续涨停)、龙头是否连板晋级
输出每笔按统一卖出规则的收益，再分组统计笔数/胜率/笔均/盈亏比/期望。
"""
import json, glob, os, re
from collections import defaultdict, Counter

OUT = os.path.dirname(os.path.abspath(__file__))
D = json.load(open(os.path.join(OUT, "data.json"), encoding="utf-8"))
dates = D["dates"]; DIDX = {d: i for i, d in enumerate(dates)}
stages = {x["date"]: x for x in D["stages"]}
money = D["money"]
ladder_full = D["ladderFull"]

# 日K
KBARS = {}
for fn in glob.glob(os.path.join(OUT, "klines", "klines_*.json")):
    for c, bars in json.load(open(fn, encoding="utf-8")).items():
        rec = KBARS.setdefault(str(c), {})
        for b in bars:
            if b and len(b) >= 5:
                rec[b[0]] = b
CLOSE = {c: {d: b[2] for d, b in rec.items()} for c, rec in KBARS.items()}

# 涨停池（code->info）由 ladderFull 重建：题材浮层同源、当日真实收盘涨停
pool = {}
for d, themes in ladder_full.items():
    m = {}
    for nm, arr in themes.items():
        for x in arr:
            m.setdefault(str(x.get("code")), {"name": x.get("name", ""),
                          "boards": x.get("boards") or 1, "theme": nm})
    pool[d] = m

NAME = {}
for d, m in pool.items():
    for c, s in m.items():
        NAME[c] = s.get("name", "")

def lim_of(code):
    nm = NAME.get(code, "")
    if "ST" in nm.upper(): return .05
    if code.startswith(("30", "688")): return .20
    if code.startswith(("8", "4", "92")): return .30
    return .10

def bar_of(code, d): return (KBARS.get(code) or {}).get(d)

def prev_close(code, d):
    rec = KBARS.get(code)
    if not rec or d not in rec: return None
    i = DIDX.get(d)
    if i is None: return None
    for j in range(i - 1, -1, -1):
        b = rec.get(dates[j])
        if b and len(b) >= 3 and b[2]: return b[2]
    return None

def chg_on(code, d):
    rec = CLOSE.get(code)
    if not rec or d not in rec or not rec[d]: return None
    pv = prev_close(code, d)
    return round((rec[d]/pv - 1)*100, 2) if pv else None

def touch_on(code, d):
    b, pv = bar_of(code, d), prev_close(code, d)
    if not b or len(b) < 5 or not pv: return False
    return b[4]/pv - 1 >= lim_of(code) - .006

def is_oneword_up(code, d):
    b, pv = bar_of(code, d), prev_close(code, d)
    if not b or len(b) < 5 or not pv: return False
    return (b[1] == b[2] == b[3] == b[4] and b[2]/pv - 1 >= lim_of(code) - .006)

def is_oneword_down(code, d):
    b, pv = bar_of(code, d), prev_close(code, d)
    if not b or len(b) < 5 or not pv: return False
    return (b[1] == b[2] == b[3] == b[4] and b[2]/pv - 1 <= -(lim_of(code) - .006))

STOP = .07
def simulate_exit(code, B):
    """B日买入后统一卖出：盘中-7%止损/首日未涨停尾盘卖/一字跌停顺延。"""
    i = DIDX[B]
    deferred = 0
    for j in range(i + 1, len(dates)):
        t = dates[j]
        b, pv = bar_of(code, t), prev_close(code, t)
        if not b or not pv: continue
        if is_oneword_down(code, t):
            deferred += 1; continue
        if b[3] and b[3] <= pv * (1 - STOP):
            return round(pv*(1-STOP), 2), t, "止损", j - i
        if code in pool.get(t, {}):
            continue
        return b[2], t, "尾盘", j - i
    tail = [(x, (KBARS.get(code) or {})[x][2]) for x in sorted(KBARS.get(code) or {})
            if x > B and (KBARS.get(code) or {})[x][2]]
    if not tail: return None
    return tail[-1][1], tail[-1][0], "期末", len(tail)

def pnl_of(buy, sell): return round((sell - buy)/buy*100, 2)

# 题材连续出现天数（在 money 榜上连续）
theme_streak = {}
for d in dates:
    for r in money.get(d, []):
        nm = r["theme"]
        # 往前数连续在 money 出现的天数
        k = DIDX[d]; s = 1
        j = k - 1
        seen = set(x["theme"] for x in money.get(dates[j], [])) if j >= 0 else set()
        while j >= 0 and nm in seen:
            s += 1; j -= 1
            seen = set(x["theme"] for x in money.get(dates[j], [])) if j >= 0 else set()
        theme_streak[(d, nm)] = s

# 龙头连续涨停天数（连板高度）
def board_of(code, d): return (pool.get(d, {}).get(code) or {}).get("boards") or 0

# ---- 逐样本构造 ----
samples = []
for T in dates:
    i = DIDX[T]
    if i + 1 >= len(dates): break
    B = dates[i + 1]
    stT = stages.get(T, {}) or {}
    stB = stages.get(B, {}) or {}
    topT = stT.get("spaceTop") or 0
    supT = stT.get("suppress") or 0
    space_codes = set(str(s.get("code")) for s in (stT.get("spaceFull") or []))
    for r in money.get(T, []):
        code = str(r.get("leadCode") or "")
        nm = r["theme"]
        if not code: continue
        # T日龙头板数、是否空间龙头、T日该股是否涨停
        bT = board_of(code, T)
        is_space = code in space_codes
        # 空间关系（T日）
        if topT and supT:
            if topT > supT: rel = "突破(顶>压)"
            elif topT == supT: rel = "贴压制(顶=压)"
            else: rel = "顶<压(压制上方未到)"
        elif topT and not supT: rel = "无压制(顶>0压0)"
        else: rel = "无空间"
        # 梯队档数
        ladder_n = len([x for x in str(r.get("ladder") or "").split("-") if x])
        # 连续出现天数
        streak = theme_streak.get((T, nm), 1)
        # B日打板买入
        e = pool.get(B, {}).get(code)
        sealed_B = bool(e)
        touch = sealed_B or touch_on(code, B)
        yizi = bool(e and is_oneword_up(code, B)) or is_oneword_up(code, B)
        row = {
            "T": T, "B": B, "code": code, "name": NAME.get(code, ""), "theme": nm,
            "stage": stT.get("stage") or "未知", "stageB": stB.get("stage") or "未知",
            "topT": topT, "supT": supT, "rel": rel, "is_space": is_space,
            "maxB": r.get("maxB") or 0, "count": r.get("count") or 0,
            "theme_stage": r.get("stage") or "", "avg2": r.get("avg2"),
            "ladder_n": ladder_n, "streak": streak, "bT": bT,
            "touch": touch, "sealed_B": sealed_B, "yizi": yizi,
            "hasK": bool(bar_of(code, B)),
        }
        # 逐笔收益（T+1打板）
        if not touch:
            row["pnl"] = None; row["why_no"] = "T+1未涨停"; row["valid"] = False
        elif yizi:
            row["pnl"] = None; row["why_no"] = "一字买不进"; row["valid"] = False
        else:
            b = bar_of(code, B); pv = prev_close(code, B)
            buy = b[2] if sealed_B else round(pv*(1+lim_of(code)), 2)
            out = simulate_exit(code, B)
            if not out:
                row["pnl"] = None; row["why_no"] = "无卖出日K"; row["valid"] = False
            else:
                sell, sd, rs, hold = out
                row.update(pnl=pnl_of(buy, sell), buy=buy, sell=sell,
                           sellD=sd, hold=hold, reason=rs, valid=True)
        samples.append(row)

json.dump(samples, open(os.path.join(OUT, "mine_money_samples.json"), "w", encoding="utf-8"),
          ensure_ascii=False)

# ---- 统计函数 ----
def stat(rows):
    ok = [r for r in rows if r.get("valid") and r.get("pnl") is not None]
    if not ok:
        return (0, 0, 0, 0, 0)
    p = [r["pnl"] for r in ok]
    win = [x for x in p if x > 0]; lose = [x for x in p if x <= 0]
    winrate = len(win)/len(p)*100
    avg = sum(p)/len(p)
    avgw = sum(win)/len(win) if win else 0
    avgl = sum(lose)/len(lose) if lose else 0
    pl = (avgw/abs(avgl)) if avgl else 0
    return (len(samples) and len(rows), len(p), round(winrate, 1),
            round(avg, 2), round(pl, 2))

def show(title, keyfn, rows=None):
    rows = rows if rows is not None else samples
    g = defaultdict(list)
    for r in rows:
        g[keyfn(r)].append(r)
    print("\n### " + title)
    print("%-26s %7s %7s %8s %9s %8s" % ("分组", "样本", "有效笔", "胜率%", "笔均%", "盈亏比"))
    for k in sorted(g, key=lambda x: -len(g[x])):
        n, m, w, a, pl = stat(g[k])
        print("%-26s %7d %7d %8.1f %9.2f %8.2f" % (str(k), n, m, w, a, pl))

valid = [r for r in samples if r.get("valid")]
n, m, w, a, pl = stat(samples)
print("样本总数 %d | 有效逐笔 %d | 整体胜率 %.1f%% | 笔均 %.2f%% | 盈亏比 %.2f" % (len(samples), m, w, a, pl))
print("未成立原因：", Counter(r.get("why_no") for r in samples if not r.get("valid")))

show("A. 市场阶段(T日)", lambda r: "阶段·"+r["stage"], valid)
show("A2. 买入日阶段(B)", lambda r: "阶段·"+r["stageB"], valid)
show("B. 空间关系(T日)", lambda r: "空间·"+r["rel"], valid)
show("B2. 龙头是否空间龙头(T)", lambda r: "空间·%s" % ("是空间龙头" if r["is_space"] else "非空间"), valid)
show("B3. 空间高度topT分档", lambda r: "高度·%d板" % min(r["topT"], 10), valid)
show("C. 板块题材阶段", lambda r: "题阶·"+(r["theme_stage"] or "无"), valid)
show("C2. 梯队档数", lambda r: "梯队·%d档" % min(r["ladder_n"], 5), valid)
show("C3. 龙头T日板数", lambda r: "板位·%d板" % min(r["bT"], 10), valid)
show("C4. 题材连续出现天数", lambda r: "连续·%s" % (">=5天" if r["streak"]>=5 else "%d天"%r["streak"]), valid)
show("C5. 2板+赚钱avg2", lambda r: "avg2·%s" % ("无" if r["avg2"] is None else ("正" if r["avg2"]>0 else "负/零")), valid)
show("C6. 板块涨停家数", lambda r: "家数·%s" % (">=8" if r["count"]>=8 else "%d"%r["count"]), valid)
