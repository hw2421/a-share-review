# -*- coding: utf-8 -*-
"""定稿口径：T日复盘(连续登榜+空间龙头) → T+1(B)阶段主升/突破+涨停打板。
  示范持续性用「截至T日连续登赚钱榜天数」，是T日复盘可见信号，无未来函数。
  对比 streak阈值2/3、B日阶段白名单，选最终参数。"""
import json, glob, os
from collections import Counter

OUT=os.path.dirname(os.path.abspath(__file__))
D=json.load(open(os.path.join(OUT,"data.json"),encoding="utf-8"))
dates=D["dates"]; DIDX={d:i for i,d in enumerate(dates)}
stages={x["date"]:x for x in D["stages"]}; money=D["money"]; lf=D["ladderFull"]
KBARS={}
for fn in glob.glob(os.path.join(OUT,"klines","klines_*.json")):
    for c,bars in json.load(open(fn,encoding="utf-8")).items():
        rec=KBARS.setdefault(str(c),{})
        for b in bars:
            if b and len(b)>=5: rec[b[0]]=b
CLOSE={c:{d:b[2] for d,b in rec.items()} for c,rec in KBARS.items()}
pool={}
for d,ths in lf.items():
    m={}
    for nm,arr in ths.items():
        for x in arr: m.setdefault(str(x.get("code")),{"name":x.get("name",""),"boards":x.get("boards") or 1,"theme":nm})
    pool[d]=m
NAME={}
for d,m in pool.items():
    for c,s in m.items(): NAME[c]=s.get("name","")
def lim(c):
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
    b,p=bar(c,d),pc(c,d); return bool(b and p and len(b)>=5 and b[4]/p-1>=lim(c)-.006)
def yz(c,d):
    b,p=bar(c,d),pc(c,d); return bool(b and p and len(b)>=5 and b[1]==b[2]==b[3]==b[4] and b[2]/p-1>=lim(c)-.006)
def yzdn(c,d):
    b,p=bar(c,d),pc(c,d); return bool(b and p and len(b)>=5 and b[1]==b[2]==b[3]==b[4] and b[2]/p-1<=-(lim(c)-.006))
def exsim(c,B):
    for j in range(DIDX[B]+1,len(dates)):
        t=dates[j]; b,p=bar(c,t),pc(c,t)
        if not b or not p: continue
        if yzdn(c,t): continue
        if b[3] and b[3]<=p*.93: return round(p*.93,2),t,"止损",j-DIDX[B]
        if c in pool.get(t,{}): continue
        return b[2],t,"尾盘",j-DIDX[B]
    tail=[(x,(KBARS.get(c) or {})[x][2]) for x in sorted(KBARS.get(c) or {}) if x>B and (KBARS.get(c) or {})[x][2]]
    return (tail[-1][1],tail[-1][0],"期末",len(tail)) if tail else None
def streak_to(T,nm):
    """截至T日连续登赚钱榜天数。"""
    j=DIDX[T]; s=0
    while j>=0 and any(r["theme"]==nm for r in money.get(dates[j],[])):
        s+=1; j-=1
    return s

def build(streak_min, stage_white):
    out=[]
    for i in range(len(dates)-1):
        T,B=dates[i],dates[i+1]
        stT=stages.get(T,{}) or {}
        spT=set(str(s.get("code")) for s in (stT.get("spaceFull") or []))
        # T日复盘信号：T日空间龙头 + 题材连续登榜≥min
        for r in money.get(T,[]):
            code=str(r["leadCode"]); nm=r["theme"]
            if code not in spT: continue
            sk=streak_to(T,nm)
            if sk<streak_min: continue
            # B日参与条件
            stB=stages.get(B,{}) or {}
            if stB.get("stage") not in stage_white: continue
            e=pool.get(B,{}).get(code); sealed=bool(e)
            if not (sealed or touch(code,B)) or yz(code,B): continue
            b,p=bar(code,B),pc(code,B)
            buy=b[2] if sealed else round(p*(1+lim(code)),2)
            o=exsim(code,B)
            if not o: continue
            sell,sd,rs,hold=o
            out.append({"d":B,"T":T,"code":code,"name":NAME.get(code,""),"theme":nm,
                "stage":stB["stage"],"boards":(e or {}).get("boards") or r.get("maxB") or 0,
                "streak":sk,"theme_stage":r.get("stage"),
                "pnl":round((sell-buy)/buy*100,2),"burst":not sealed})
    out.sort(key=lambda x:(x["d"],x["code"]))
    return out

def stat(rows):
    p=[r["pnl"] for r in rows]
    if not p: return (0,0,0,0)
    w=[x for x in p if x>0]; l=[x for x in p if x<=0]
    aw=sum(w)/len(w) if w else 0; al=sum(l)/len(l) if l else 0
    return (len(p),round(len(w)/len(p)*100,1),round(sum(p)/len(p),2),round(aw/abs(al),2) if al else 0)

rev=D.get("review",{})
def overlap(rows):
    k=set((t["d"],t["code"]) for t in rows)
    o1=k&set((t["d"],t["code"]) for t in rev.get("spaceDragon",[]))
    o2=k&set((t["d"],t["code"]) for t in rev.get("trialLead",[]))
    return len(o1),len(o2),len(k)-len(o1|o2)

for sm in (2,3,4):
    for sw in (("主升","突破"),("主升","突破","补涨")):
        rows=build(sm,sw)
        n,wr,avg,pl=stat(rows)
        o1,o2,nw=overlap(rows)
        print("连榜≥%d 阶段%s : 笔%3d 胜%5.1f%% 均%6.2f%% 盈亏比%5.2f | 重叠战法一%d/二%d 新增%d"
              %(sm,"/".join(sw),n,wr,avg,pl,o1,o2,nw))

print("\n--- 定稿参数 连榜≥3 + 阶段{主升,突破} 细节 ---")
final=build(3,("主升","突破"))
print("年份",dict(sorted(Counter(t["d"][:4] for t in final).items())))
print("阶段",dict(Counter(t["stage"] for t in final)))
print("题阶",dict(Counter(t["theme_stage"] for t in final)))
print("炸板",sum(1 for t in final if t["burst"]))
for y in sorted(set(t["d"][:4] for t in final)):
    n,wr,avg,pl=stat([t for t in final if t["d"][:4]==y])
    print("  %s 笔%3d 胜%5.1f%% 均%6.2f%% 盈亏比%5.2f"%(y,n,wr,avg,pl))
ex=[t for t in final if t["name"]!="顺控发展"]
print("剔除顺控发展:",stat(ex))
json.dump(final,open(os.path.join(OUT,"mine_trades_v2.json"),"w",encoding="utf-8"),ensure_ascii=False)
