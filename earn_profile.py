# -*- coding: utf-8 -*-
"""赚钱效应复盘（v64→v66）——把近期涨停股拉平成样本，按特征分组算赚亏，让数据自己说话。

核心问题（用户口径）：
  近期市场里，哪些个股在赚钱、哪些在亏钱？它们有什么共同特征？

样本定义（一笔样本 = 一只昨日涨停股的一次「次日验证」）：
  T 日涨停（一字板剔除——当天买不进，不构成可参与的赚钱效应）
  → 看 T+1 日的三个结果（用户要求三个都算）：
      rc  昨收买入 → 今收      持筹者收益（主口径，「昨天买的人今天赚没赚」）
      ri  今开买入 → 今收      接力者日内收益（剔除隔夜跳空）
      zt  T+1 是否再涨停        连板晋级（赚钱效应的强度）
  停牌 / 缺 K 线 / 上市首日无昨收 的样本剔除（宁可缺失，不要错误）。

特征维度（全部来自涨停池与当日环境，不预设结论）：
  板位 / 封板时间 / 开板次数 / 换手率 / 题材涨停家数 / 题材内地位 /
  空间龙头四阶段（突破·主升·补涨·节点）/ 全市场涨停家数 / 循环健康度

聚合口径：
  每档给 样本数 n、赚钱率 win（rc>0 占比）、笔均 rc/ri、晋级率 zt、
  超额 ex（该档笔均 rc − 全样本笔均 rc）。只保留 n≥MIN_N(w) 的档参与排序，
  避免小样本噪声；n<MIN_N(w) 的档照实展示但标「样本少」。

窗口（v66 用户定）：近 1 / 3 / 5 个交易日，默认 1 日。
  样本门槛按窗口缩放 MIN_N(w)=max(3, 30·w/20)——1 日窗口全样本才几十笔，
  固定 30 会让所有档变灰、最强/最弱和候选全空。个股榜最少出现次数同步缩放
  （1 日窗口每股最多出现 1 次，门槛固定 2 会让个股榜永远为空）。

明日待验证（候选）：按所选窗口独立打分（特征分＝命中该窗口强/弱档超额之和），
特征分 ≤0 的不推荐（v66：只有站在赚钱画像上的才列出来）。
"""
from collections import Counter, defaultdict

MIN_N = 30          # 单档最小样本基准（20 日窗口用；按窗口缩放）
MIN_N_CROSS = 25    # 交叉格最小样本
MIN_N_STOCK = 2     # 个股上榜最少出现次数基准（按窗口缩放）
ALL_N = 30          # 样本池覆盖交易日数（=最大窗口 5 的富余余量）


def min_n_for(w):
    """按窗口缩放单档最小样本：1日→3 / 3日→4 / 5日→7。"""
    return max(3, int(MIN_N * w / 20))

DIMS = [
    ("boards", "板位"),
    ("seal", "封板时间"),
    ("open", "开板次数"),
    ("turn", "换手率"),
    ("thn", "题材涨停家数"),
    ("role", "题材内地位"),
    ("stage", "四阶段环境"),
    ("heat", "全市场涨停家数"),
    ("health", "循环健康度"),
]

# 每档的展示顺序（未列出的档排在最后，按样本数降序）
ORDER = {
    "boards": ["1板", "2板", "3板", "4板+"],
    "seal": ["≤10:00 早盘", "10:00-11:30", "11:30-14:00", "≥14:00 尾盘", "未知"],
    "open": ["封死(0次)", "1-2次", "≥3次", "未知"],
    "turn": ["<5%", "5-15%", "15-30%", "≥30%", "未知"],
    "thn": ["1-2家(独苗)", "3-5家", "6-10家", "≥11家", "未知"],
    "role": ["龙头", "跟风", "独苗", "未知"],
    "stage": ["突破", "主升", "补涨", "节点", "未知"],
    "heat": ["<40家", "40-60家", "60-80家", "≥80家", "未知"],
    "health": ["<4 弱", "4-6 中", "≥6 强", "未知"],
}


def _b_boards(b):
    b = b or 1
    return "1板" if b <= 1 else ("2板" if b == 2 else ("3板" if b == 3 else "4板+"))


def _b_seal(t):
    if not t or len(str(t)) < 5:
        return "未知"
    try:
        hh, mm = int(str(t)[:2]), int(str(t)[3:5])
    except Exception:
        return "未知"
    m = hh * 60 + mm
    if m >= 15 * 60:
        return "未知"
    if m <= 10 * 60:
        return "≤10:00 早盘"
    if m <= 11 * 60 + 30:
        return "10:00-11:30"
    if m <= 14 * 60:
        return "11:30-14:00"
    return "≥14:00 尾盘"


def _b_open(n):
    if n is None:
        return "未知"
    if n <= 0:
        return "封死(0次)"
    if n <= 2:
        return "1-2次"
    return "≥3次"


def _b_turn(v):
    if v is None:
        return "未知"
    if v < 5:
        return "<5%"
    if v < 15:
        return "5-15%"
    if v < 30:
        return "15-30%"
    return "≥30%"


def _b_thn(n):
    if not n:
        return "未知"
    if n <= 2:
        return "1-2家(独苗)"
    if n <= 5:
        return "3-5家"
    if n <= 10:
        return "6-10家"
    return "≥11家"


def _b_heat(n):
    if n is None:
        return "未知"
    if n < 40:
        return "<40家"
    if n < 60:
        return "40-60家"
    if n < 80:
        return "60-80家"
    return "≥80家"


def _b_health(s):
    if s is None:
        return "未知"
    if s < 4:
        return "<4 弱"
    if s < 6:
        return "4-6 中"
    return "≥6 强"


def _th_info(v):
    """题材当日梯队：兼容 build 内存版 {count,maxB,stocks} 与 data.json 精简版 [stocks]。"""
    if isinstance(v, dict):
        st = v.get("stocks") or []
        mx = v.get("maxB")
        if mx is None and st:
            mx = max((x.get("boards") or 1) for x in st)
        return (v.get("count") or len(st), mx, st)
    if isinstance(v, list):
        mx = max(((x.get("boards") or 1) for x in v), default=None)
        return (len(v), mx, v)
    return (0, None, [])


def _mean(xs):
    return sum(xs) / len(xs) if xs else None


def _agg(samples, base_rc, min_n=None):
    n = len(samples)
    if not n:
        return None
    rc = [s["rc"] for s in samples]
    ri = [s["ri"] for s in samples if s["ri"] is not None]
    mn = MIN_N if min_n is None else min_n
    return {
        "n": n,
        "win": round(sum(1 for x in rc if x > 0) / n * 100, 1),
        "rc": round(_mean(rc), 2),
        "ri": round(_mean(ri), 2) if ri else None,
        "zt": round(sum(s["zt"] for s in samples) / n * 100, 1),
        "ex": (round(_mean(rc) - base_rc, 2) if base_rc is not None else None),
        "few": n < mn,
    }


def _feat_of(s, th, n_th, mx, st, cycle_s):
    """一只涨停股的特征档位（样本与今日候选共用同一套口径）。"""
    boards = s.get("boards") or 1
    return {
        "boards": _b_boards(boards),
        "seal": _b_seal(s.get("first_seal")),
        "open": _b_open(s.get("open_num")),
        "turn": _b_turn(s.get("turnover")),
        "thn": _b_thn(n_th),
        "role": ("未知" if not n_th else
                 ("独苗" if n_th <= 1 else
                  ("龙头" if (mx is not None and boards >= mx) else "跟风"))),
        "stage": (st.get("stage") or "未知"),
        "heat": _b_heat(st.get("ztCount")),
        "health": _b_health(cycle_s),
    }


def _day_theme_stats(pools_day, theme_of, d):
    """当日题材 → (家数, 最高板)。同一题材名下的涨停股合并统计。"""
    tmap = {str(s["code"]): theme_of(d, str(s["code"])) for s in pools_day}
    thn, thmax = Counter(), {}
    for s in pools_day:
        t = tmap.get(str(s["code"]))
        if not t:
            continue
        thn[t] += 1
        thmax[t] = max(thmax.get(t, 0), s.get("boards") or 1)
    return tmap, thn, thmax


def _build_samples(dates, pools, theme_of, stages, cycle_map, KBARS, yizi):
    """最近 ALL_N 个交易日的全部样本（T 日涨停 → T+1 日验证）。"""
    st_map = {r["date"]: r for r in (stages or [])}
    out = []
    i0 = max(0, len(dates) - ALL_N - 1)
    for i in range(i0, len(dates) - 1):
        d, d1 = dates[i], dates[i + 1]
        yz = {str(x) for x in (yizi.get(d) or set())}
        nxt = {str(s.get("code")) for s in (pools.get(d1) or [])}
        st = st_map.get(d) or {}
        cycle_s = (cycle_map.get(d) or {}).get("score")
        tmap, thn, thmax = _day_theme_stats(pools.get(d) or [], theme_of, d)
        for s in (pools.get(d) or []):
            code = str(s.get("code") or "")
            if not code or code in yz:
                continue
            bT = (KBARS.get(code) or {}).get(d)
            b1 = (KBARS.get(code) or {}).get(d1)
            if not bT or not b1 or not bT[2] or not b1[1]:
                continue
            rc = (b1[2] / bT[2] - 1) * 100
            ri = (b1[2] / b1[1] - 1) * 100
            th = tmap.get(code) or ""
            feat = _feat_of(s, th, thn.get(th), thmax.get(th), st, cycle_s)
            out.append({
                "d": d, "d1": d1, "code": code, "name": s.get("name") or code,
                "boards": (s.get("boards") or 1), "theme": th,
                "rc": rc, "ri": ri, "zt": 1 if code in nxt else 0,
                "f": feat,
            })
    return out


def _dims_agg(samples, base_rc, min_n=None):
    res = []
    for key, label in DIMS:
        grp = defaultdict(list)
        for s in samples:
            grp[s["f"][key]].append(s)
        items = []
        for k, ss in grp.items():
            a = _agg(ss, base_rc, min_n)
            if a:
                a["k"] = k
                items.append(a)
        order = ORDER.get(key) or []
        idx = {k: i for i, k in enumerate(order)}
        items.sort(key=lambda x: (idx.get(x["k"], 99), -x["n"]))
        res.append({"key": key, "name": label, "items": items})
    return res


def _cross(samples, base_rc, d1, d2, name, min_n=None):
    grp = defaultdict(list)
    for s in samples:
        grp[(s["f"][d1], s["f"][d2])].append(s)
    cells = []
    mn = MIN_N_CROSS if min_n is None else min_n
    for (a, b), ss in grp.items():
        if len(ss) < mn:
            continue
        g = _agg(ss, base_rc, min_n)
        if not g:
            continue
        g.pop("few", None)
        g["a"], g["b"] = a, b
        cells.append(g)
    cells.sort(key=lambda x: -(x["rc"] if x["rc"] is not None else -999))
    return {"name": name, "cells": cells}


def _stocks(samples, NAME=None, min_stock=None, limit=12):
    grp = defaultdict(list)
    for s in samples:
        grp[s["code"]].append(s)
    ms = MIN_N_STOCK if min_stock is None else min_stock
    rows = []
    for code, ss in grp.items():
        if len(ss) < ms:
            continue
        rc = [s["rc"] for s in ss]
        th = Counter(s["theme"] for s in ss if s["theme"]).most_common(1)
        rows.append({
            "code": code, "name": ss[0]["name"],
            "n": len(ss),
            "win": round(sum(1 for x in rc if x > 0) / len(rc) * 100, 1),
            "rc": round(sum(rc) / len(rc), 2),
            "zt": round(sum(s["zt"] for s in ss) / len(ss) * 100, 1),
            "boards": round(sum(s["boards"] for s in ss) / len(ss), 1),
            "theme": th[0][0] if th else "",
            "last": max(s["d"] for s in ss),
        })
    rows.sort(key=lambda x: -x["rc"])
    up = [r for r in rows if r["rc"] > 0][:limit]
    down = [r for r in reversed(rows) if r["rc"] < 0][:limit]
    return {"up": up, "down": down, "total": len(rows), "minS": ms}


def _candidates(today_samples, dims, top, bottom, limit=8):
    """明日候选：今日涨停股按「赚钱特征」打分。

    只用该窗口最强 5 档 / 最弱 5 档（强信号）作权重：命中强档加分、命中弱档减分，
    分数 = 命中档超额之和。中间档（区分度低）不参与，避免稀释信号。
    """
    w = {}
    for it in list(top) + list(bottom):
        w[(it["key"], it["k"])] = {"ex": it["ex"], "n": it["n"], "win": it["win"],
                                   "dim": it["dim"]}
    out = []
    for s in today_samples:
        hits, miss = [], []
        for key, _ in DIMS:
            k = s["f"][key]
            e = w.get((key, k))
            if not e:
                continue
            (hits if e["ex"] > 0 else miss).append(
                {"dim": e["dim"], "k": k, "ex": e["ex"], "n": e["n"], "win": e["win"]})
        score = round(sum(x["ex"] for x in hits + miss), 2) if (hits or miss) else None
        if score is None or score <= 0:
            continue                      # v66：特征分 ≤0 不推荐（没站在赚钱画像上）
        out.append({
            "code": s["code"], "name": s["name"], "boards": s["boards"],
            "theme": s["theme"], "score": score,
            "hits": sorted(hits, key=lambda x: -x["ex"]),
            "miss": sorted(miss, key=lambda x: x["ex"]),
        })
    out.sort(key=lambda x: -(x["score"] if x["score"] is not None else -999))
    return out[:limit]


def compute(dates, pools, ladder_full, stages, cycle_map, KBARS, yizi, NAME=None,
            STOCK_THEME=None, windows=(1, 3, 5)):
    """STOCK_THEME：个股→题材兜底映射（build 传 data.json 的 stockTheme，覆盖 96% 涨停股）。

    题材口径：当日复盘清单（ladder_full）优先，清单外的涨停股用 stockTheme 兜底；
    当日「题材家数 / 最高板」由当日涨停池按同一映射现算，不取清单的家数
    （清单常只覆盖部分题材，直接用会把 70% 的股判成「未知」）。
    """
    NAME = NAME or {}
    STOCK_THEME = STOCK_THEME or {}
    _lf = {}
    for d, m in (ladder_full or {}).items():
        for nm, v in m.items():
            for s in _th_info(v)[2]:
                _lf[(d, str(s.get("code")))] = nm

    def theme_of(d, code):
        return _lf.get((d, code)) or STOCK_THEME.get(code) or ""

    samples = _build_samples(dates, pools, theme_of, stages, cycle_map, KBARS, yizi)
    today = dates[-1]
    # 今日涨停股（明日待验证）：只算特征，不给结果（T+1 还没发生）
    st_map = {r["date"]: r for r in (stages or [])}
    _st = st_map.get(today) or {}
    _cyc = (cycle_map.get(today) or {}).get("score")
    _yz = {str(x) for x in (yizi.get(today) or set())}
    _pl = [s for s in (pools.get(today) or []) if str(s.get("code")) not in _yz]
    _tmap, _thn, _thmax = _day_theme_stats(_pl, theme_of, today)
    today_samples = []
    for s in _pl:
        code = str(s.get("code") or "")
        th = _tmap.get(code) or ""
        today_samples.append({
            "code": code, "name": s.get("name") or code,
            "boards": s.get("boards") or 1, "theme": th,
            "f": _feat_of(s, th, _thn.get(th), _thmax.get(th), _st, _cyc),
        })

    base_rc = _mean([s["rc"] for s in samples]) if samples else None
    packs = {}
    for w in windows:
        mn = min_n_for(w)
        # v66：按「验证日 d1」∈ 最近 w 个交易日过滤——「近1日」＝最新交易日当天
        # 昨日涨停股的验证结果（若按涨停日 d 过滤，w1 会把最新一天的结果漏掉）。
        wd = set(dates[-w:])
        ss = [s for s in samples if s["d1"] in wd]
        if not ss:
            continue
        b = _agg(ss, None, mn) or {}
        dims = _dims_agg(ss, _mean([x["rc"] for x in ss]), mn)
        flat = []
        for dm in dims:
            for it in dm["items"]:
                if not it.get("few") and it.get("ex") is not None:
                    flat.append({"dim": dm["name"], "key": dm["key"], "k": it["k"],
                                 "n": it["n"], "win": it["win"], "rc": it["rc"],
                                 "ri": it["ri"], "zt": it["zt"], "ex": it["ex"]})
        flat.sort(key=lambda x: -x["ex"])
        tp, bt = flat[:5], flat[-5:][::-1]
        packs["w%d" % w] = {
            "win": w,
            "from": dates[-w] if len(dates) >= w else dates[0],
            "to": today,
            "minN": mn,
            "base": {"n": b.get("n"), "win": b.get("win"), "rc": b.get("rc"),
                     "ri": b.get("ri"), "zt": b.get("zt")},
            "dims": dims,
            "cross": [_cross(ss, _mean([x["rc"] for x in ss]), "boards", "seal", "板位 × 封板时间", max(3, int(MIN_N_CROSS * w / 20))),
                      _cross(ss, _mean([x["rc"] for x in ss]), "stage", "boards", "四阶段 × 板位", max(3, int(MIN_N_CROSS * w / 20)))],
            "top": tp,
            "bottom": bt,
            "stocks": _stocks(ss, NAME, min_stock=(1 if w <= 3 else MIN_N_STOCK)),
            "cand": _candidates(today_samples, dims, tp, bt),
        }

    return {
        "today": today,
        "windows": list(packs.keys()),
        "packs": packs,
        "rule": ("样本＝T 日涨停股（剔除一字，当天买不进）在 T+1 的验证。三个结果口径："
                 "①昨收买入→今收（持筹者，主排序）；②今开买入→今收（接力者日内）；"
                 "③T+1 是否再涨停（晋级）。停牌/缺K线/首日无昨收的样本剔除。"
                 "窗口＝近 1/3/5 个交易日（默认 1 日）；单档门槛按窗口缩放（1日≥3笔 / 3日≥4笔 / 5日≥7笔），"
                 "不足门槛的档照实列出但标灰、不参与最强/最弱排序与候选打分。"
                 "每档「超额」＝该档笔均 − 同窗口全样本笔均。"
                 "明日待验证按所选窗口独立打分，特征分≤0 的不列。"
                 "特征全部来自当日涨停池与环境，不做任何预设结论——数据说什么就是什么。"),
    }
