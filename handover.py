# -*- coding: utf-8 -*-
"""新旧龙头交接事件序列（v55）。

口径：
  一条「交接事件」= 旧龙头批次断板退出 → 新龙头批次上位的那一次更替。
  · 旧龙头 old：更替前最后在位的空间/板块龙头批次（含 code/name/boards/断板日）；
  · 新龙头 new：更替当日新上位的最高板批次（同身位多个备选全部带上）；
  · 同一天若有多个同身位龙头备选，一起进入 old/new，供前端叠加显示。

两套序列：
  space —— 全市场空间龙头交接（取 stages[].spaceFull 的最高板批次）；
  theme —— 每个题材内部的板块龙头交接（取 ladderFull[d][题材] 的最高板批次）。

后端只传元数据(code/name/boards/date)；前端用 stockKBars(code) 取各自完整日K，
按交接日对齐窗口、以窗口首日收盘归一化后叠加（归一化涨幅叠加，用户 v55 定稿）。
"""
from collections import defaultdict


def _top_batch(stocks):
    """返回同身位(最高板)的备选批次；stocks=[{code,name,boards},...]。"""
    if not stocks:
        return []
    mx = max((int(x.get("boards") or 1) for x in stocks), default=0)
    batch = []
    seen = set()
    for x in stocks:
        if int(x.get("boards") or 1) != mx:
            continue
        c = str(x.get("code") or "")
        if not c or c in seen:
            continue
        seen.add(c)
        batch.append({"code": c, "name": x.get("name") or "", "boards": mx})
    return batch


def _chain(seq, limit=40):
    """seq=[(date,[{code,name,boards}...]), ...]（升序）。检测龙头批次更替。"""
    events = []
    prev_codes, prev_leaders, prev_date = set(), [], None
    for date, leaders in seq:
        if not leaders:
            continue
        codes = {x["code"] for x in leaders}
        # 与前一批完全无交集 → 判定为一次交接（旧龙头已断板退出）
        if prev_codes and not (codes & prev_codes):
            events.append({
                "date": date,
                "breakDate": prev_date,
                "old": [dict(x, lastDate=prev_date) for x in prev_leaders],
                "new": [dict(x) for x in leaders],
            })
        if codes != prev_codes:
            prev_codes, prev_leaders, prev_date = codes, leaders, date
    return events[-limit:]


def compute(dates, stages, ladder_full, NAME=None, limit=40):
    NAME = NAME or {}
    sd = {s["date"]: s for s in (stages or [])}

    # ---- 空间维度：全市场空间龙头批次 ----
    sp_seq = []
    for d in dates:
        s = sd.get(d) or {}
        full = [x for x in (s.get("spaceFull") or [])
                if int(x.get("boards") or 1) >= 2]
        b = _top_batch(full)
        if b:
            sp_seq.append((d, b))
    space_events = _chain(sp_seq, limit)

    # ---- 题材维度：每个题材的板块龙头批次 ----
    theme_map = defaultdict(list)
    for d in dates:
        for th, arr in (ladder_full.get(d) or {}).items():
            b = _top_batch(arr)
            if b:
                theme_map[th].append((d, b))
    theme_events = {}
    for th, seq in theme_map.items():
        seq.sort(key=lambda x: x[0])
        ev = _chain(seq, limit)
        if ev:
            theme_events[th] = ev

    return {
        "space": space_events,
        "theme": theme_events,
        "rule": ("新旧龙头交接：旧龙头批次断板退出→新龙头批次上位的那次更替；"
                 "空间龙头取当日全市场最高板，板块龙头取该题材当日最高板；"
                 "同一天多个同身位备选一起叠加。点开后按交接日对齐各自日K、"
                 "以窗口首日收盘归一化后叠加显示，可切换上一次/下一次。"),
    }
