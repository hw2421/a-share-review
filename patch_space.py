# -*- coding: utf-8 -*-
"""增量补丁：用涨停池磁盘缓存重算空间龙头（新增"连续一字板无换手不计空间"口径），
不重新抓网（money / klines / themeDetail 保持不变）"""
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import fetch_data as fd

OUT = os.path.dirname(os.path.abspath(__file__))
data = json.load(open(os.path.join(OUT, "data.json"), encoding="utf-8"))
cache = json.load(open(os.path.join(OUT, ".pool_cache.json"), encoding="utf-8"))

pools = {d: v for d, v in cache.items() if v}
dates = sorted(pools)
print("交易日 %d 天（%s ~ %s）" % (len(dates), dates[0], dates[-1]))

# 连板数重算（与主流程一致）
streaks = fd.compute_streaks(pools)
for d in dates:
    for s in pools[d]:
        s["boards"] = streaks[d].get(s["code"], 1)
pool_map_by_date = {d: {s["code"]: s for s in pools[d]} for d in dates}

# 重算四阶段 / 空间龙头 / 压制
days = fd.build_stages(pools)

# 空间龙头 / 压制龙头 回填题材（与主流程一致）
theme_detail = data["themeDetail"]


def theme_of(code, d, idx):
    for k in range(idx, max(-1, idx - 4), -1):
        dd = dates[k]
        for t in theme_detail.get(dd, []):
            if any(s["code"] == code for s in t["stocks"]):
                return t["name"]
    return ""


for k, day in enumerate(days):
    for s in day.get("spaceFull", []):
        s["theme"] = theme_of(s["code"], day["date"], k)
    for s in day.get("suppressStocks", []):
        s["theme"] = theme_of(s["code"], day["date"], k)

data["stages"] = days

# 空间龙头连板周期线重算
seen = {}
for day in days:
    for s in day.get("spaceFull", []):
        if s["code"] not in seen:
            seen[s["code"]] = {"name": s["name"], "theme": s.get("theme", ""), "pts": []}
        seen[s["code"]]["theme"] = s.get("theme", "") or seen[s["code"]]["theme"]
space_series = []
for code, obj in seen.items():
    for dt in dates:
        s2 = pool_map_by_date[dt].get(code)
        if s2 and s2.get("boards", 1) >= 1:
            obj["pts"].append([dt, s2["boards"]])
    if obj["pts"] and max(p[1] for p in obj["pts"]) >= 3:
        obj["maxB"] = max(p[1] for p in obj["pts"])
        space_series.append(obj)
space_series.sort(key=lambda o: (-o["maxB"], o["pts"][0][0]))
data["spaceSeries"] = space_series
print("空间周期线 %d 条" % len(space_series))

# recentPools 加 ay 标志（供前端刷新递推一字口径）
yz_all = fd.yizi_map(pools)
data["recentPools"] = {
    dt: [{"code": s["code"], "name": s["name"], "boards": s["boards"],
          "ay": 1 if fd.all_yizi_run(s["code"], s["boards"],
                                     dates.index(dt), dates, yz_all) else 0}
         for s in pools[dt]]
    for dt in dates[-3:]
}

# 统计被排除的样本
excl = 0
for day in days:
    for s in day.get("note", "").split("；"):
        if s.startswith("一字连板不计空间"):
            excl += 1
print("出现'一字连板不计空间'提示的天数:", excl)

with open(os.path.join(OUT, "data.json"), "w", encoding="utf-8") as f:
    json.dump(data, f, ensure_ascii=False)
with open(os.path.join(OUT, "data.js"), "w", encoding="utf-8") as f:
    f.write("window.REVIEW=" + json.dumps(data, ensure_ascii=False) + ";")
print("完成 -> data.json / data.js")
