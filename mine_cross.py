# -*- coding: utf-8 -*-
"""交叉分析：三维度条件组合筛选 + 逐层收紧漏斗，定位赚钱效应龙头的高胜率共性。"""
import json, os, itertools
from collections import defaultdict

OUT = os.path.dirname(os.path.abspath(__file__))
S = json.load(open(os.path.join(OUT, "mine_money_samples.json"), encoding="utf-8"))
V = [r for r in S if r.get("valid") and r.get("pnl") is not None]

def stat(rows):
    p = [r["pnl"] for r in rows]
    if not p: return (0, 0, 0, 0)
    w = [x for x in p if x > 0]; l = [x for x in p if x <= 0]
    wr = len(w)/len(p)*100; avg = sum(p)/len(p)
    aw = sum(w)/len(w) if w else 0; al = sum(l)/len(l) if l else 0
    pl = aw/abs(al) if al else 0
    exp = sum(p)/len(p)
    return (len(p), round(wr,1), round(avg,2), round(pl,2))

def line(tag, rows):
    n, wr, avg, pl = stat(rows)
    print("%-58s 笔数%5d 胜率%5.1f%% 笔均%7.2f%% 盈亏比%5.2f 期望%7.2f%%" %
          (tag, n, wr, avg, pl, avg))

print("="*110)
print("逐层收紧漏斗（三维度：市场阶段 / 市场空间股 / 板块梯队示范持续性）")
print("="*110)
line("L0 全部赚钱效应龙头 T+1 打板（基线）", V)

f1 = [r for r in V if r["stageB"] != "节点"]
line("L1 买入日市场阶段≠节点（突破/主升/补涨）", f1)

f2 = [r for r in f1 if r["is_space"]]
line("L2 + T日该龙头本身就是空间龙头", f2)

f2b = [r for r in f1 if r["rel"] in ("贴压制(顶=压)", "突破(顶>压)")]
line("L2' + T日空间顶=压制或已突破（贴压制/突破）", f2b)

f3 = [r for r in f2b if 3 <= r["topT"] <= 8]
line("L3 + 空间高度 3~8 板（排除 9/10 板高位）", f3)

f4 = [r for r in f3 if r["streak"] >= 2]
line("L4 + 题材连续≥2日登赚钱效应榜（示范延续）", f4)

f5 = [r for r in f4 if r["theme_stage"] in ("启动", "加强", "反复")]
line("L5 + 题材阶段非转弱（启动/加强/反复）", f5)

f6 = [r for r in f5 if r["ladder_n"] >= 3]
line("L6 + 板块梯队≥3档（龙头+中位+首板齐全）", f6)

f7 = [r for r in f6 if 3 <= r["bT"] <= 6]
line("L7 + 龙头T日板位 3~6 板", f7)

print()
print("="*110)
print("单条件增量（在 L1 非节点基础上各加一维）")
print("="*110)
conds = {
  "空间龙头本身": lambda r: r["is_space"],
  "贴压制或突破": lambda r: r["rel"] in ("贴压制(顶=压)", "突破(顶>压)"),
  "空间高度3~8": lambda r: 3 <= r["topT"] <= 8,
  "题材连续≥2日": lambda r: r["streak"] >= 2,
  "题材连续≥3日": lambda r: r["streak"] >= 3,
  "题材阶段非转弱": lambda r: r["theme_stage"] in ("启动","加强","反复"),
  "梯队≥3档": lambda r: r["ladder_n"] >= 3,
  "梯队≥4档": lambda r: r["ladder_n"] >= 4,
  "龙头板位3~6": lambda r: 3 <= r["bT"] <= 6,
  "板块涨停≥5": lambda r: r["count"] >= 5,
  "2板+当日赚钱": lambda r: (r["avg2"] or 0) > 0,
}
for k, fn in conds.items():
    line("L1 + "+k, [r for r in f1 if fn(r)])

print()
print("="*110)
print("三维度组合搜索（阶段×空间×梯队，要求笔数≥40），按笔均排序")
print("="*110)
# 维度取值
stage_opt = {
  "非节点": lambda r: r["stageB"] != "节点",
  "主升/突破": lambda r: r["stageB"] in ("主升","突破"),
  "突破": lambda r: r["stageB"] == "突破",
}
space_opt = {
  "空间龙头": lambda r: r["is_space"],
  "贴压制/突破": lambda r: r["rel"] in ("贴压制(顶=压)","突破(顶>压)"),
  "高度3~8": lambda r: 3 <= r["topT"] <= 8,
  "空间龙头+高3~8": lambda r: r["is_space"] and 3 <= r["topT"] <= 8,
}
ladder_opt = {
  "连续≥2日": lambda r: r["streak"] >= 2,
  "连续≥3日": lambda r: r["streak"] >= 3,
  "梯队≥3档": lambda r: r["ladder_n"] >= 3,
  "连续≥2+非转弱": lambda r: r["streak"]>=2 and r["theme_stage"] in ("启动","加强","反复"),
  "非转弱+梯队≥3": lambda r: r["theme_stage"] in ("启动","加强","反复") and r["ladder_n"]>=3,
  "连续≥2+梯队≥3": lambda r: r["streak"]>=2 and r["ladder_n"]>=3,
}
combos = []
for sn, sf in stage_opt.items():
  for pn, pf in space_opt.items():
    for gn, gf in ladder_opt.items():
      rows = [r for r in V if sf(r) and pf(r) and gf(r)]
      n, wr, avg, pl = stat(rows)
      if n >= 40:
        combos.append((avg, n, wr, pl, sn, pn, gn))
combos.sort(reverse=True)
print("%-12s %-16s %-18s %6s %7s %8s %7s" % ("市场阶段","空间股","板块梯队","笔数","胜率%","笔均%","盈亏比"))
for avg, n, wr, pl, sn, pn, gn in combos[:25]:
    print("%-12s %-16s %-18s %6d %7.1f %8.2f %7.2f" % (sn, pn, gn, n, wr, avg, pl))
