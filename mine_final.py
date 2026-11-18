# -*- coding: utf-8 -*-
"""定稿验证：候选战法条件组合 vs 现有两战法重叠度、逐笔分布、稳健性。"""
import json, os
from collections import defaultdict, Counter

OUT = os.path.dirname(os.path.abspath(__file__))
D = json.load(open(os.path.join(OUT, "data.json"), encoding="utf-8"))
S = json.load(open(os.path.join(OUT, "mine_money_samples.json"), encoding="utf-8"))
V = [r for r in S if r.get("valid") and r.get("pnl") is not None]

def stat(rows):
    p=[r["pnl"] for r in rows]
    if not p: return (0,0,0,0,0)
    w=[x for x in p if x>0]; l=[x for x in p if x<=0]
    aw=sum(w)/len(w) if w else 0; al=sum(l)/len(l) if l else 0
    return (len(p),round(len(w)/len(p)*100,1),round(sum(p)/len(p),2),
            round(aw/abs(al),2) if al else 0, round(max(p),1))

# 现有两战法逐笔键
rev = D.get("review", {})
exist = {}
for m, arr in rev.items():
    exist[m] = set((t["d"], t["code"]) for t in arr)

# 三个候选定义（三维度）
def c_good(r):   # 严格：B主升/突破 + 空间龙头 + 连续≥3日
    return r["stageB"] in ("主升","突破") and r["is_space"] and r["streak"]>=3
def c_mid(r):    # 均衡：B主升/突破 + 空间龙头 + 连续≥2日
    return r["stageB"] in ("主升","突破") and r["is_space"] and r["streak"]>=2
def c_wide(r):   # 宽：B非节点 + 空间龙头 + 连续≥3日
    return r["stageB"] != "节点" and r["is_space"] and r["streak"]>=3

for tag, fn in [("严格(主升/突破+空间龙头+连续≥3)",c_good),
                ("均衡(主升/突破+空间龙头+连续≥2)",c_mid),
                ("宽(非节点+空间龙头+连续≥3)",c_wide)]:
    rows=[r for r in V if fn(r)]
    n,wr,avg,pl,mx=stat(rows)
    key=set((r["B"],r["code"]) for r in rows)
    ov1=len(key & exist.get("spaceDragon",set()))
    ov2=len(key & exist.get("trialLead",set()))
    print("\n### "+tag)
    print("笔数%d 胜率%.1f%% 笔均%.2f%% 盈亏比%.2f 最大单笔%.1f%%" % (n,wr,avg,pl,mx))
    print("与战法一空间突破重叠 %d 笔；与战法二试错龙头重叠 %d 笔；纯新增 %d 笔"
          % (ov1,ov2,len(key)-len((key&exist.get('spaceDragon',set()))|(key&exist.get('trialLead',set())))))
    print("年份分布",dict(sorted(Counter(r["B"][:4] for r in rows).items())))
    print("阶段分布",dict(Counter(r["stageB"] for r in rows)))
    print("买入日样本(前12)：")
    for r in rows[:12]:
        print("  %s→%s %s %s %d板 空间%d/压%d 连续%d日 题阶%s 盈亏%+.1f%%"
              %(r["T"],r["B"],r["name"],r["theme"][:8],r["bT"],r["topT"],r["supT"],
                r["streak"],r["theme_stage"],r["pnl"]))

# 稳健性：按年看严格组合
print("\n"+"="*100)
print("严格组合按年份稳健性")
print("="*100)
g=[r for r in V if c_good(r)]
for y in sorted(set(r["B"][:4] for r in g)):
    line([r for r in g if r["B"][:4]==y], y)

def line(rows,y):
    n,wr,avg,pl,mx=stat(rows)
    print("%s 笔数%3d 胜率%5.1f%% 笔均%7.2f%% 盈亏比%5.2f"%(y,n,wr,avg,pl))

# 对照：若把"空间龙头"换成"空间龙头或空间股之一"
stages={x["date"]:x for x in D["stages"]}
def c_alt(r):
    if r["stageB"] not in ("主升","突破") or r["streak"]<3: return False
    return r["is_space"]
print("\n严格组合中 is_space 分布确认：",Counter(r["is_space"] for r in g))
