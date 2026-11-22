# -*- coding: utf-8 -*-
"""v78 统计（对话汇报用，不入站）：历史空间突破股后续表现。

口径（与站点空间压制/空间高度同源）：
  · 空间高度 spaceTop：data.json stages 每日值（截至当日市场的最高连板板位）；
  · 突破事件（t 日）：个股当日板位（收盘涨停）或盘中触及涨停板位 > t-1 日 spaceTop
    （刷新市场空间高度），分三类：
      zt_hard  收盘涨停、全天未开板（最低价 ≥ 当日涨停价，含一字）；
      zt_reseal 收盘涨停、但盘中炸板回封（最低价 < 当日涨停价，当日开板）；
      zb_brk   盘中触及突破板位涨停价、收盘未封住（炸板未回封）。
  · 连续涨幅：突破后（t+1 起）连续收盘涨停天数 ext；另报突破后 5 个交易日累计涨跌幅；
  · 盈利成功率：t+1 开盘买入（开盘 ≥ t+1 涨停价＝一字买不进，豁免），
    持有 3 个交易日以 t+3 收盘价卖出，收益 = 卖出/买入 − 1，收益 > 0 计为盈利；
  · 展示：总计 + 各年份 + 按突破板位（3/4/5+ 板）细分。
"""
import json
import os
import glob
import sys
import collections

OUT = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, OUT)
import fetch_data as fd

CUT = "20260101"

D = json.load(open(os.path.join(OUT, "data.json"), encoding="utf-8"))
stages = sorted(D["stages"], key=lambda x: x["date"])
space_top = {x["date"]: x.get("spaceTop") or 0 for x in stages}
ec = json.load(open(os.path.join(OUT, ".excel_cache.json"), encoding="utf-8"))
pools = {}
for d, v in (ec.get("pools") or {}).items():
    if d < CUT and v:
        pools[d] = v
for d, v in fd.load_pool_cache().items():
    if d >= CUT and v:
        pools[d] = v
dates = sorted(pools)
didx = {d: i for i, d in enumerate(dates)}

KB = {}
for fp in glob.glob(os.path.join(OUT, "klines", "klines_*.json")):
    raw = json.load(open(fp, encoding="utf-8"))
    for c, bars in raw.items():
        for b in bars:
            if b and len(b) >= 6 and (not isinstance(b[2], (int, float)) or b[2] > 0):
                KB[(str(c), b[0])] = b

def lim_of(code, name):
    if "ST" in (name or "").upper():
        return .05
    if str(code).startswith(("30", "688")):
        return .20
    if str(code).startswith(("8", "4", "92")):
        return .30
    return .10

def prev_close(code, d):
    i = didx.get(d)
    if not i:
        return None
    b = KB.get((str(code), dates[i - 1]))
    return float(b[2]) if (b and b[2]) else None

def bar(code, d):
    return KB.get((str(code), d))

def chain_up(p0, lim, k):
    """k 板涨停价（逐级 round，与真实涨停价序列一致）。"""
    px = p0
    for _ in range(k):
        px = round(px * (1 + lim), 2)
    return px

events = []
for i in range(1, len(dates)):
    d, dp = dates[i], dates[i - 1]
    st0 = space_top.get(dp)
    if not st0 or st0 < 1:
        continue
    pool_map = {str(s["code"]): s for s in pools.get(d, [])}
    # ① 收盘涨停突破（硬板 / 炸板回封）
    for code, s in pool_map.items():
        b = s.get("boards") or 1
        if b <= st0:
            continue
        bt = bar(code, d)
        pc = prev_close(code, d)
        kind = "zt_hard"
        if bt and pc and len(bt) >= 5:
            up1 = chain_up(pc, lim_of(code, s.get("name")), 1)
            if bt[3] < up1 - 0.011:            # 当日最低价低于涨停价 = 盘中开过板
                kind = "zt_reseal"
        events.append({"d": d, "code": code, "name": s.get("name") or code,
                       "kind": kind, "b": b, "year": d[:4]})
    # ② 炸板未封的突破（触及 > st0 的涨停板位、收盘未回封）
    seen = set(pool_map.keys())
    for s in pools.get(dp, []):
        code = str(s["code"])
        if code in seen:
            continue
        bb = bar(code, d)
        pc = prev_close(code, d)
        if not bb or not bb[4] or not pc:
            continue
        lim = lim_of(code, s.get("name"))
        k, px = 0, float(pc)
        while k < 20:
            nxt = chain_up(px, lim, 1)
            if float(bb[4]) >= nxt - 0.02:
                k += 1
                px = nxt
            else:
                break
        if k > st0 and bb[2]:
            up_k = chain_up(float(pc), lim, k)
            if float(bb[2]) < up_k - 0.011:    # 收盘未封住
                events.append({"d": d, "code": code, "name": s.get("name") or code,
                               "kind": "zb_brk", "b": k, "year": d[:4]})

n_kinds = collections.Counter(x["kind"] for x in events)
print("突破事件 %d 笔（硬板 %d / 炸板回封 %d / 炸板未封 %d）"
      % (len(events), n_kinds["zt_hard"], n_kinds["zt_reseal"], n_kinds["zb_brk"]))

def follow(e):
    i = didx[e["d"]]
    code = str(e["code"])
    ext = 0
    j = i + 1
    while j < len(dates) and str(code) in {str(s["code"]) for s in pools.get(dates[j], [])}:
        ext += 1
        j += 1
    bt = bar(code, e["d"])
    cum5 = None
    if bt and bt[2]:
        j5 = min(i + 5, len(dates) - 1)
        b5 = bar(code, dates[j5])
        if b5 and b5[2]:
            cum5 = round((float(b5[2]) / float(bt[2]) - 1) * 100, 2)
    ret = None
    if i + 1 < len(dates) and i + 3 < len(dates):
        b1 = bar(code, dates[i + 1])
        b3 = bar(code, dates[i + 3])
        if b1 and b3 and b1[1] and b3[2]:
            pc1 = prev_close(code, dates[i + 1])
            if pc1:
                up1 = chain_up(pc1, lim_of(code, e["name"]), 1)
                if float(b1[1]) < up1 - 0.011:
                    ret = round((float(b3[2]) / float(b1[1]) - 1) * 100, 2)
    return ext, cum5, ret

for e in events:
    e["ext"], e["cum5"], e["ret"] = follow(e)
rows = events

def agg(items):
    n = len(items)
    if not n:
        return None
    rets = [x["ret"] for x in items if x["ret"] is not None]
    cums = [x["cum5"] for x in items if x["cum5"] is not None]
    exts = [x["ext"] for x in items]
    return {
        "n": n,
        "next_zt_pct": round(sum(1 for x in items if x["ext"] >= 1) / n * 100, 1),
        "ext_avg": round(sum(exts) / n, 2),
        "ext_med": sorted(exts)[n // 2],
        "cum5_avg": round(sum(cums) / len(cums), 2) if cums else None,
        "n_ret": len(rets),
        "win_pct": round(sum(1 for r in rets if r > 0) / len(rets) * 100, 1) if rets else None,
        "ret_avg": round(sum(rets) / len(rets), 2) if rets else None,
        "ret_med": round(sorted(rets)[len(rets) // 2], 2) if rets else None,
    }

def fmt(a):
    if not a:
        return "—"
    return ("%d笔 续板%s%% 连板均%s/中位%s 5日累计%s%% 买测%d笔 胜率%s%% 均%s%% 中位%s%%"
            % (a["n"], a["next_zt_pct"], a["ext_avg"], a["ext_med"], a["cum5_avg"],
               a["n_ret"], a["win_pct"], a["ret_avg"], a["ret_med"]))

print("\n【总计 · 按突破当日形态】")
for kind, label in (("zt_hard", "硬板突破(全天未开板)"), ("zt_reseal", "炸板回封突破"),
                    ("zb_brk", "炸板未封(冲击新高失败)"), ("all", "合计")):
    a = agg([x for x in rows if kind == "all" or x["kind"] == kind])
    print("%-16s %s" % (label, fmt(a)))

print("\n【按年份】（合计口径）")
for y in sorted({x["year"] for x in rows}):
    items = [x for x in rows if x["year"] == y]
    hard = agg([x for x in items if x["kind"] == "zt_hard"])
    reseal = agg([x for x in items if x["kind"] == "zt_reseal"])
    zb = agg([x for x in items if x["kind"] == "zb_brk"])
    a = agg(items)
    print("%s 合计:%s" % (y, fmt(a)))
    print("      硬板:%s" % fmt(hard))
    print("      回封:%s" % fmt(reseal))
    print("      炸未封:%s" % fmt(zb))

print("\n【按突破板位】（合计）")
for bmin, bmax, label in ((3, 3, "3板"), (4, 4, "4板"), (5, 99, "5板+")):
    a = agg([x for x in rows if bmin <= x["b"] <= bmax])
    print("%-5s %s" % (label, fmt(a)))

json.dump(rows, open(os.path.join(OUT, "output", "breakout_stat_detail.json"), "w", encoding="utf-8"),
          ensure_ascii=False)
print("\n明细已存 output/breakout_stat_detail.json")
