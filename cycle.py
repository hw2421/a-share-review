# -*- coding: utf-8 -*-
"""赚钱效应循环仪表盘（v60）——「万变不离其宗」的量化落地。

核心认知：赚钱效应不是「涨停多」，而是资金「来了能赚、赚了还想来」的
自我强化循环。本模块每天对循环的四个环节各打分（等权合成 0-10 健康度）：

  ① 买入示范：昨日 ≥2 板个股今日的「不亏钱率」（收盘涨幅 ≥0 占比）
     —— 循环的命门：昨日买入的人今天不亏钱，示范效应才成立。
  ② 溢价扩散：昨日涨停股今日的 晋级率（再板）+ 红盘率（收盘 > 昨收）均值
     —— 赚到钱的示范是否吸引接力。
  ③ 梯队接力：今日 1→2 板晋级家数 + 今日 ≥2 板家数（梯队厚度）
     —— 赚钱效应的传导路径是否畅通（五要素「板块连板助攻」同源）。
  ④ 高度打开：空间高度变化 + 全市场涨停家数变化
     —— 市场是否愿意给高度溢价（五要素「市场情绪变好」同源）。

计分方式：每个原始指标按全期（2017 起）分布做分位数映射（percentile rank），
比率类直接映射，计数类（家数/变化量）先取分位数再合成 —— 分数即「该指标
在历史上的相对位置」，自动适应市场长期变迁（2017 年涨停家数远少于 2026）。

五段生命周期（历史分位数自动划分）：
  冰点 = 健康度分位 < 20%
  高潮 = 健康度分位 ≥ 80%
  扩散 = 分位 ≥ 50% 且 3 日斜率上行
  发酵 = 分位 < 50% 且 3 日斜率上行
  退潮 = 其余（分位 20%-80% 且回落）

负反馈起点：≥2 只「前一日 ≥3 板」个股当日收盘跌幅 ≤ −8%（高位集中处刑日，
高位买入者被公开处刑）—— 亏钱效应的负反馈由此启动，仪表盘标红。

输出：
  days        [{date, s1..s4, score, pct, phase, slope, neg, raw:{...}}, ...] 全期
  cross       生命周期 × 山顶洞人战法 交叉统计（如实呈现，不说谎）
  rule        口径说明
"""
import bisect

PHASES = ("发酵", "扩散", "高潮", "退潮", "冰点")
NEG_LOSS = -8.0           # 负反馈起点阈值：≥2 只前一日≥3板股 收盘跌幅 ≤ −8%
NEG_HIT = 2               # 单日命中家数门槛（高位集中处刑，非个别股）
ICE_SCORE = 4.0           # 推荐降级阈值：健康度 < 4 → 仅防守型机会
WEAK_SCORE = 6.0          # 健康度 < 6 → 循环转弱提示


def compute(dates, pools, days, KBARS, summit=None):
    summit = summit or []
    didx = {d: i for i, d in enumerate(dates)}
    pmap = {d: {s["code"]: s for s in pools.get(d, [])} for d in dates}
    SD = {s["date"]: s for s in days}

    CD = {c: sorted(r) for c, r in KBARS.items()}

    def close_at(code, d):
        rec = KBARS.get(str(code))
        if not rec:
            return None
        ds = CD.get(str(code))
        k = bisect.bisect_left(ds, d)
        if k >= len(ds) or ds[k] != d:
            return None
        b = rec.get(ds[k])
        return b[2] if (b and len(b) >= 5 and b[2]) else None

    def high_at(code, d):
        rec = KBARS.get(str(code))
        if not rec:
            return None
        b = rec.get(d)
        return b[3] if (b and len(b) >= 6 and b[3]) else None

    # ---------- 逐日原始读数 ----------
    rows = []
    for i, d in enumerate(dates):
        if i == 0:
            continue
        pd_ = dates[i - 1]
        prev_pool = pmap.get(pd_, {})
        cur_pool = pmap.get(d, {})
        # ① 昨日 ≥2 板个股今日不亏钱率 / 平均涨幅
        hi2 = [(c, s) for c, s in prev_pool.items() if (s.get("boards") or 1) >= 2]
        chgs = []
        for c, s in hi2:
            cp, cc = close_at(c, pd_), close_at(c, d)
            if cp and cc:
                chgs.append((cc / cp - 1) * 100)
        r1 = (sum(1 for x in chgs if x >= 0) / len(chgs)) if chgs else None
        avg1 = (sum(chgs) / len(chgs)) if chgs else None
        # ② 昨日全部涨停股：晋级率 + 红盘率
        promo = red = tot = 0
        for c, s in prev_pool.items():
            cp, cc = close_at(c, pd_), close_at(c, d)
            if not cp or not cc:
                continue
            tot += 1
            cs = cur_pool.get(c)
            if cs and (cs.get("boards") or 1) > (s.get("boards") or 1):
                promo += 1
            if cc > cp:
                red += 1
        r2a = (promo / tot) if tot else None
        r2b = (red / tot) if tot else None
        # ③ 1→2 板晋级家数 + 今日 ≥2 板家数
        cnt12 = 0
        for c, s in prev_pool.items():
            if (s.get("boards") or 1) == 1:
                cs = cur_pool.get(c)
                if cs and (cs.get("boards") or 1) == 2:
                    cnt12 += 1
        cnt2p = sum(1 for s in cur_pool.values() if (s.get("boards") or 1) >= 2)
        # ④ 空间高度变化 + 涨停家数变化
        st_c, st_p = SD.get(d) or {}, SD.get(pd_) or {}
        dh = (st_c.get("maxBoard") or 0) - (st_p.get("maxBoard") or 0)
        dz = (st_c.get("ztCount") or 0) - (st_p.get("ztCount") or 0)
        # 负反馈起点：≥2 只「前一日 ≥3 板」个股当日收盘跌幅 ≤ −8%（高位集中处刑日）。
        # 冲高回落型（收盘/最高≤−10%）全期仅 1 天且漏掉低开低走型，弃用；
        # 单只 −8% 有 552 天太泛，≥2 只联动 199 天（约 8%）——既有信号意义又不失焦。
        neg = False
        neg_names = []
        for c, s in prev_pool.items():
            if (s.get("boards") or 1) < 3:
                continue
            cp, cc = close_at(c, pd_), close_at(c, d)
            if cp and cc and (cc / cp - 1) * 100 <= NEG_LOSS:
                neg_names.append(s.get("name") or c)
        if len(neg_names) >= NEG_HIT:
            neg = True
        else:
            neg_names = []
        rows.append({
            "date": d,
            "r1": r1, "avg1": avg1, "r2a": r2a, "r2b": r2b,
            "cnt12": cnt12, "cnt2p": cnt2p, "dh": dh, "dz": dz,
            "neg": neg, "negNames": neg_names[:3],
        })

    # ---------- 分位数映射打分 ----------
    def prct_fn(vals):
        sv = sorted(v for v in vals if v is not None)

        def fn(v):
            if v is None or not sv:
                return None
            return bisect.bisect_left(sv, v) / len(sv)
        return fn

    p_r1 = prct_fn([r["r1"] for r in rows])
    p_r2 = prct_fn([None if (r["r2a"] is None or r["r2b"] is None)
                    else (r["r2a"] + r["r2b"]) / 2 for r in rows])
    p_c12 = prct_fn([r["cnt12"] for r in rows])
    p_c2p = prct_fn([r["cnt2p"] for r in rows])
    p_dh = prct_fn([r["dh"] for r in rows])
    p_dz = prct_fn([r["dz"] for r in rows])

    def s2_5(p):
        return round(p * 2.5, 2) if p is not None else None

    for r in rows:
        r["s1"] = s2_5(p_r1(r["r1"]))
        r["s2"] = s2_5(p_r2(None if (r["r2a"] is None or r["r2b"] is None)
                             else (r["r2a"] + r["r2b"]) / 2))
        r["s3"] = (round((p_c12(r["cnt12"]) + p_c2p(r["cnt2p"])) / 2 * 2.5, 2)
                   if r["cnt12"] is not None else None)
        r["s4"] = (round((p_dh(r["dh"]) + p_dz(r["dz"])) / 2 * 2.5, 2)
                   if r["dh"] is not None else None)
        parts = [r["s1"], r["s2"], r["s3"], r["s4"]]
        r["score"] = round(sum(p for p in parts if p is not None), 1) \
            if all(p is not None for p in parts) else None

    # 健康度分位 + 五段生命周期
    scores = [r["score"] for r in rows if r["score"] is not None]

    def pct_of(v):
        return bisect.bisect_left(sorted(scores), v) / len(scores) if scores else 0

    for j, r in enumerate(rows):
        if r["score"] is None:
            r["pct"] = r["phase"] = None
            r["slope"] = None
            continue
        r["pct"] = round(pct_of(r["score"]) * 100, 1)
        # 3 日斜率（分数变化，前 3 日不足时用可得区间）
        k0 = max(0, j - 3)
        r["slope"] = round(r["score"] - rows[k0]["score"], 1) if rows[k0]["score"] is not None else 0
        if r["pct"] < 20:
            r["phase"] = "冰点"
        elif r["pct"] >= 80:
            r["phase"] = "高潮"
        elif r["slope"] > 0:
            r["phase"] = "扩散" if r["pct"] >= 50 else "发酵"
        else:
            r["phase"] = "退潮"

    # ---------- 生命周期 × 山顶洞人战法 交叉统计（如实呈现） ----------
    phase_map = {r["date"]: r for r in rows}
    cross = {}
    for t in summit:
        ph = (phase_map.get(t.get("d")) or {}).get("phase")
        if not ph:
            continue
        g = cross.setdefault(ph, {"n": 0, "win": 0, "sum": 0.0})
        g["n"] += 1
        g["win"] += 1 if (t.get("pnl") or 0) > 0 else 0
        g["sum"] += (t.get("pnl") or 0)
    cross_out = []
    for ph in PHASES:
        g = cross.get(ph)
        if not g or not g["n"]:
            continue
        cross_out.append({
            "phase": ph, "n": g["n"],
            "win": round(g["win"] / g["n"] * 100, 1),
            "avg": round(g["sum"] / g["n"], 2),
        })

    return {
        "days": rows,
        "cross": cross_out,
        "rule": ("循环健康度：四环节等权合成（各 2.5 分，满分 10）。①买入示范＝昨日≥2板股今日"
                 "不亏钱率（循环的命门）；②溢价扩散＝昨日涨停股今日晋级率+红盘率均值；"
                 "③梯队接力＝1→2板晋级家数+≥2板家数；④高度打开＝空间高度变化+涨停家数变化。"
                 "所有指标按全期 2017 起分布做分位数映射——分数即历史相对位置。"
                 "生命周期（分位数自动划分）：冰点<20%分位；高潮≥80%分位；3日斜率上行时"
                 "分位≥50%为扩散、<50%为发酵；其余为退潮。"
                 "负反馈起点＝前一日≥3板个股中 ≥2 只当日收盘跌幅 ≤−8% 的高位集中处刑日"
                 "（高位买入者被公开处刑）。"
                 "推荐联动（v60）：健康度<4 仅防守型机会（推荐缩减至3只）；4-6 循环转弱提示；"
                 "≥6 正常。回测样本不受影响。"),
    }
