# -*- coding: utf-8 -*-
"""空间情绪口径一致性校验（可重复运行）：python3 verify_space.py
校验 data.json 的 stages 与涨停池是否自洽，逐条对照《README · 术语》：

  1. 空间龙头  spaceFull 必须都在当日涨停池内，板数 == spaceTop，且非全程一字、非次新
  2. 空间高度  spaceTop == max(spaceFull.boards)，且 maxBoard >= spaceTop
  3. 空间压制  suppress 只能来自「上一日空间龙头整批断板」，高度 >= 2，且等于该批最高板
  4. 突破      阶段为突破/主升的日子，spaceTop 必须 > 突破前的 suppress
  5. 连续性    压制线相邻两日变化只能是「保持 / 清空 / 改写为当日断板龙头高度」
  6~8. 题材梯队 / 题材块 ladderFull 同源、全站可点个股日K覆盖
  9. 断板后5天 每个「可点击股票日」的日K至少延伸到其后 5 个交易日（停牌/退市豁免）
 10. 赚钱效应  只保留「板块当日 ≥3 板个股再涨停 + 板块至少 3 只涨停」的题材、
               卡片只含新口径字段、梯队与 ladders 同源、avg2 仅统计 2 板+ 个股、
               stage 与题材炒作页四分类（启动/加强/转弱/反复）逐日一致
 11. 涨停时间  梯队个股 first_seal 统一 HH:MM，连板梯队 5 列
 12. 三战法复盘  逐笔自洽：价格/卖出日存在、盈亏可复算、卖出原因合法、
                 封板标记齐全；并校验 K线骷髅头与题材前龙已移除
 12.2 机会池    chances 含一字买不进/炸板、逐笔是其子集
 12.3 v32 口径   两战法独立复算：战法一仅「N板压制 + 昨日板块助攻 + 当日 N+1 板触板」三点（其余条件已删）；战法二板块赚钱效应/梯队/高开；一字不进逐笔；boardEarn 与 money_effect.compute 全量一致；META 无历史战法残留
 12.4 v19 口径   所有战法（含战法一）买入须有板块助攻：买入当日或前一交易日，
                 所属板块内（本股之外）有 2 板+ 真实涨停连板个股（独立重算）
 12.5 v20 口径   阶段门禁（节点例外/主升不买空间龙头）、各战法新条件留痕、
                 黑盒=前三战法买点并集、META 新文案生效（独立重算）
 12.6 v21 口径   试错龙头只在节点次日参与、凹凸曼在节点日不参与：两战法逐笔/机会池
                 买入当日不得为「节点」、不得带节点例外留痕；节点例外仅对空间突破/
                 补涨（及跟随的黑盒）放行；v21stat 与 META 新文案生效（独立重算）
 12.7 v22 口径   赚钱效应示范全局门禁（市场级 + 板块级）：每条逐笔须带两级示范留痕、
                 阈值 0.50、v22stat 生效、五个战法 buy 均有「核心：」主线、uni 含新定义
 13. 历史日历  2021-2025 由 Excel OCR 补池，OCR 覆盖的交易日必须全部进入主数据
              （若某日池为空而缺席，compute_streaks 会跨缺口把不相邻两日误判为连续涨停）
退出码 0 = 全部通过；1 = 存在问题（逐条打印）
"""
import glob
import json
import os
import re
import sys

OUT = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, OUT)
import fetch_data as fd

CUT = "20260101"


def load_pools():
    ec = json.load(open(os.path.join(OUT, ".excel_cache.json"), encoding="utf-8"))
    pc = fd.load_pool_cache()
    pools = {}
    for d, v in ec["pools"].items():
        if d < CUT and v:
            pools[d] = v
    for d, v in pc.items():
        if d >= CUT and v:
            pools[d] = v
    pools = {d: v for d, v in pools.items() if v}
    hb = json.load(open(os.path.join(OUT, ".hunan_boards.json"), encoding="utf-8"))
    ovp = os.path.join(OUT, ".board_overrides.json")
    if os.path.exists(ovp):
        ov = json.load(open(ovp, encoding="utf-8"))
        for d, m in ov.items():
            if d.startswith("_"):
                continue
            for c, b in m.items():
                hb.setdefault(d, {})[c] = b
    st = fd.compute_streaks(pools, hb)
    for d in pools:
        for s in pools[d]:
            s["boards"] = st[d].get(s["code"], 1)
    return pools


def main():
    D = json.load(open(os.path.join(OUT, "data.json"), encoding="utf-8"))
    days = D["stages"]
    dates = [d["date"] for d in days]
    pools = load_pools()
    yz = fd.yizi_map(pools)
    bad = []

    def err(msg):
        bad.append(msg)

    def valid_code(c):
        """A 股合法 6 位代码前缀；复盘图 OCR 会产生 660000 / 800130 等无效码，
        这些票本来就没有行情，不纳入日K覆盖率与断板后 5 天的考核。"""
        c = str(c or "")
        return bool(re.fullmatch(r"\d{6}", c) and c[:2] in
                    ("60", "68", "00", "30", "83", "87", "43", "92", "90"))

    # ---- 1 / 2 空间龙头与空间高度 ----
    for i, day in enumerate(days):
        d = day["date"]
        if d not in pools:
            err("%s 不在涨停池" % d)
            continue
        top = day.get("spaceTop")
        if top is None:
            err("%s 缺 spaceTop 字段" % d)
            continue
        sf = day.get("spaceFull") or []
        pm = {s["code"]: s for s in pools[d]}
        if top == 0:
            if sf:
                err("%s 空间高度 0 但仍有空间龙头 %s" % (d, [s["name"] for s in sf]))
            continue
        if not sf:
            err("%s 空间高度 %d 但 spaceFull 为空" % (d, top))
        for s in sf:
            p = pm.get(s["code"])
            if not p:
                err("%s %s 不在当日涨停池" % (d, s["name"]))
                continue
            if p["boards"] != s["boards"]:
                err("%s %s 板数不一致 data=%d 池=%d"
                    % (d, s["name"], s["boards"], p["boards"]))
            if s["boards"] != top:
                err("%s %s 板数 %d != 空间高度 %d" % (d, s["name"], s["boards"], top))
            if fd.all_yizi_run(s["code"], s["boards"], i, dates, yz):
                err("%s %s 是全程一字连板，不应作为空间龙头" % (d, s["name"]))
            if p.get("new"):
                err("%s %s 是次新股，不应作为空间龙头" % (d, s["name"]))
        # 空间高度必须是"换手口径"最高：不允许存在更高的非一字、非次新涨停股
        for p in pools[d]:
            if p.get("new") or p["boards"] <= top:
                continue
            if not fd.all_yizi_run(p["code"], p["boards"], i, dates, yz):
                err("%s 存在更高换手连板 %s %d板 > 空间高度 %d"
                    % (d, p["name"], p["boards"], top))
        if day.get("maxBoard", 0) < top:
            err("%s maxBoard %d < spaceTop %d" % (d, day.get("maxBoard", 0), top))

    # ---- 3 / 5 空间压制来源（只校验"发生变化"的那天） ----
    for i, day in enumerate(days[1:], 1):
        d, pd_ = day["date"], days[i - 1]["date"]
        sup = day.get("suppress")
        prev = days[i - 1]
        if sup is None:
            continue
        if sup < 2:
            err("%s 压制 %d < 2（首板断板无信息量）" % (d, sup))
        if prev.get("suppress") == sup:
            continue                      # 压制未变化：沿用上一个空间龙头，无需再验
        prev_sf = prev.get("spaceFull") or []
        pm = {s["code"]: s for s in pools.get(d, [])}
        broken = [s for s in prev_sf
                  if not pm.get(s["code"])
                  or pm[s["code"]]["boards"] != s["boards"] + 1]   # 与 build_stages 同口径：未晋级=断板
        if not prev_sf:
            err("%s 有压制但上一日无空间龙头" % d)
        elif len(broken) != len(prev_sf):
            err("%s 上一日空间龙头未整批断板，压制不应改写" % d)
        else:
            bmax = max(s["boards"] for s in broken)
            if bmax != sup:
                err("%s 压制 %d != 上一日断板龙头高度 %d" % (d, sup, bmax))

    # ---- 4 突破 ----
    for i, day in enumerate(days[1:], 1):
        if day.get("stage") not in ("突破", "主升"):
            continue
        prev_sup = days[i - 1].get("suppress")
        if prev_sup is not None and day.get("stage") == "突破":
            if day.get("spaceTop", 0) <= prev_sup:
                err("%s 判为突破但空间高度 %d <= 压制 %d"
                    % (day["date"], day.get("spaceTop", 0), prev_sup))
        if day.get("stage") == "突破" and day.get("suppress") is not None:
            err("%s 突破当日压制应清空，实际仍为 %d"
                % (day["date"], day["suppress"]))

    # ---- 6 题材梯队（明细表/赚钱效应/连板梯队 同源） ----
    LAD = D.get("ladders") or {}
    LB = D.get("lb") or {}
    for day in days:
        d = day["date"]
        lad = LAD.get(d) or {}
        for nm, e in lad.items():
            bl = e.get("boards") or []
            if not bl:
                err("%s %s 梯队板数列表为空" % (d, nm))
                continue
            if bl != sorted(bl, reverse=True):
                err("%s %s 梯队板数未降序 %s" % (d, nm, bl))
            if e.get("maxB") != bl[0]:
                err("%s %s maxB=%s 与梯队最高板 %s 不一致" % (d, nm, e.get("maxB"), bl[0]))
            if e.get("ladder") != "-".join(str(b) for b in bl):
                err("%s %s ladder 字符串与板数列表不一致" % (d, nm))
            if (e.get("count") or 0) < 1:
                err("%s %s count<1" % (d, nm))
        # 空间龙头必须落在自己题材的当日梯队里，且板数要在梯队中（否则就是"4板配3-1"错配）
        for s in day.get("spaceFull") or []:
            nm = s.get("theme")
            if not nm:
                continue
            e = lad.get(nm)
            if not e:
                err("%s 空间龙头 %s 的题材「%s」当日无梯队" % (d, s["name"], nm))
                continue
            if s["boards"] not in (e.get("boards") or []):
                err("%s 空间龙头 %s %d板 不在题材「%s」梯队 %s 中（错配）"
                    % (d, s["name"], s["boards"], nm, e.get("ladder")))
            if s["boards"] > (e.get("maxB") or 0):
                err("%s 空间龙头 %s %d板 > 题材「%s」最高板 %s"
                    % (d, s["name"], s["boards"], nm, e.get("maxB")))
        # 当日 2 板以上的连板股，其题材梯队必须包含该板数
        for row in LB.get(d) or []:
            name, boards, nm = row[0], row[1], (row[2] if len(row) > 2 else "")
            if not nm or not boards or boards < 2:
                continue
            e = lad.get(nm)
            if not e:
                err("%s 连板股 %s 的题材「%s」当日无梯队" % (d, name, nm))
            elif boards not in (e.get("boards") or []):
                err("%s 连板股 %s %d板 不在题材「%s」梯队 %s 中"
                    % (d, name, boards, nm, e.get("ladder")))

    # ---- 7 题材块 / 梯队完整清单（浮层）与 ladders 同源 ----
    TD = D.get("themeDetail") or {}
    LF = D.get("ladderFull") or {}
    for d, blks in TD.items():
        pm = {s["code"] for s in pools.get(d, [])}
        for b in blks:
            nm = b["name"]
            e = (LAD.get(d) or {}).get(nm)
            if not e:
                err("%s 题材块「%s」当日无梯队" % (d, nm))
                continue
            if b.get("count") != e.get("count") or b.get("maxB") != e.get("maxB"):
                err("%s 题材块「%s」count/maxB 与梯队不一致" % (d, nm))
            # v30 题材炒作图入链门槛：板块有连板（≥2板）且板块涨停≥3只
            if e.get("maxB", 0) < 2 or (e.get("count") or 0) < 3:
                err("%s 题材块「%s」未达入链门槛（有连板≥2板且≥3只）" % (d, nm))
            for s in b.get("stocks") or []:
                c = s.get("code")
                if c and c not in pm:
                    err("%s 题材块「%s」含当日未涨停个股 %s" % (d, nm, s.get("name")))
                    break
            f = (LF.get(d) or {}).get(nm)
            if not f:
                err("%s 题材块「%s」缺 ladderFull 完整清单" % (d, nm))
            else:
                if len(f) != e.get("count"):
                    err("%s %s ladderFull %d 只 != count %d"
                        % (d, nm, len(f), e.get("count")))
                fb = sorted({s["boards"] for s in f}, reverse=True)
                if fb != (e.get("boards") or []):
                    err("%s %s ladderFull 板级 %s != 梯队 %s"
                        % (d, nm, fb, e.get("boards")))

    # ---- 8 个股日K覆盖：全站可点个股必须在主包或分片里有日K ----
    allk = set(D.get("klines") or {})
    for fp in glob.glob(os.path.join(OUT, "klines", "klines_*.json")):
        allk |= set(json.load(open(fp, encoding="utf-8")))
    clickable = set()
    for day in days:
        for s in day.get("spaceFull") or []:
            clickable.add(s["code"])
        for s in day.get("suppressStocks") or []:
            clickable.add(s["code"])
    for blks in TD.values():
        for b in blks:
            for s in b.get("stocks") or []:
                if s.get("code"):
                    clickable.add(s["code"])
    for rows in (D.get("lb") or {}).values():
        for r in rows:
            if len(r) >= 4:
                clickable.add(r[3])
    for arr in (D.get("recentPools") or {}).values():
        for s in arr:
            clickable.add(s["code"])
    for s in D.get("todayStocks") or []:
        clickable.add(s["code"])
    invalid = {c for c in clickable if not valid_code(c)}
    clickable -= invalid
    miss = clickable - allk
    if miss:
        err("个股日K缺失 %d 只：%s" % (len(miss), ",".join(sorted(miss)[:10])))
    sk = D.get("supKlines") or {}
    for day in days:
        for s in day.get("suppressStocks") or []:
            if not sk.get(s["code"] + "_" + s["breakDate"]):
                err("%s 压制股 %s 断板日K缺失" % (day["date"], s["name"]))
    print("个股日K：可点 %d 只 / 覆盖 %d 只（OCR无效码豁免 %d）；题材块 %d 个，梯队完整清单 %d 日"
          % (len(clickable), len(clickable & allk), len(invalid),
             sum(len(v) for v in TD.values()), len(LF)))

    # ---- 9 日K窗口：至少显示到「断板后 5 个交易日」 ----
    # 只校验页面上真正可点击的 (个股, 日期)：其余全池历史涨停周期点不到，
    # 分片按「需求日聚簇」裁剪本就不覆盖，不该算缺口。
    idx = {d: i for i, d in enumerate(dates)}
    SB = D.get("stockBreak") or {}
    kdates = {}
    for fp in glob.glob(os.path.join(OUT, "klines", "klines_*.json")):
        for c, bars in json.load(open(fp, encoding="utf-8")).items():
            kdates.setdefault(c, set()).update(b[0] for b in bars)
    for c, bars in (D.get("klines") or {}).items():
        kdates.setdefault(c, set()).update(b[0] for b in bars)
    need = {}
    def add(c, dt):
        if c and dt:
            need.setdefault(str(c), set()).add(dt)
    for day in days:
        for s in (day.get("spaceFull") or []):
            add(s.get("code"), day["date"])
        for s in (day.get("suppressStocks") or []):
            add(s.get("code"), day["date"])
    for dt, blks in TD.items():
        for b in blks:
            for s in b.get("stocks") or []:
                add(s.get("code"), dt)
    for dt, rows in (D.get("lb") or {}).items():
        for r in rows:
            if len(r) >= 4:
                add(r[3], dt)
    for dt, arr in (D.get("recentPools") or {}).items():
        for s in arr:
            add(s.get("code"), dt)
    for s in D.get("todayStocks") or []:
        add(s.get("code"), D.get("today"))
    need = {c: ds for c, ds in need.items() if valid_code(c)}
    n_pt = n_short = n_na = 0
    for c, ds in need.items():
        kd = kdates.get(c) or set()
        tail = max(kd) if kd else ""
        for d in ds:
            i = idx.get(d)
            if i is None:
                continue
            end5 = dates[min(i + 6, len(dates) - 1)]
            if end5 >= dates[-1]:          # 断板后 5 天尚未到来（最新交易日附近）
                continue
            n_pt += 1
            if len([x for x in kd if x > d]) >= 5:
                continue
            # 数据源已终止（停牌/退市）：日K最后一根早于最新交易日且不超过 end5
            if tail and tail < dates[-1] and tail <= end5:
                n_na += 1
                continue
            n_short += 1
            if n_short <= 8:
                err("日K %s 可点日 %s 后不足 5 根（尾部 %s，需 >= %s）"
                    % (c, d, tail, end5))
    if n_short > 8:
        err("…另有 %d 个可点股票日日K不足断板后 5 天" % (n_short - 8))
    for key, bars in sk.items():
        bd = key.rsplit("_", 1)[-1]
        i = idx.get(bd)
        if i is None:
            continue
        end5 = dates[min(i + 5, len(dates) - 1)]
        if end5 >= dates[-1]:              # 断板后 5 天还没到
            continue
        after = len([b for b in bars if b[0] > bd])
        if after < 5 and bars[-1][0] >= end5:
            err("压制股日K %s 断板后仅 %d 根（数据尾部 %s 已够）"
                % (key, after, bars[-1][0]))
    print("断板后5天：可点股票日 %d 个，不足 %d 个，停牌/退市豁免 %d 个；"
          "stockBreak %d 只 / supKlines %d 段"
          % (n_pt, n_short, n_na, len(SB), len(sk)))

    # ---- 10 赚钱效应口径：≥3 板再涨停 + 板块至少 3 只涨停、
    #     日均涨幅仅 2 板+、当日题材阶段与题材炒作页四分类一致、卡片无冗余字段 ----
    M = D.get("money") or {}
    old_keys = ("nAll", "avgAll", "leader", "mids", "items",
                "hasMid", "nFirst", "avgMid")
    # 复算题材四分类阶段（与前端 TStages / build_merged 5.6.0 同一算法）
    TD = D.get("themeDetail") or {}
    tidx = {d: i for i, d in enumerate(dates)}
    occ = {}
    for _d in sorted(TD):
        for _t in TD[_d]:
            _a = occ.setdefault(_t["name"], [])
            if _a[-1:] != [_d]:
                _a.append(_d)
    stage_calc = {}
    for _nm, _ds in occ.items():
        _st = {}
        for _k, _d in enumerate(_ds):
            _c = next((t["count"] for t in TD[_d] if t["name"] == _nm), 0)
            if _k == 0:
                _st[_d] = "启动"; continue
            _gap = tidx[_d] - tidx[_ds[_k - 1]]
            if _gap >= 5:
                _st[_d] = "启动"; continue
            if _gap >= 2:
                _st[_d] = "反复"; continue
            _pc = next((t["count"] for t in TD[_ds[_k - 1]] if t["name"] == _nm), 0)
            _st[_d] = "加强" if _c > _pc else ("转弱" if _c < _pc else "反复")
        stage_calc[_nm] = _st
    # v35：题材炒作页只保留博客列的板块，赚钱效应里由涨停原因归属出来的板块
    # 当天可能没有题材块，此时阶段按「赚钱效应自身序列」补算（build_merged 5.6.1b）。
    _mseq = {}
    for _d in sorted(M):
        for _g in M[_d]:
            _mseq.setdefault(_g["theme"], []).append((_d, _g["count"]))
    stage_money = {}
    for _nm, _arr in _mseq.items():
        _arr.sort()
        _st = {}
        for _k, (_d, _c) in enumerate(_arr):
            if _k == 0:
                _st[_d] = "启动"; continue
            _gap = tidx[_d] - tidx[_arr[_k - 1][0]]
            if _gap >= 5:
                _st[_d] = "启动"; continue
            if _gap >= 2:
                _st[_d] = "反复"; continue
            _pc = _arr[_k - 1][1]
            _st[_d] = "加强" if _c > _pc else ("转弱" if _c < _pc else "反复")
        stage_money[_nm] = _st
    n_grp = 0
    for d, gs in M.items():
        lad = LAD.get(d) or {}
        for g in gs or []:
            n_grp += 1
            nm = g.get("theme")
            if (g.get("maxB") or 0) < 3:
                err("赚钱效应 %s「%s」最高板 %s < 3" % (d, nm, g.get("maxB")))
            if (g.get("count") or 0) < 3:
                err("赚钱效应 %s「%s」板块涨停不足 3 只：%s" % (d, nm, g.get("count")))
            e = lad.get(nm)
            if not e:
                err("赚钱效应 %s「%s」当日无梯队" % (d, nm))
            elif (e.get("maxB") != g.get("maxB") or e.get("ladder") != g.get("ladder")
                  or e.get("count") != g.get("count")):
                err("赚钱效应 %s「%s」梯队与 ladders 不一致" % (d, nm))
            _want = stage_calc.get(nm, {}).get(d) or stage_money.get(nm, {}).get(d)
            if g.get("stage") not in ("启动", "加强", "转弱", "反复"):
                err("赚钱效应 %s「%s」题材阶段缺失或非法：%s" % (d, nm, g.get("stage")))
            elif g["stage"] != _want:
                err("赚钱效应 %s「%s」阶段与题材炒作页不一致：%s != %s"
                    % (d, nm, g["stage"], _want))
            if any(k in g for k in old_keys):
                err("赚钱效应 %s「%s」仍带冗余旧字段" % (d, nm))
            if "avg2" not in g or "n2" not in g or "leadCode" not in g:
                err("赚钱效应 %s「%s」缺新口径字段" % (d, nm))
    print("赚钱效应：%d 个交易日 / %d 个题材组，门槛/阶段/字段全部校验"
          % (len(M), n_grp))

    # ---- 11 涨停时间：梯队完整清单 / 连板梯队 / 题材块个股都带 HH:MM ----
    LF = D.get("ladderFull") or {}
    n_tm = n_lf = 0
    for d, gmap in LF.items():
        for nm, arr in gmap.items():
            for s in arr or []:
                n_lf += 1
                t = s.get("t") or ""
                if t:
                    n_tm += 1
                    if not re.match(r"^\d{2}:\d{2}$", t):
                        err("涨停时间格式异常 %s「%s」%s" % (d, nm, t))
    for d, rows in (D.get("lb") or {}).items():
        for r in rows:
            if len(r) < 5:
                err("连板梯队 %s 缺涨停时间列" % d)
    print("涨停时间：%d/%d 条梯队个股带 HH:MM；连板梯队 %d 日均为 5 列"
          % (n_tm, n_lf, len(D.get("lb") or {})))

    # ---- 12 两战法复盘：逐笔自洽（T 日复盘 → T+1 参与）----
    RVALL = D.get("review") or {}
    if isinstance(RVALL, list):                 # 兼容旧版单战法数组
        RVALL = {"spaceDragon": RVALL}
    RV_METHODS = ("spaceDragon", "trialLead")
    stage_by_d = {x["date"]: x.get("stage") for x in days}
    date_set = set(dates)
    n_rv_bad, n_rv_all = 0, 0
    meta = D.get("reviewMeta") or {}
    for m in RV_METHODS:
        if m not in meta:
            err("战法 %s 缺口径说明 reviewMeta" % m)
        for t in RVALL.get(m, []):
            n_rv_all += 1
            if t.get("d") not in date_set:
                err("回测 %s %s 买入日不在交易日历" % (t["d"], t.get("name")))
                n_rv_bad += 1
            if not (t.get("buy") and t.get("sell") and t.get("sellD")):
                err("回测 %s %s 缺价格/卖出日" % (t["d"], t.get("name")))
                n_rv_bad += 1
                continue
            if t["sellD"] < t["d"]:
                err("回测 %s %s 卖出日早于买入日" % (t["d"], t.get("name")))
                n_rv_bad += 1
            pnl = round((t["sell"] - t["buy"]) / t["buy"] * 100, 2)
            if abs(pnl - t.get("pnl", 999)) > 0.02:
                err("回测 %s %s 盈亏比例不符（%s vs %s）" %
                    (t["d"], t.get("name"), t.get("pnl"), pnl))
                n_rv_bad += 1
            # v15：一字跌停顺延会在原因后追加「（一字跌停顺延N日）」，按前缀校验
            REASONS = ("尾盘未涨停", "盘中-7%止损", "期末仍涨停·按末收盘")
            if not any(str(t.get("reason") or "").startswith(r) for r in REASONS):
                err("回测 %s %s 卖出原因异常 %s" %
                    (t["d"], t.get("name"), t.get("reason")))
                n_rv_bad += 1
            if not isinstance(t.get("sealed"), bool):
                err("回测 %s %s 缺收盘封板标记 sealed" % (t["d"], t.get("name")))
                n_rv_bad += 1
            # v15：买入日炸板标记 / 当日市场情绪阶段 / 个股级符合逻辑
            if t.get("burst") != (not t["sealed"]):
                err("回测 %s %s 买入日炸板标记与封板状态不符" % (t["d"], t.get("name")))
                n_rv_bad += 1
            if t.get("stage") and t["stage"] not in ("节点", "补涨", "突破", "主升"):
                err("回测 %s %s 市场情绪阶段异常 %s" % (t["d"], t.get("name"), t["stage"]))
                n_rv_bad += 1
            if not t.get("why"):
                err("回测 %s %s 缺个股级符合买入逻辑 why" % (t["d"], t.get("name")))
                n_rv_bad += 1
    n_defer = sum(1 for m in RV_METHODS for t in RVALL.get(m, [])
                  if "顺延" in str(t.get("reason") or ""))
    print("交易复盘·两战法：%s，异常 %d 笔（一字跌停顺延 %d 笔）" %
          (" / ".join("%s %d笔" % (m, len(RVALL.get(m, []))) for m in RV_METHODS),
           n_rv_bad, n_defer))

    # ---- 12.1 K线骷髅头 / 题材前龙 ----
    KM = D.get("kMarks") or {}
    n_km2 = 0
    for code, marks in KM.items():
        for dt, lv in marks.items():
            if lv not in (1, 2):
                err("K线标记 %s %s 等级异常 %s" % (code, dt, lv))
            if lv == 2:
                n_km2 += 1
    TPL = D.get("themePrevLead")
    if TPL:
        err("v28 已移除「题材前龙」显示，data.json 不应再包含 themePrevLead 字段")
    print("K线骷髅头 %d 只 / %d 标记（双骷髅 %d）；题材前龙 %s"
          % (len(KM), sum(len(v) for v in KM.values()), n_km2,
             "已移除" if not TPL else "仍存在 %d 日（异常）" % len(TPL)))

    # ---- 12.2 v17 当日机会池 chances + 非突破战法「距压制2板」门禁 ----
    CH = D.get("chances") or {}
    sup_by_d = {x["date"]: (x.get("suppress") or 0) for x in days}
    n_ch, n_ch_bad, n_yizi = 0, 0, 0
    ch_keys = set()
    for d, arr in CH.items():
        if d not in date_set:
            err("机会池 %s 不在交易日历" % d); n_ch_bad += 1
        for o in arr:
            n_ch += 1
            ch_keys.add((o.get("d"), o.get("m"), o.get("code")))
            for f in ("d", "code", "name", "boards", "m", "why"):
                if o.get(f) in (None, ""):
                    err("机会 %s %s 缺字段 %s" % (d, o.get("name"), f)); n_ch_bad += 1
            if o.get("m") not in RV_METHODS:
                err("机会 %s %s 战法异常 %s" % (d, o.get("name"), o.get("m"))); n_ch_bad += 1
            if not isinstance(o.get("tradable"), bool) or not isinstance(o.get("yizi"), bool):
                err("机会 %s %s 缺 tradable/yizi" % (d, o.get("name"))); n_ch_bad += 1
            elif o["tradable"] == o["yizi"]:
                err("机会 %s %s 一字与可参与状态冲突" % (d, o.get("name"))); n_ch_bad += 1
            if o.get("yizi"):
                n_yizi += 1
    # 逐笔必须是机会池的子集（每条可参与模拟的交易都应有一条对应机会）
    n_notin = 0
    for m in RV_METHODS:
        for t in RVALL.get(m, []):
            if (t["d"], m, t["code"]) not in ch_keys:
                err("逐笔 %s %s 无对应机会记录" % (t["d"], t.get("name")))
                n_notin += 1
    print("当日机会池：%d 日 / %d 条机会（一字买不进 %d 条）；逐笔无对应机会 %d；异常 %d" %
          (len(CH), n_ch, n_yizi, n_notin, n_ch_bad))

    # ---- 12.3 v32：两战法口径（空间突破已精简；试错龙头不变）----
    # 独立复算：同花顺日K + 题材梯队 + 赚钱效应验证，不依赖 strategies 自证
    LFV = D.get("ladderFull") or {}
    V23 = D.get("v23stat") or {}
    MV = D.get("money") or {}
    BE = D.get("boardEarn") or {}
    stage_full = {x["date"]: x for x in days}
    DIDXV = {d: i for i, d in enumerate(dates)}
    KBV = {}
    for _fp in glob.glob(os.path.join(OUT, "klines", "klines_*.json")):
        for _c, _bars in json.load(open(_fp, encoding="utf-8")).items():
            _rec = KBV.setdefault(str(_c), {})
            for _b in _bars:
                if _b and len(_b) >= 5:
                    _rec[_b[0]] = _b

    def _bar(c, d):
        return (KBV.get(str(c)) or {}).get(d)

    def _pv(c, d):
        rec = KBV.get(str(c)); i = DIDXV.get(d)
        if not rec or i is None:
            return None
        for j in range(i - 1, -1, -1):
            b = rec.get(dates[j])
            if b and len(b) >= 3 and b[2]:
                return b[2]
        return None

    def _lim(c, name=""):
        if "ST" in (name or "").upper():
            return .05
        if str(c).startswith(("30", "688")):
            return .20
        if str(c).startswith(("8", "4", "92")):
            return .30
        return .10

    def _touch(c, d, name=""):
        b, pv = _bar(c, d), _pv(c, d)
        return bool(b and pv and b[4] / pv - 1 >= _lim(c, name) - .006)

    def _highopen(c, d, name, buy_px):
        """高开但不是涨停开盘：以逐笔买价（涨停价）反推昨收，规避除权日未复权价。"""
        b = _bar(c, d)
        if not b or not buy_px:
            return False
        lim = _lim(c, name)
        prev_adj = buy_px / (1 + lim)
        return b[1] > prev_adj * 1.0001 and b[1] < buy_px - .001

    def _yizi(c, d, name=""):
        b, pv = _bar(c, d), _pv(c, d)
        return bool(b and pv and b[1] == b[2] == b[3] == b[4]
                    and b[2] / pv - 1 >= _lim(c, name) - .006)

    def _break_ok(T):
        """上一空间龙头断板日无大长阴、次日无跌停（与 strategies 同口径独立复算）。"""
        for s0 in (stage_full.get(T, {}).get("suppressStocks") or []):
            c, nm = str(s0.get("code") or ""), s0.get("name", "")
            bd = str(s0.get("breakDate") or "")
            if not c or not bd or bd > T:
                continue
            b, pv = _bar(c, bd), _pv(c, bd)
            if not b or not pv:
                continue
            chg = b[2] / pv - 1
            entity = (b[1] - b[2]) / pv
            if chg <= -.07 or (b[2] < b[1] and entity >= .05):
                return False, "%s 断板日大长阴" % nm
            i = DIDXV.get(bd)
            if i is None or i + 1 >= len(dates) or dates[i + 1] > T:
                continue
            d2 = dates[i + 1]
            b2, pv2 = _bar(c, d2), _pv(c, d2)
            if b2 and pv2 and b2[2] / pv2 - 1 <= -(_lim(c, nm) - .006):
                return False, "%s 断板次日跌停" % nm
        return True, "断板温和"

    def _barr(d, th):
        return (LFV.get(d) or {}).get(th) or []

    def _money_ok(B, th):
        if not any(r.get("theme") == th for r in (MV.get(B) or [])):
            return False, "未登当日赚钱效应榜"
        rec = (BE.get(B) or {}).get(th)
        if not rec or not rec.get("ok"):
            return False, "板块赚钱效应验证未过"
        return True, "梯队 %s" % (rec.get("ladder") or "")

    def _assist(B, th, mx):
        return any(2 <= (x.get("boards") or 1) < mx for x in _barr(B, th))

    def _assist_prev(T, th, exclude=None):
        """昨日（T）板块已有助攻股：该题材昨日梯队中除今日目标股外，另有 2 板及以上连板股。
        返回 None 表示当日无该题材梯队、无法独立判定，跳过而不误报。"""
        arr = _barr(T, th)
        if not arr:
            return None
        return any(str(x.get("code")) != str(exclude) and (x.get("boards") or 1) >= 2
                   for x in arr)

    def _cand_max(B, th):
        """当日该题材候选最高板（逐笔 + 机会池，含炸板/一字）。"""
        vals = [t.get("boards") or 1 for m in RV_METHODS
                for t in RVALL.get(m, []) if t["d"] == B and (t.get("theme") or "") == th]
        vals += [o.get("boards") or 1 for o in (CH.get(B) or [])
                 if (o.get("theme") or "") == th]
        return max(vals) if vals else 0

    n_v29 = 0
    for m in RV_METHODS:
        for t in RVALL.get(m, []):
            B = t["d"]
            i = DIDXV.get(B)
            if i is None or i == 0:
                err("v32 %s %s 买入日无前一交易日" % (m, B)); n_v29 += 1; continue
            T = dates[i - 1]
            th = t.get("theme") or ""
            code, name = t["code"], t.get("name") or ""
            # ① 买入日必须盘中冲击涨停（封板以收盘涨停池为准，或日K触板；除权日豁免）
            if not (t.get("sealed") or _touch(code, B, name)):
                err("v32 %s %s %s 买入日既未封板（涨停池）也无日K触板"
                    % (m, B, name)); n_v29 += 1
            # ② 高开但不是涨停开盘（战法二仍要求；战法一 v32 已删除该条件）
            if m == "trialLead" and not _highopen(code, B, name, t.get("buy")):
                err("v32 %s %s %s 非「高开但不是涨停开盘」" % (m, B, name)); n_v29 += 1
            # ③ 一字买不进不得进逐笔
            if _yizi(code, B, name):
                err("v32 %s %s %s 一字涨停却进入逐笔" % (m, B, name)); n_v29 += 1
            # ④ 战法二前置：板块赚钱效应 + 梯队有助攻（战法一 v32 已删除）
            arrB = _barr(B, th)
            mxB = max((x.get("boards") or 1) for x in arrB) if arrB else 0
            if m == "trialLead":
                okm, mdesc = _money_ok(B, th)
                if not okm:
                    err("v32 %s %s %s 板块「%s」%s" % (m, B, name, th, mdesc)); n_v29 += 1
                if mxB < 3 or not _assist(B, th, mxB):
                    err("v32 %s %s %s 板块「%s」梯队/助攻不足（最高 %d 板）"
                        % (m, B, name, th, mxB)); n_v29 += 1
            # ⑤ 分战法条件
            cmx = _cand_max(B, th)
            if m == "spaceDragon":
                st = stage_full.get(T, {})
                N = st.get("suppress") or 0
                if N < 2:
                    err("v32 战法一 %s %s 复盘日无 ≥2 板空间压制" % (B, name)); n_v29 += 1
                if (t.get("boards") or 0) != N + 1:
                    err("v32 战法一 %s %s 非 N+1 板（板位 %s / 压制 %s）"
                        % (B, name, t.get("boards"), N)); n_v29 += 1
                ap = _assist_prev(T, th, code)
                if ap is False:
                    err("v32 战法一 %s %s 昨日板块「%s」无 2 板及以上助攻股"
                        % (B, name, th)); n_v29 += 1
                if "突破买点" not in str(t.get("why") or ""):
                    err("v32 战法一 %s %s why 缺「突破买点」留痕" % (B, name)); n_v29 += 1
                if "昨日助攻" not in str(t.get("why") or ""):
                    err("v32 战法一 %s %s why 缺「昨日助攻」留痕" % (B, name)); n_v29 += 1
                # 战法一已删除的条件不得再出现在 why 里
                for bad_kw in ("断板温和", "赚钱效应", "高开"):
                    if bad_kw in str(t.get("why") or ""):
                        err("v32 战法一 %s %s why 残留已删除条件「%s」"
                            % (B, name, bad_kw)); n_v29 += 1
            elif m == "trialLead":
                if (stage_full.get(T, {}).get("stage") or "") != "节点":
                    err("v32 战法二 %s %s 复盘日阶段非节点（%s）"
                        % (B, name, stage_full.get(T, {}).get("stage"))); n_v29 += 1
                if (t.get("boards") or 0) < 3:
                    err("v32 战法二 %s %s 买入板位不足 3 板" % (B, name)); n_v29 += 1
                if cmx and (t.get("boards") or 0) < cmx:
                    err("v32 战法二 %s %s 非当日板块最高板龙头（%s vs %s）"
                        % (B, name, t.get("boards"), cmx)); n_v29 += 1
                if "节点" not in str(t.get("why") or ""):
                    err("v32 战法二 %s %s why 缺「节点」留痕" % (B, name)); n_v29 += 1
            else:
                err("v32 已删除战法「%s」出现在逐笔（%s %s）" % (m, B, name)); n_v29 += 1

    # ⑥ v23stat 自洽
    if V23.get("cand") != (V23.get("fire") or 0) + (V23.get("yizi") or 0) + (V23.get("noKline") or 0):
        err("v32 v23stat 自洽失败：候选 %s ≠ 参与 %s + 一字 %s + 无日K %s"
            % (V23.get("cand"), V23.get("fire"), V23.get("yizi"), V23.get("noKline"))); n_v29 += 1
    for m in RV_METHODS:
        if (V23.get("byM") or {}).get(m) != len(RVALL.get(m, [])):
            err("v32 v23stat %s 笔数与逐笔不符（%s vs %s）"
                % (m, (V23.get("byM") or {}).get(m), len(RVALL.get(m, [])))); n_v29 += 1
    # ⑦ META / 文案：仅两战法、无已删除战法残留
    if set(meta.keys()) != set(RV_METHODS):
        err("v32 reviewMeta 战法集合不符：%s" % sorted(meta.keys())); n_v29 += 1
    uni29 = (meta.get("spaceDragon") or {}).get("uni") or ""
    for kw in ("v32", "面对空间压制 N 板", "昨日板块已有助攻股", "其余条件全部删除",
               "只在突破当日参与", "战法二（试错龙头）", "N+1 板",
               "盘中 −7% 无条件止损", "补涨战法已删除"):
        if kw not in uni29:
            err("v32 统一口径 uni 缺关键字「%s」" % kw); n_v29 += 1
    buy_all = " ".join((meta.get(m) or {}).get("buy") or "" for m in RV_METHODS)
    for bad_kw in ("凹凸曼", "黑盒", "示范龙头", "v27 门槛", "首次符合条件", "连续 ≥3 日登"):
        if bad_kw in buy_all:
            err("v32 战法买入文案残留已删除口径「%s」" % bad_kw); n_v29 += 1
    for m, kw in (("spaceDragon", "盘中冲击涨停的那一刻"),
                  ("spaceDragon", "只在突破当日参与"),
                  ("trialLead", "博弈成为新的空间龙头")):
        if kw not in ((meta.get(m) or {}).get("buy") or ""):
            err("v32 战法 %s 买入文案缺「%s」" % (m, kw)); n_v29 += 1
    if "fillUp" in (meta or {}):
        err("v32 reviewMeta 仍残留已删除的补涨战法"); n_v29 += 1
    # ⑧ boardEarn 与 money_effect.compute 独立复算一致
    import money_effect as ME
    EFFV = ME.compute(dates, LFV, MV, KBV)
    n_be_bad = 0
    for (bd, nm), v in EFFV.items():
        got = (BE.get(bd) or {}).get(nm)
        if not got or bool(got.get("ok")) != bool(v["ok"]):
            err("v32 boardEarn %s「%s」与独立复算不一致" % (bd, nm)); n_be_bad += 1
    n_v29 += n_be_bad
    print("v32 两战法口径：%d 笔逐笔全部复核（战法一 N+1 板/昨日助攻；战法二赚钱效应/梯队/高开；一字）；"
          "boardEarn %d 题材日独立复算一致（异常 %d）；候选 %s / 参与 %s / 一字 %s / 无日K %s；口径异常 %d"
          % (sum(len(RVALL.get(m, [])) for m in RV_METHODS), len(EFFV), n_be_bad,
             V23.get("cand"), V23.get("fire"), V23.get("yizi"), V23.get("noKline"), n_v29))
    # ---- 13 历史交易日历完整性（2021-2025 涨停池全靠 Excel OCR 补）----
    # 某日 OCR 有行但解析出的涨停池为空时，该日不会进入 dates；
    # compute_streaks 按 sorted(dates) 递推，会跨缺口把不相邻两日误判为连续涨停。
    try:
        ec = json.load(open(os.path.join(OUT, ".excel_cache.json"),
                            encoding="utf-8"))
        ov = set((ec.get("overview") or {}).keys())
        # 缓存按年份分批 OCR，可能领先于当前构建（已 OCR 但尚未并入的年份）。
        # 只校验「主数据起始日之后」的 OCR 交易日有没有缺席，避免误报为断板缺口。
        hist = {d for d in ov if d < CUT and dates[0] <= d}
        gaps = sorted(hist - set(dates))
        if gaps:
            err("历史交易日未进入主数据 %d 天（连板递推会跨缺口）：%s"
                % (len(gaps), ",".join(gaps[:12])))
        thin = [d for d in sorted(hist)
                if len((ec.get("pools") or {}).get(d) or []) < 10]
        print("历史交易日历：OCR 覆盖 %d 天，缺席 %d 天；池<10只 %d 天%s"
              % (len(hist), len(gaps), len(thin),
                 ("：" + ",".join(thin[:8])) if thin else ""))
    except Exception as e:
        print("历史交易日历校验跳过：%s" % e)

    # ---- 汇总 ----
    n_break = sum(1 for d in days if d.get("stage") == "突破")
    n_main = sum(1 for d in days if d.get("stage") == "主升")
    print("交易日 %d；突破 %d 天 / 主升 %d 天；空间高度峰值 %d 板"
          % (len(days), n_break, n_main,
             max((d.get("spaceTop") or 0) for d in days)))
    if bad:
        print("发现 %d 处不一致：" % len(bad))
        for m in bad[:60]:
            print("  -", m)
        return 1
    print("口径一致：空间龙头 / 空间高度 / 空间压制 / 突破 全部自洽")
    return 0


if __name__ == "__main__":
    sys.exit(main())
