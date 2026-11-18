# -*- coding: utf-8 -*-
"""干净版：全部用买入日B可判定条件重构新战法，避免未来函数。

新战法三「示范龙头」候选（B日盘中可判定）：
  ① 市场阶段：B 日 stage ∈ {主升, 突破}
  ② 空间股：B 日该股是空间龙头（spaceFull[B]）
  ③ 板块梯队/示范持续性：该股题材在 B 日赚钱效应榜，且该题材连续≥3 日登榜
  ④ B 日该股涨停（收盘封板或盘中触板炸板），非一字，涨停价打板买入
统一卖出规则。再与现有两战法比重叠。
"""
import json, glob, os
from collections import Counter, defaultdict

OUT = os.path.dirname(os.path.abspath(__file__))
D = json.load(open(os.path.join(OUT, "data.json"), encoding="utf-8"))
dates=D["dates"]; DIDX={d:i for i,d in enumerate(dates)}
stages={x["date"]:x for x in D["stages"]}
money=D["money"]; ladder_full=D["ladderFull"]

KBARS={}
for fn in glob.glob(os.path.join(OUT,"klines","klines_*.json")):
    for c,bars in json.load(open(fn,encoding="utf-8")).items():
        rec=KBARS.setdefault(str(c),{})
        for b in bars:
            if b and len(b)>=5: rec[b[0]]=b
CLOSE={c:{d:b[2] for d,b in rec.items()} for c,rec in KBARS.items()}
pool={}
for d,ths in ladder_full.items():
    m={}
    for nm,arr in ths.items():
        for x in arr: m.setdefault(str(x.get("code")),{"name":x.get("name",""),"boards":x.get("boards") or 1,"theme":nm})
    pool[d]=m
NAME={}
for d,m in pool.items():
    for c,s in m.items(): NAME[c]=s.get("name","")
def lim_of(c):
    nm=NAME.get(c,"")
    if "ST" in nm.upper(): return .05
    if c.startswith(("30","688")): return .20
    if c.startswith(("8","4","92")): return .30
    return .10
def bar(c,d): return (KBARS.get(c) or {}).get(d)
def pc(c,d):
    rec=KBARS.get(c)
    if not rec or d not in rec: return None
    i=DIDX.get(d)
    if i is None: return None
    for j in range(i-1,-1,-1):
        b=rec.get(dates[j])
        if b and len(b)>=3 and b[2]: return b[2]
    return None
def touch(c,d):
    b,p=bar(c,d),pc(c,d)
    return bool(b and p and len(b)>=5 and b[4]/p-1>=lim_of(c)-.006)
def yizi(c,d):
    b,p=bar(c,d),pc(c,d)
    return bool(b and p and len(b)>=5 and b[1]==b[2]==b[3]==b[4] and b[2]/p-1>=lim_of(c)-.006)
def yizi_dn(c,d):
    b,p=bar(c,d),pc(c,d)
    return bool(b and p and len(b)>=5 and b[1]==b[2]==b[3]==b[4] and b[2]/p-1<=-(lim_of(c)-.006))
def exit_sim(c,B):
    i=DIDX[B]
    for j in range(i+1,len(dates)):
        t=dates[j]; b,p=bar(c,t),pc(c,t)
        if not b or not p: continue
        if yizi_dn(c,t): continue
        if b[3] and b[3]<=p*.93: return round(p*.93,2),t,"止损",j-i
        if c in pool.get(t,{}): continue
        return b[2],t,"尾盘",j-i
    tail=[(x,(KBARS.get(c) or {})[x][2]) for x in sorted(KBARS.get(c) or {}) if x>B and (KBARS.get(c) or {})[x][2]]
    return (tail[-1][1],tail[-1][0],"期末",len(tail)) if tail else None

# 题材连续登榜天数（按B日往前数）
def money_streak(d,nm):
    i=DIDX[d]; s=0; j=i
    while j>=0 and any(r["theme"]==nm for r in money.get(dates[j],[])):
        s+=1; j-=1
    return s

# money[B] 里每个题材的龙头code（当日该题材最高板）
money_lead={}
for d,rows in money.items():
    for r in rows:
        money_lead[(d,r["theme"])]=str(r["leadCode"])

trades=[]
for i,d in enumerate(dates):
    st=stages.get(d,{}) or {}
    if st.get("stage") not in ("主升","突破"): continue
    space_codes=set(str(s.get("code")) for s in (st.get("spaceFull") or []))
    for r in money.get(d,[]):
        code=str(r["leadCode"]); nm=r["theme"]
        if code not in space_codes: continue          # ② B日空间龙头
        sk=money_streak(d,nm)
        if sk<3: continue                             # ③ 连续≥3日登榜
        e=pool.get(d,{}).get(code)
        sealed=bool(e); istouch=sealed or touch(code,d)
        if not istouch or yizi(code,d): continue      # ④ 涨停/非一字
        b,p=bar(code,d),pc(code,d)
        buy=b[2] if sealed else round(p*(1+lim_of(code)),2)
        out=exit_sim(code,d)
        if not out: continue
        sell,sd,rs,hold=out
        trades.append({"d":d,"code":code,"name":NAME.get(code,""),"theme":nm,
            "stage":st["stage"],"boards":(e or {}).get("boards") or (r.get("maxB") or 0),
            "spaceTop":st.get("spaceTop"),"suppress":st.get("suppress"),
            "streak":sk,"theme_stage":r.get("stage"),"maxB":r.get("maxB"),"count":r.get("count"),
            "buy":buy,"sell":sell,"sellD":sd,"hold":hold,"reason":rs,
            "pnl":round((sell-buy)/buy*100,2),"burst":not sealed})

def stat(rows):
    p=[r["pnl"] for r in rows]
    w=[x for x in p if x>0]; l=[x for x in p if x<=0]
    aw=sum(w)/len(w) if w else 0; al=sum(l)/len(l) if l else 0
    return (len(p),round(len(w)/len(p)*100,1),round(sum(p)/len(p),2),round(aw/abs(al),2) if al else 0)
trades.sort(key=lambda x:(x["d"],x["code"]))
n,wr,avg,pl=stat(trades)
print("战法三「示范龙头」B日可判定版：笔数%d 胜率%.1f%% 笔均%.2f%% 盈亏比%.2f"%(n,wr,avg,pl))
# 重叠
rev=D.get("review",{})
k=set((t["d"],t["code"]) for t in trades)
o1=k&set((t["d"],t["code"]) for t in rev.get("spaceDragon",[]))
o2=k&set((t["d"],t["code"]) for t in rev.get("trialLead",[]))
print("与战法一重叠%d 与战法二重叠%d 纯新增%d"%(len(o1),len(o2),len(k)-len(o1|o2)))
print("年份",dict(sorted(Counter(t["d"][:4] for t in trades).items())))
print("阶段",dict(Counter(t["stage"] for t in trades)))
print("板位",dict(sorted(Counter(min(t["boards"],12) for t in trades).items())))
print("炸板",sum(1 for t in trades if t["burst"]))
# 按年稳健
for y in sorted(set(t["d"][:4] for t in trades)):
    nn,ww,aa,pp=stat([t for t in trades if t["d"][:4]==y])
    print("  %s 笔%3d 胜%5.1f%% 均%7.2f%% 盈亏比%5.2f"%(y,nn,ww,aa,pp))
# 分去掉极端顺控发展后
ex=[t for t in trades if t["name"]!="顺控发展"]
print("剔除顺控发展后：",stat(ex))
json.dump(trades,open(os.path.join(OUT,"mine_trades.json"),"w",encoding="utf-8"),ensure_ascii=False)
