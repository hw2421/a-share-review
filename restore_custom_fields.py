# -*- coding: utf-8 -*-
"""v116：build_merged 重建后轻量恢复 v113/v114/v115 三个自定义字段。

背景：build_merged.py 只生成近窗数据（eventsAll≈近140天、无 histSim/shapeAll），
每次自动重建都会把 v113 全历史 eventsAll(3444)、v114 逐笔模拟 histSim(3443)、
v115 全连板形态 shapeAll(2114) 冲掉，导致历史复盘统计空、连板梯队历史形态缺失。

本脚本在 build_merged 写盘之后调用，**不加载全量日K**（8GB cgroup 会 OOM），
仅以 output/custom_fields_base.json 为基线，从当前 data.json 已构建好的
dayZT / ladderFull / 近窗 eventsAll 中提取增量，合并后原地重写。

用法：python3 restore_custom_fields.py
"""
import json, os, sys

OUT = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, OUT)
import datajs

DATA_F = os.path.join(OUT, "data.json")
BASE_F = os.path.join(OUT, "output", "custom_fields_base.json")


def jload(p, default):
    try:
        return json.load(open(p, encoding="utf-8"))
    except Exception:
        return default


def main():
    data = jload(DATA_F, None)
    base = jload(BASE_F, None)
    if not data or not base:
        print("restore_custom_fields: 缺少 data.json 或基线快照，跳过")
        return 1

    # ---------- 1) eventsAll 全历史 ----------
    cur = (data.get("earnStreak") or {}).get("eventsAll") or []
    merged = {}
    for e in base["eventsAll"]:
        if str(e.get("s")) == "赚T":
            e = dict(e)
            e["s"] = "赚钱"
        merged[(e["d"], str(e["code"]))] = e
    for e in cur:  # 当前近窗（含当日新事件）覆盖/追加
        merged[(e["d"], str(e["code"]))] = e
    evs = sorted(merged.values(), key=lambda x: x["d"], reverse=True)
    data["earnStreak"]["eventsAll"] = evs
    data["earnStreak"]["allRange"] = [evs[-1]["d"], evs[0]["d"]] if evs else base.get("allRange")

    # ---------- 2) shapeAll 全连板形态（基线 + 当前已构建形态） ----------
    shapeAll = base.get("shapeAll", {})
    # 从当前 ladderFull 提取各日期个股形态（build_merged 已算好，无需日K重算）
    lf = data.get("ladderFull", {})
    changed = 0
    for date, themes in lf.items():
        m = dict(shapeAll.get(date, {}))
        for th, arr in themes.items():
            for x in arr:
                s = x.get("s", "")
                code = str(x.get("code", ""))
                if code and s:
                    m[code] = s
        if m and m != shapeAll.get(date):
            shapeAll[date] = m
            changed += 1
    # 从 dayZT 兜底补充（[name,time,s,boards,theme]）——仅补形态为空的
    dz = data.get("dayZT", {})
    for date, stocks in dz.items():
        m = dict(shapeAll.get(date, {}))
        for code, v in stocks.items():
            if isinstance(v, list) and len(v) >= 3 and v[2] and not m.get(str(code)):
                m[str(code)] = v[2]
        if m and m != shapeAll.get(date):
            shapeAll[date] = m
            changed += 1
    data["shapeAll"] = shapeAll

    # ---------- 3) histSim 逐笔模拟（基线原样保留） ----------
    # 今日及最近事件无次日完整走势，历史上本就剔除「末端未断板」笔，
    # 故直接沿用基线 3443 笔；待后续自然扩展基线快照即可。
    data["earnStreak"]["histSim"] = base.get("histSim", {"rule": "", "trades": []})

    print("restore_custom_fields: eventsAll %d (%s~%s) / shapeAll %d 天(更新%d) / histSim %d 笔"
          % (len(evs), evs[-1]["d"] if evs else "-", evs[0]["d"] if evs else "-",
             len(shapeAll), changed, len(data["earnStreak"]["histSim"]["trades"])))
    print("data.js 重写：" + datajs.write_obj(data, OUT))
    return 0


if __name__ == "__main__":
    sys.exit(main())
