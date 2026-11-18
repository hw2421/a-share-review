# -*- coding: utf-8 -*-
"""v29 板块赚钱效应验证报告（用户口径：爬取题材梯队 + 同花顺日K 实证）。

运行：python3 verify_money_effect.py
输出全期通过率、未通过原因、三种买法收益分布与逐年稳定性；
同时写出 board_earn_v29.json 供 build_merged / strategies 使用（口径唯一来源 money_effect.compute）。
"""
import json, os
from collections import defaultdict
import money_effect as ME

OUT = os.path.dirname(os.path.abspath(__file__))
EFF, D, KB = ME.from_data_json()
rows = [(b, t, v) for (b, t), v in EFF.items()]
n = len(rows)
okc = sum(1 for _, _, v in rows if v["ok"])

print("日K股票 %d 只；赚钱效应题材日 %d 个（B 日 ≥3板再涨停 + 板块≥%d只涨停）" %
      (len(KB), n, ME.MIN_MONEY_STOCKS))
print("=" * 96)
print("验证口径：①梯队完整（≥3板龙头+≥2板助攻+首板）②昨日买龙头者今日可获利了结")
print("          ③低吸 ④半路 ⑤打板做跟风，三种买法平均收益均 ≥ −5%")
print("=" * 96)
print("通过 %d / %d = %.1f%%" % (okc, n, okc / n * 100 if n else 0))

reasons = defaultdict(int)
for _, _, v in rows:
    if not v["ok"]:
        reasons[str(v.get("reason", ""))[:26]] += 1
print("\n未通过原因 TOP10：")
for k, c in sorted(reasons.items(), key=lambda x: -x[1])[:10]:
    print("   %-30s %d" % (k, c))

print("\n通过样本的三种买法平均收益（B 日买入、收盘卖出）：")
for key, label in (("low", "低吸(最低价买)"), ("mid", "半路((开+高)/2买)"),
                   ("board", "打板(涨停价买)")):
    vals = sorted(v[key] for _, _, v in rows if v["ok"] and v[key] is not None)
    if vals:
        print("   %-20s 均值%+.2f%% 中位%+.2f%% 最差%+.2f%% 最好%+.2f%%" %
              (label, sum(vals) / len(vals), vals[len(vals) // 2], vals[0], vals[-1]))

print("\n逐年通过率：")
for y in sorted(set(b[:4] for b, _, _ in rows)):
    sub = [v for b, _, v in rows if b[:4] == y]
    o = sum(1 for v in sub if v["ok"])
    print("   %s 样本%4d 通过%4d (%.1f%%)" % (y, len(sub), o, o / len(sub) * 100))

# 紧凑版：只留 ok + 关键明细，供 data.json 前端展示
slim = {}
for (b, t), v in EFF.items():
    slim.setdefault(b, {})[t] = {
        "ok": bool(v["ok"]),
        "ladder": v["ladder"],
        "lead": v["lead"],
        "low": v["low"], "mid": v["mid"], "board": v["board"],
        "nFol": v["nFol"],
        "reason": v["reason"] if not v["ok"] else "",
    }
json.dump(slim, open(os.path.join(OUT, "board_earn_v29.json"), "w", encoding="utf-8"),
          ensure_ascii=False)
print("\n已写出 board_earn_v29.json（%d 个交易日）" % len(slim))
