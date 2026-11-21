# -*- coding: utf-8 -*-
"""v73 历史复盘：历史所有赚钱效应股整理 + 断板跟踪 + 空间龙头叠加。

用户口径：
  1. 历史所有赚钱效应股按日期排列（日期标记当日市场阶段）；
  2. 同一日期按题材排列，同一题材按高度（板位）排列；点题材看当日题材梯队；
  3. 所有赚钱效应股断板后跟踪三天，没有再次涨停就不再显示（终止 → 移出展示）；
  4. 叠加显示空间龙头 + 上一个断板空间龙头（同样断板后跟踪三天，不涨停不再显示）；
  5. 每个个股都显示空间高度（个股连板高度 + 当日市场空间高度）。

v74 扩展：
  6. 日期连续：窗口内所有交易日均有输出（当日无赚钱效应股 → themes=[]，前端显示「无」）；
  7. 个股涨停形态：缺口 / 长腿（缺口 = 当日最低价 > 昨日最高价，否则视为长腿）；
  8. 断板空间龙头显示当日涨跌幅（chg）与当日是否炸板（zb，取炸板池）。

跟踪规则（与 v67 brk_track 三日窗口一致）：
  断板日 = 连续涨停后的第一个未涨停交易日；自断板日起 3 个交易日窗口内
  （断板日本身必不涨停，实际机会为断板后 2 日）再次涨停 → 继续 alive，
  以再板日为新起点重复跟踪；窗口走完仍无涨停 → dead，不再显示。

数据契约：
  days = {交易日 d（窗口内连续）: {
    "stage": 当日市场阶段, "spaceTop": 当日市场空间高度,
    "leader":  {code,name,boards,state,gap} 或 None,   # 当日空间龙头
    "prevBrk": {code,name,boards,bd,days:[[d,chg,zt]...],re,state,chg,zb} 或 None,
               # 上一个仍存活的断板空间龙头（chg=当日涨跌幅, zb=当日炸板）
    "themes": [ {"th": 题材, "stks": [ {code,name,boards,state,track,gap} ]} ]
               # 仅 alive 的赚钱效应股；题材内按板位降序；无事件日为 []
  }}
"""
import bisect

ALL_N = 140          # 与 earnStreak 事件池覆盖一致


def compute(dates, pools, KBARS, events, days_meta, NAME=None, days_num=ALL_N,
            zb_days=None):
    NAME = NAME or {}
    idx = {d: i for i, d in enumerate(dates)}
    n = len(dates)
    pmap = {d: {str(s["code"]): s for s in pools.get(d, [])} for d in dates}

    def is_zt(c, d):
        return str(c) in pmap.get(d, {})

    def close(c, d):
        b = (KBARS.get(str(c)) or {}).get(d)
        return b[2] if (b and len(b) >= 5 and b[2]) else None

    def chg(c, d):
        i = idx.get(d)
        if not i:
            return None
        pv, cc = close(c, dates[i - 1]), close(c, d)
        if pv and cc:
            return round((cc / pv - 1) * 100, 2)
        return None

    # ---- v74：个股自身日K序列（缺口 / 停牌顺延涨跌幅计算） ----
    _sdates = {}

    def sdates(c):
        if c not in _sdates:
            rec = KBARS.get(str(c)) or {}
            _sdates[c] = sorted(k for k, b in rec.items()
                                if b and len(b) >= 5 and b[2])
        return _sdates[c]

    def bar(c, d):
        b = (KBARS.get(str(c)) or {}).get(d)
        return b if (b and len(b) >= 5 and b[2]) else None

    def prev_bar(c, d):
        ds = sdates(c)
        k = bisect.bisect_left(ds, d)
        for j in range(k - 1, -1, -1):
            b = (KBARS.get(str(c)) or {}).get(ds[j])
            if b and len(b) >= 5 and b[2]:
                return b
        return None

    def chg_own(c, d):
        """个股在交易日 d 的涨跌幅（按自身上一根日K，停牌顺延），无数据返回 None。"""
        b, pb = bar(c, d), prev_bar(c, d)
        if b and pb and pb[2]:
            return round((b[2] / pb[2] - 1) * 100, 2)
        return None

    def gap_of(c, d):
        """涨停形态：缺口=当日最低价(b[3])>昨日最高价(pb[4])；否则长腿。无日K返回 None。"""
        b, pb = bar(c, d), prev_bar(c, d)
        if not b or not pb or len(b) < 5 or len(pb) < 5:
            return None
        return 1 if b[3] > pb[4] else 0

    zbmap = zb_days or {}

    def _st(c, d):
        s = pmap.get(d, {}).get(str(c)) or {}
        return s.get("name") or NAME.get(str(c)) or str(c), s.get("boards") or 1

    def track(c, start_d):
        """自 start_d（涨停日）向前跟踪。返回 (alive, state, breaks)
        breaks = [{"bd": 断板日, "days": [[d,chg,zt],...], "re": 再涨停日或""}, ...]"""
        if start_d not in idx:
            return False, "", []
        i = idx[start_d]
        breaks = []
        while True:
            while i + 1 < n and is_zt(c, dates[i + 1]):
                i += 1
            if i + 1 >= n:
                return True, "连板中", breaks
            bd_i = i + 1
            seg, re_i = [], None
            for k in range(3):
                if bd_i + k >= n:
                    break
                d = dates[bd_i + k]
                zt = 1 if is_zt(c, d) else 0
                seg.append([d, chg(c, d), zt])
                if zt and k >= 1:
                    re_i = bd_i + k
                    break
            breaks.append({"bd": dates[bd_i], "days": seg,
                           "re": dates[re_i] if re_i is not None else ""})
            if re_i is None:
                if len(seg) < 3:              # 窗口未走完（数据末端）→ 仍存活
                    return True, "断板第%d天" % len(seg), breaks
                return False, "终止", breaks
            i = re_i

    def state_of(alive, breaks):
        if not breaks:
            return "连板中"
        last = breaks[-1]
        if last["re"]:
            return "断板后再涨停"
        return "断板第%d天" % len(last["days"])

    # ---- 空间龙头序列（tenure）与断板跟踪 ----
    tenures = []          # {code,name,boards,start,end,bd,alive,track}
    cur = None
    for d in dates:
        meta = next((x for x in days_meta if x.get("date") == d), None) or {}
        sf = meta.get("spaceFull") or []
        if not sf:
            if cur:
                cur["end"] = d
                cur = None
            continue
        s0 = sf[0]
        code = str(s0.get("code") or "")
        if cur and cur["code"] == code:
            cur["end"] = d
            cur["boards"] = max(cur["boards"], s0.get("boards") or 1)
        else:
            if cur:
                tenures.append(cur)
            cur = {"code": code, "name": s0.get("name") or NAME.get(code) or code,
                   "boards": s0.get("boards") or 1, "start": d, "end": d}
    if cur:
        tenures.append(cur)
    for t in tenures:
        t["alive"], t["state"], t["track"] = track(t["code"], t["end"])
        t["bd"] = t["track"][-1]["bd"] if t["track"] else ""

    def leader_state(code, d):
        """当日空间龙头状态：涨停(第X板) / 断板第X天 / 数据末端"""
        i = idx[d]
        if is_zt(code, d):
            _, b = _st(code, d)
            return "涨停 %d板" % b
        for k in range(1, 4):
            if i - k < 0:
                break
            if is_zt(code, dates[i - k]):
                return "断板第%d天" % k
        return "未涨停"

    # ---- 赚钱效应股事件（按 code+d 去重，保留最高板） ----
    best = {}
    for e in events or []:
        if not e or not e.get("code") or e.get("d") not in idx:
            continue
        k = (str(e["code"]), e["d"])
        if k not in best or (e.get("b") or 1) > best[k].get("b", 0):
            best[k] = e
    by_date = {}
    for (code, d), e in best.items():
        by_date.setdefault(d, []).append(e)

    out = {}
    n_alive = 0
    lo = dates[-days_num] if (days_num and days_num < n) else (dates[0] if dates else "")
    meta_by = {x.get("date"): x for x in days_meta}
    for d in dates:                          # v74：窗口内日期连续（无事件日 themes=[]）
        if d < lo:
            continue
        meta = meta_by.get(d) or {}
        rows = []
        for e in by_date.get(d) or []:
            code = str(e["code"])
            alive, _st_t, brks = track(code, d)
            if not alive:
                continue                      # 断板 3 日未再涨停 → 不再显示
            n_alive += 1
            rows.append({"code": code,
                         "name": e.get("name") or NAME.get(code) or code,
                         "boards": e.get("b") or 1,
                         "theme": e.get("theme") or "",
                         "gap": gap_of(code, d),
                         "state": _st_t if _st_t else state_of(alive, brks),
                         "track": brks})
        grp = {}
        for r in rows:
            grp.setdefault(r["theme"] or "·无题材", []).append(r)
        themes = [{"th": th,
                   "stks": sorted(v, key=lambda x: -x["boards"])}
                  for th, v in sorted(grp.items(),
                                      key=lambda kv: -max(x["boards"] for x in kv[1]))]
        # 当日空间龙头
        sf = meta.get("spaceFull") or []
        leader = None
        if sf:
            s0 = sf[0]
            _lc = str(s0.get("code") or "")
            leader = {"code": _lc, "name": s0.get("name") or "",
                      "boards": s0.get("boards") or 1,
                      "gap": gap_of(_lc, d),
                      "state": leader_state(_lc, d)}
        # 上一个仍存活的断板空间龙头（断板日 <= d 的最近一个 alive tenure）
        prev = None
        for t in tenures:
            if not t["alive"] or not t["bd"] or t["bd"] > d:
                continue
            if t["code"] == (leader or {}).get("code"):
                continue
            if prev is None or t["bd"] > prev["bd"]:
                prev = t
        prevBrk = None
        if prev:
            last = prev["track"][-1] if prev["track"] else None
            prevBrk = {"code": prev["code"], "name": prev["name"],
                       "boards": prev["boards"], "bd": prev["bd"],
                       "days": (last or {}).get("days", []),
                       "re": (last or {}).get("re", ""),
                       "chg": chg_own(prev["code"], d),          # v74：当日涨跌幅
                       "zb": 1 if str(prev["code"]) in (zbmap.get(d) or {}) else 0,
                       "state": prev["state"]}
        out[d] = {"stage": meta.get("stage") or "",
                  "spaceTop": meta.get("spaceTop"),
                  "leader": leader, "prevBrk": prevBrk,
                  "themes": themes}
    return {
        "days": out,
        "allRange": [dates[0], dates[-1]] if dates else [],
        "rule": ("历史复盘：窗口内交易日连续展示（当日无赚钱效应股显示「无」），日期带市场阶段与空间高度，"
                 "同一日期按题材、题材内按板位降序；断板后 3 个交易日内再次涨停继续显示，否则移除；"
                 "叠加当日空间龙头与上一个仍存活的断板空间龙头（含当日涨跌幅与炸板标记）辅助判断；"
                 "个股涨停形态按缺口/长腿标注（缺口＝当日最低价＞昨日最高价）。"),
    }, n_alive
