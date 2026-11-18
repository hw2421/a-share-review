# -*- coding: utf-8 -*-
"""v27 消融实验：逐个条件单独开关，量化每个条件对 胜率/笔均/样本量 的净贡献。
   目的：找出「真正改善期望」的条件，而非单纯砍样本的条件。"""
import json, glob, os
from collections import Counter

OUT = os.path.dirname(os.path.abspath(__file__))
exec(open(os.path.join(OUT, "v27_validate.py"), encoding="utf-8").read().split(
    'print("\\n" + "="*112)')[0])   # 复用前面的数据装载与函数（去掉打印段）

def run2(cfg):
    trades = {"spaceDragon": [], "trialLead": [], "demoLead": []}
    bought = set()
    for i in range(1, len(dates)):
        B, T = dates[i], dates[i-1]
        sT, sB = st_of(T), st_of(B)
        stT, stB = sT.get("stage") or "", sB.get("stage") or ""
        topT, supT = sT.get("spaceTop") or 0, sT.get("suppress") or 0
        space_codes = {str(x.get("code")) for x in (sT.get("spaceFull") or [])}
        cand = daily_cand(B); t2t = c2t(T)
        ztT = zt_of(T)
        topP = st_of(dates[i-2]).get("spaceTop") or 0 if i >= 2 else 0
        topDelta = topT - topP
        ban = None
        if cfg.get("ban_zt100") and ztT >= 100:
            ban = "涨停家数≥100"
        elif cfg.get("ban_topdrop") and topDelta <= -4:
            ban = "空间骤降"

        def earn_ok(th):
            arr = lf(T, th)
            mids = [x for x in arr if (x.get("boards") or 1) == 2]
            for x in mids:
                cc = str(x.get("code"))
                cr = CLOSE.get(cc); pp = pc(cc, B)
                chg = round((cr[B]/pp-1)*100, 2) if (cr and pp and B in cr and cr[B]) else None
                if cc in (cand.get(B) or {}) and cand[B][cc].get("sealed"): return True
                if chg is not None and chg > 0: return True
            return False

        def push(m, code, e, th, boards, stage):
            if cfg.get("first_only") or cfg.get("first_all"):
                # 「只在首次符合条件时买入，次日符合不买入不加仓」：
                # 以该股本轮连板周期为界——同一轮连板里第一次符合条件即买入，
                # 之后该股在同一轮连板内再符合条件也不重复买入（不加仓）；
                # 断板后重新起一轮连板属新机会，可再次参与。
                # first_all：三个战法共用一把锁（同一账户不加仓同一标的）。
                ck = cycle_key(code, B) or ("%s#%s" % (code, B))
                if cfg.get("first_all"):
                    k = "ALL|%s|%s" % (code, ck)
                else:
                    k = "%s|%s|%s" % (m, code, ck)
                if k in bought: return
                bought.add(k)
            b = bar(code, B)
            buy = b[2] if e.get("sealed") else round(pc(code, B)*(1+lim(code)), 2)
            out = exsim(code, B)
            if not out: return
            sell, sd, rs, hold = out
            trades[m].append({"d": B, "code": code, "name": NAME.get(code, ""), "theme": th,
                "stage": stage, "boards": boards, "burst": not e.get("sealed"),
                "buy": round(buy,2), "sell": round(sell,2), "sellD": sd, "hold": hold,
                "reason": rs, "pnl": round((sell-buy)/buy*100,2)})

        # 战法一
        if supT >= 2 and topT == supT:
            for s in (sT.get("spaceFull") or []):
                code = str(s.get("code") or "")
                if not code: continue
                th = t2t.get(code) or ""
                if not th or not board_effect_ok(B, th): continue
                if ban: continue
                e = cand.get(code)
                if not e or not (e.get("sealed") or touch(code, B)): continue
                if yz(code, B) or consec_yz(code, B): continue
                boards = e.get("boards") or 0
                if boards < supT + 1: continue
                if cfg.get("sd_breakout") and stB != "突破": continue
                if cfg.get("sd_le6") and boards > 6: continue
                if cfg.get("sd_earn") and not earn_ok(th): continue
                push("spaceDragon", code, e, th, boards, stB)
        # 战法二
        if stT == "节点":
            groups = {}
            for code, e in cand.items():
                th = t2t.get(code) or ""
                if not th: continue
                groups.setdefault(th, []).append((code, e))
            for th, items in groups.items():
                lbB = max((e.get("boards") or 1) for _, e in items)
                if lbB < 3 or not board_effect_ok(B, th): continue
                if ban: continue
                for code, e in [x for x in items if (x[1].get("boards") or 1) == lbB]:
                    if not (e.get("sealed") or touch(code, B)): continue
                    if yz(code, B) or consec_yz(code, B): continue
                    if cfg.get("tl_cnt4") and len(lf(B, th)) < 4: continue
                    if cfg.get("tl_earn") and not earn_ok(th): continue
                    push("trialLead", code, e, th, lbB, stB)
        # 战法三
        if stB in ("主升", "突破"):
            for s in (sT.get("spaceFull") or []):
                code = str(s.get("code") or "")
                if not code: continue
                th = t2t.get(code) or ""
                if not th or mstreak.get((T, th), 0) < 3: continue
                if ban: continue
                e = cand.get(code)
                if not e or not (e.get("sealed") or touch(code, B)): continue
                if yz(code, B) or consec_yz(code, B): continue
                push("demoLead", code, e, th, e.get("boards") or 0, stB)
    return trades

def row(cfg, tag):
    t = run2(cfg)
    out = []
    for m in ("spaceDragon", "trialLead", "demoLead"):
        p = [x["pnl"] for x in t[m]]
        if not p: out.append("%s: 无" % m[:6]); continue
        w = [x for x in p if x > 0]; l = [x for x in p if x <= 0]
        aw = sum(w)/len(w) if w else 0; al = sum(l)/len(l) if l else 0
        out.append("%s %3d笔 胜%5.1f%% 均%+6.2f%% 比%4.2f" % (m[:6], len(p), len(w)/len(p)*100,
                     sum(p)/len(p), aw/abs(al) if al else 0))
    allp = [x["pnl"] for v in t.values() for x in v]
    aw = sum([x for x in allp if x>0])/max(1,len([x for x in allp if x>0]))
    al = sum([x for x in allp if x<=0])/max(1,len([x for x in allp if x<=0]))
    print("%-30s | %s | 合计 %3d笔 胜%5.1f%% 均%+6.2f%% 比%4.2f"
          % (tag, " | ".join(out), len(allp),
             len([x for x in allp if x>0])/len(allp)*100, sum(allp)/len(allp),
             aw/abs(al) if al else 0))

print("="*150)
row({}, "基线(无新条件)")
print("-"*150)
for k, lbl in [("first_only","首次买入不重复"),("sd_breakout","战法一:B日须突破"),
               ("sd_le6","战法一:板位≤6"),("sd_earn","战法一:2板当日赚钱"),
               ("tl_cnt4","战法二:板块≥4只"),("tl_earn","战法二:2板当日赚钱"),
               ("ban_zt100","禁入:涨停≥100"),("ban_topdrop","禁入:空间骤降4+")]:
    row({k: True}, "单开:"+lbl)
print("-"*150)
row({"first_only":True}, "首次买入")
row({"sd_breakout":True,"sd_le6":True,"sd_earn":True}, "战法一三门槛")
row({"tl_cnt4":True,"tl_earn":True}, "战法二两门槛")
row({"ban_zt100":True,"ban_topdrop":True}, "两禁入")
print("-"*150)
row({"first_only":True,"sd_breakout":True,"sd_le6":True,"sd_earn":True,
     "tl_cnt4":True,"tl_earn":True,"ban_zt100":True,"ban_topdrop":True}, "全开(v27候选)")
row({"first_only":True,"sd_breakout":True,"sd_le6":True,"sd_earn":True,
     "tl_cnt4":True,"ban_zt100":True}, "全开(去tl_earn)")
row({"first_only":True,"sd_breakout":True,"sd_le6":True,
     "tl_cnt4":True,"tl_earn":True,"ban_zt100":True}, "全开(去sd_earn)")
row({"first_only":True,"sd_le6":True,"sd_earn":True,
     "tl_cnt4":True,"tl_earn":True,"ban_zt100":True}, "全开(去sd_breakout)")
row({"first_only":True,"sd_breakout":True,"sd_le6":True,"sd_earn":True,
     "tl_earn":True,"ban_zt100":True}, "全开(去tl_cnt4)")
row({"first_only":True,"sd_breakout":True,"sd_le6":True,"sd_earn":True,
     "tl_cnt4":True,"tl_earn":True}, "全开(去两禁入)")
print("-"*150)
print(">>> 首次买入两种口径对比（是否跨战法共用锁）")
row({"first_only":True}, "first_only(战法各自锁)")
row({"first_all":True}, "first_all(跨战法共用锁)")
row({"first_all":True,"sd_breakout":True,"sd_le6":True,
     "tl_cnt4":True,"tl_earn":True,"ban_zt100":True,"ban_topdrop":True}, "final:first_all+全条件")
row({"first_all":True,"sd_breakout":True,"sd_le6":True,
     "tl_cnt4":True,"ban_zt100":True,"ban_topdrop":True}, "final2:first_all 去tl_earn")
