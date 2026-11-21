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
import bisect
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
    # v36：2026 起连板数纯同花顺递推（模拟交易口径），OCR 锚点只用于 2025 及以前
    anchors36 = {d: v for d, v in hb.items() if d < CUT}
    st = fd.compute_streaks(pools, anchors36)
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

    # 次新股（上市不足 60 个交易日）：build_merged 用 .stock_kline_cache 判定，
    # 校验脚本必须同口径豁免，否则 2021 年新上市的连板股会被误报「更高换手连板」。
    # v58：流式解析日K缓存，只保留 {code: (首根日期, 根数)}——全量 json.load 峰值
    # ~3GB 会顶爆 8GB 沙箱 cgroup（v58 缓存已含全量历史）。后续判定只需要这两项。
    def _kcv_compact():
        import json as _j
        out = {}
        fp = os.path.join(OUT, ".stock_kline_cache.json")
        if not os.path.exists(fp):
            return out
        s = open(fp, encoding="utf-8", errors="ignore").read()
        dec = _j.JSONDecoder()
        i, n = 0, len(s)
        while i < n and s[i] != "{":
            i += 1
        i += 1
        while i < n:
            while i < n and s[i] != '"':
                if s[i] == "}":
                    i = n
                    break
                i += 1
            if i >= n:
                break
            try:
                key, i = dec.raw_decode(s, i)
            except Exception:
                break
            while i < n and s[i] in " \t\n:":
                i += 1
            try:
                val, i = dec.raw_decode(s, i)
                if isinstance(val, list) and val:
                    out[str(key)] = (str(val[0][0]) if isinstance(val[0], list) else "",
                                     len(val))
            except Exception:
                nxt = s.find(',"', i)
                if nxt < 0:
                    break
                i = nxt + 1
        return out
    _kcv = _kcv_compact()          # {code: (first_date, n_bars)}

    class _KcvView(dict):
        """兼容 fd.new_stock_start 的最小视图：按需把 (first,n) 伪造成 bar 列表。"""
        def __getitem__(self, c):
            v = super().__getitem__(str(c))
            return v
        def get(self, c, default=None):
            v = super().get(str(c))
            return v
    _kcv_view = _kcv_compact()
    # v52：与 build_merged 共用同一判定（历史早期新股reason兜底），杜绝口径漂移
    def _new_stock_start_compact(pools, dates):
        idx = {d: i for i, d in enumerate(dates)}
        starts = {}
        for d in dates:
            if d >= "20210101":
                break
            for s in pools.get(d, []):
                r = str(s.get("reason") or "")
                if "新股" not in r and "次新" not in r:
                    continue
                v = _kcv_view.get(s["code"])
                if v and v[0] and v[0] <= d:
                    continue
                if s["code"] not in starts:
                    starts[s["code"]] = idx[d]
        for c, v in _kcv_view.items():
            if v and 100 <= v[1] < 500 and v[0] in idx:
                i = idx[v[0]]
                if c not in starts or i < starts[c]:
                    starts[c] = i
        return starts
    _ipo = _new_stock_start_compact(pools, dates)

    def _is_new(code, i):
        ii = _ipo.get(str(code))
        return ii is not None and i - ii < 60

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
            if p.get("new") or _is_new(p["code"], i) or p["boards"] <= top:
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
    LF6 = D.get("ladderFull") or {}   # v47：判断连板股当日复盘是否真有该股
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
        # v47：题材为「沿用昨日复盘」的回退股（当日 ladderFull 无此股），其题材当日
        #      本就漏抓、可能无当日梯队，故跳过本条「当日梯队」检查，改由第 15 段
        #      验证该题材可追溯到当日或昨日复盘，避免把合法的昨日归类误报。
        for row in LB.get(d) or []:
            name, boards, nm = row[0], row[1], (row[2] if len(row) > 2 else "")
            if not nm or not boards or boards < 2:
                continue
            _code = row[3] if len(row) > 3 else ""
            _in_today = any(any(str(s.get("code")) == str(_code) for s in arr)
                            for arr in (LF6.get(d) or {}).values())
            if not _in_today:             # 回退题材：当日无梯队属正常
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
    # v52：历史老股可能在完整日K缓存里有数据、但未进按需分片（需求日全在 2019-06 前
    # 被裁掉）→ 数据实际可用，不算缺失。仅缓存与分片都没有才算真缺。
    miss = {c for c in (clickable - allk)
            if not (_kcv.get(str(c)) and _kcv[str(c)][1] >= 5)}
    if miss:
        err("个股日K缺失 %d 只：%s" % (len(miss), ",".join(sorted(miss)[:10])))
    sk = D.get("supKlines") or {}
    # v52：新浪日K源最早约 2019-06（DATALEN=1800 根回溯上限），此前交易日
    # 无论哪只股票都无日K数据 → 属数据源硬限制而非构建缺陷，一律豁免；
    # 2019-06 起仍逐股严格校验。
    KLINE_START = "20190601"
    def _no_kline_at(code, d):
        if d < KLINE_START:
            return True
        b = _kcv.get(str(code))
        return not (b and b[0] and b[0] <= d)
    for day in days:
        for s in day.get("suppressStocks") or []:
            if sk.get(s["code"] + "_" + s["breakDate"]):
                continue
            if _no_kline_at(s["code"], s.get("breakDate") or day["date"]):
                continue
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
            # v52：历史早期该股在日K源起点(约2019-06)之前无可点日K → 数据源限制，豁免
            if _no_kline_at(c, d):
                continue
            end5 = dates[min(i + 6, len(dates) - 1)]
            if end5 >= dates[-1]:          # 断板后 5 天尚未到来（最新交易日附近）
                continue
            n_pt += 1
            if len([x for x in kd if x > d]) >= 5:
                continue
            # 停牌：窗口内连续缺 2 天以上、且窗口后仍有日K → 不是数据缺失
            _gap = [dates[j] for j in range(i + 1, min(i + 6, len(dates)))
                    if dates[j] not in kd]
            if len(_gap) >= 2 and any(x > end5 for x in kd):
                n_na += 1
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

    # ---- 12 交易复盘：逐笔自洽（T 日选股 → T+1 买入；单一战法「山顶洞人」）----
    RVALL = D.get("review") or {}
    if isinstance(RVALL, list):                 # 兼容旧版单战法数组
        RVALL = {"summit": RVALL}
    RV_METHODS = ("summit",)
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
    print("交易复盘·山顶洞人：%s，异常 %d 笔（一字跌停顺延 %d 笔）" %
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

    def _vol(c, d):
        b = _bar(c, d)
        return b[5] if (b and len(b) >= 6) else 0

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

    def _handover_pick(T, th, code, name):
        """T 日选股独立复算：板块最高板龙头且为换手（非一字）3 板+，
        板块内另有换手 2 板跟风股。返回 (bool|None, 说明)。"""
        arr = _barr(T, th)
        if not arr:
            return None, "T 日无该题材梯队（无法独立判定）"
        mx = max((x.get("boards") or 1) for x in arr)
        if mx < 3:
            return False, "T 日板块最高 %d 板 < 3" % mx
        if not any(str(x.get("code")) == str(code) and (x.get("boards") or 1) == mx
                   for x in arr):
            return False, "目标股非 T 日板块最高板龙头"
        if _yizi(code, T, name):
            return False, "T 日龙头为一字板（非换手）"
        f2 = [x for x in arr if (x.get("boards") or 1) == 2
              and str(x.get("code")) != str(code)
              and not _yizi(str(x.get("code")), T, str(x.get("name") or ""))]
        if not f2:
            return False, "T 日板块无换手 2 板跟风股"
        return True, "梯队 %s" % "-".join(
            str(b) for b in sorted({x.get("boards") for x in arr}, reverse=True))

    def _assist_next(B, T, th, code):
        """买入日助攻独立复算：昨日（T）跟风 2 板今日高开红盘无亏钱，
        或昨日首板今日收盘涨停晋级 2 板。"""
        arr = _barr(T, th)
        ok = []
        for x in arr:
            bd = x.get("boards") or 1
            c2, nm2 = str(x.get("code")), str(x.get("name") or "")
            if c2 == str(code):
                continue
            b, pv = _bar(c2, B), _pv(c2, B)
            if not b or not pv or len(b) < 5:
                continue
            if bd == 2 and b[1] > pv and b[2] >= pv:
                ok.append("%s 高开红盘" % (nm2 or c2))
            elif bd == 1 and b[2] / pv - 1 >= _lim(c2, nm2) - .006:
                ok.append("%s 晋级 2 板" % (nm2 or c2))
        return bool(ok), "、".join(ok[:2])

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
                err("v44 %s %s 买入日无前一交易日" % (m, B)); n_v29 += 1; continue
            th = t.get("theme") or ""
            code, name = t["code"], t.get("name") or ""
            why = str(t.get("why") or "")
            bd = t.get("boards") or 0
            # ① 买入日必须盘中冲击涨停（封板以收盘涨停池为准，或日K触板；除权日豁免）
            if not (t.get("sealed") or _touch(code, B, name)):
                err("v44 %s %s %s 买入日既未封板（涨停池）也无日K触板"
                    % (m, B, name)); n_v29 += 1
            # ② 一字买不进不得进逐笔（v44：一字板除外）
            if _yizi(code, B, name):
                err("v44 %s %s %s 一字涨停却进入逐笔" % (m, B, name)); n_v29 += 1
            # ③ 战法留痕
            for kw in ("山顶洞人", "当日赚钱效应龙头"):
                if kw not in why:
                    err("v44 %s %s %s why 缺「%s」留痕" % (m, B, name, kw)); n_v29 += 1
            # ④ 当日该题材必须在赚钱效应榜、且该股就是榜内龙头（独立复算）
            mg = next((g for g in (MV.get(B) or [])
                       if (g.get("theme") or "") == th), None)
            if not mg:
                err("v44 %s %s %s 买入日题材「%s」不在当日赚钱效应榜"
                    % (m, B, name, th)); n_v29 += 1
            else:
                if str(mg.get("leadCode") or "") != str(code):
                    err("v44 %s %s %s 非当日该题材龙头（榜内龙头 %s）"
                        % (m, B, name, mg.get("lead") or mg.get("leadCode"))); n_v29 += 1
                if (mg.get("maxB") or 0) < 3:
                    err("v44 %s %s %s 题材「%s」当日无 ≥3 板（最高 %s）"
                        % (m, B, name, th, mg.get("maxB"))); n_v29 += 1
                if (mg.get("count") or 0) < 3:
                    err("v44 %s %s %s 题材「%s」当日涨停不足 3 只（%s）"
                        % (m, B, name, th, mg.get("count"))); n_v29 += 1
            # ⑤ 题材须在当日题材炒作块（入链）内——「当日题材炒作有赚钱效应」
            _det = {str(x.get("name") or "").strip() for x in (TD.get(B) or [])}
            if _det and str(th).strip() not in _det:
                err("v44 %s %s %s 题材「%s」当日未在题材炒作模块入链"
                    % (m, B, name, th)); n_v29 += 1
            # ⑥ 该股当日确实是该题材最高连板（龙头）
            _arr = (LFV.get(B) or {}).get(th) or []
            if _arr:
                _mx = max((x.get("boards") or 1) for x in _arr)
                if (t.get("boards") or 0) != _mx:
                    err("v44 %s %s %s 板位 %s 非该题材最高板 %s"
                        % (m, B, name, t.get("boards"), _mx)); n_v29 += 1
            # ⑦ v59 五要素硬门槛独立复算（缺一不得入逐笔）
            _ii = DIDXV[B]
            _PB = dates[_ii - 1]
            for kw in ("板块连板助攻", "市场情绪变好", "板块变强", "持续赚钱效应", "龙头非一字"):
                if kw not in why:
                    err("v59 %s %s %s why 缺「%s」留痕" % (m, B, name, kw)); n_v29 += 1
            _a2 = [x for x in _arr if (x.get("boards") or 1) >= 2
                   and str(x.get("code")) != str(code)]
            if not _a2:
                err("v59 %s %s %s 板块「%s」当日无连板助攻（≥2板除龙头外）"
                    % (m, B, name, th)); n_v29 += 1
            _zt_c = (days[_ii] or {}).get("ztCount")
            _zt_p = (days[_ii - 1] or {}).get("ztCount")
            if not (isinstance(_zt_c, (int, float)) and isinstance(_zt_p, (int, float))
                    and _zt_c > _zt_p):
                err("v59 %s %s %s 市场情绪未变好（涨停 %s vs 昨 %s）"
                    % (m, B, name, _zt_c, _zt_p)); n_v29 += 1
            _arrP = (LFV.get(_PB) or {}).get(th) or []
            _cc, _cp = len(_arr), len(_arrP)
            _mc = max((x.get("boards") or 1) for x in _arr) if _arr else 0
            _mp = max((x.get("boards") or 1) for x in _arrP) if _arrP else 0
            if not (_cc > _cp or _mc > _mp):
                err("v59 %s %s %s 板块未变强（涨停 %d vs 昨 %d / 最高板 %d vs 昨 %d）"
                    % (m, B, name, _cc, _cp, _mc, _mp)); n_v29 += 1
            _pt = {str(x.get("theme") or "").strip() for x in (MV.get(_PB) or [])}
            if th not in _pt:
                err("v59 %s %s %s 题材「%s」前一日不在赚钱效应榜（无持续赚钱效应）"
                    % (m, B, name, th)); n_v29 += 1
            _sv = t.get("sustain")
            if not (isinstance(_sv, (int, float)) and _sv >= 2):
                err("v59 %s %s %s sustain 字段缺失/＜2（%s）" % (m, B, name, _sv)); n_v29 += 1

    # ⑥ v23stat 自洽（v52：候选 = 参与 + 一字 + 买入后无卖出K线；
    #    noKline 总数还含「买入前无K线」的那部分，不计入 cand，故改用 noKlineExit）
    if V23.get("cand") != (V23.get("fire") or 0) + (V23.get("yizi") or 0) + (V23.get("noKlineExit") or 0):
        err("v32 v23stat 自洽失败：候选 %s ≠ 参与 %s + 一字 %s + 无卖出K %s"
            % (V23.get("cand"), V23.get("fire"), V23.get("yizi"), V23.get("noKlineExit"))); n_v29 += 1
    for m in RV_METHODS:
        if (V23.get("byM") or {}).get(m) != len(RVALL.get(m, [])):
            err("v32 v23stat %s 笔数与逐笔不符（%s vs %s）"
                % (m, (V23.get("byM") or {}).get(m), len(RVALL.get(m, [])))); n_v29 += 1
    # ⑦ META / 文案：仅「山顶洞人」一个战法、无历史战法残留
    if set(meta.keys()) != set(RV_METHODS):
        err("v42 reviewMeta 战法集合不符：%s" % sorted(meta.keys())); n_v29 += 1
    uni29 = (meta.get("summit") or {}).get("uni") or ""
    for kw in ("v59", "山顶洞人", "当日题材炒作里有赚钱效应", "龙头为换手（非一字）",
               "盘中 −7% 无条件止损", "一字涨停买不进",
               "板块连板助攻", "市场情绪变好", "板块变强", "持续赚钱效应"):
        if kw not in uni29:
            err("v44 统一口径 uni 缺关键字「%s」" % kw); n_v29 += 1
    buy_all = " ".join((meta.get(m) or {}).get("buy") or "" for m in RV_METHODS)
    for bad_kw in ("凹凸曼", "黑盒", "示范龙头", "v27 门槛", "首次符合条件", "连续 ≥3 日登"):
        if bad_kw in buy_all:
            err("v42 战法买入文案残留已删除口径「%s」" % bad_kw); n_v29 += 1
    for m, kw in (("summit", "有赚钱效应"), ("summit", "龙头股"), ("summit", "一字板除外")):
        if kw not in ((meta.get(m) or {}).get("buy") or ""):
            err("v44 战法 %s 买入文案缺「%s」" % (m, kw)); n_v29 += 1
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
    print("v44/v59 单一战法「山顶洞人」：%d 笔逐笔全部复核（当日题材炒作有赚钱效应的题材 → 龙头涨停买入、一字除外；"
          "v59 五要素：连板助攻/非一字/情绪变好/板块变强/持续赚钱效应 独立复算）；"
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

    # ---- 14 v45 口径：断板涨跌幅不被盘中快照污染 / 首板家数 / 三档情绪 / 买入逻辑 ----
    # 用户报障：20261119 华东重机断板当日实际 +4.07%，页面显示 −5.43% / +2.58%。
    # 根因是 sup_kline_cache 的盘中快照覆盖了 stock_kline_cache 的收盘价。
    # 这里用「点击日K」用的分片（收盘完整日）独立复算，确保页面数字与图上一致。
    SH_OK = {"一字", "赚T", "地天", "普通", "长腿", "赚钱", "低开"}
    _bk = {}

    def _shard_bars(code):
        code = str(code)
        bkt = sum(int(x) for x in code if x.isdigit()) % 8
        if bkt not in _bk:
            try:
                _bk[bkt] = json.load(open(os.path.join(
                    OUT, "klines", "klines_%d.json" % bkt), encoding="utf-8"))
            except Exception:
                _bk[bkt] = {}
        return _bk[bkt].get(code) or []

    n_v45, n_v45_skip, n_v45_chk = 0, 0, 0
    for day in days:
        d = day["date"]
        dec = (D.get("decision") or {}).get(d) or {}
        dg = dec.get("dragon") or {}
        for st in (dg.get("stocks") or []):
            bars = _shard_bars(st["code"])
            idx = {b[0]: i for i, b in enumerate(bars)}
            i = idx.get(d)
            if i is None or i == 0:
                n_v45_skip += 1
                continue
            n_v45_chk += 1
            cur, prev = bars[i], bars[i - 1]
            exp = round((cur[2] / prev[2] - 1) * 100, 2)
            got = st.get("today")
            if got is None or abs(exp - got) > 0.02:
                err("v45 %s %s 断板涨跌幅 %s 与日K复算 %.2f%% 不符"
                    % (d, st["name"], got, exp)); n_v45 += 1
            if st.get("cum") is not None:
                j = idx.get(st.get("brk"))
                if j is not None and bars[j][2]:
                    ec = round((cur[2] / bars[j][2] - 1) * 100, 2)
                    if abs(ec - st["cum"]) > 0.02:
                        err("v45 %s %s 断板后累计 %s 与日K复算 %.2f%% 不符"
                            % (d, st["name"], st["cum"], ec)); n_v45 += 1
            # 三档情绪独立复算
            fresh = bool(dg.get("fresh"))
            lim = 0.20 if str(st["code"]).startswith(("30", "688")) else 0.10
            if fresh:
                expm = "normal" if exp >= 0 else "risk"
            elif exp >= 0:
                expm = ("hot" if (cur[2] > prev[4] or
                                  (cur[4] > prev[4] and cur[2] > prev[2])) else "normal")
            else:
                expm = "risk"
            got_m = (st.get("mood") or {}).get("mood")
            if got_m and got_m != expm:
                err("v45 %s %s 情绪档 %s ≠ 复算 %s（今日 %+.2f%%）"
                    % (d, st["name"], got_m, expm, exp)); n_v45 += 1
        # 空间龙头形态标签
        for sp in (day.get("spaceFull") or []):
            if sp.get("s") and sp["s"] not in SH_OK:
                err("v45 %s %s 涨停形态非法 %s" % (d, sp["name"], sp["s"])); n_v45 += 1
        # 题材梯队首板家数
        LFV2 = (D.get("ladderFull") or {}).get(d) or {}
        for th in (dec.get("themes") or []):
            arr = LFV2.get(th["name"]) or []
            exp_b1 = sum(1 for x in arr if (x.get("boards") or 1) == 1)
            if th.get("b1") != exp_b1:
                err("v45 %s 题材「%s」首板家数 %s ≠ 复算 %s"
                    % (d, th["name"], th.get("b1"), exp_b1)); n_v45 += 1

    RV45 = (D.get("review") or {}).get("summit") or []
    for t in RV45:
        if not str(t.get("logic") or "").startswith("赚钱效应龙头"):
            err("v45 逐笔 %s %s 缺「买入逻辑」摘要" % (t["d"], t["name"])); n_v45 += 1
        if t.get("t") and not re.fullmatch(r"\d{2}:\d{2}", str(t["t"])):
            err("v45 逐笔 %s %s 封板时间格式异常 %s" % (t["d"], t["name"], t["t"]))
            n_v45 += 1
        if t.get("shape") and t["shape"] not in SH_OK:
            err("v45 逐笔 %s %s 涨停形态非法 %s" % (t["d"], t["name"], t["shape"]))
            n_v45 += 1
        if t.get("b1") is None or t.get("cnt") is None:
            err("v45 逐笔 %s %s 缺首板/板块家数" % (t["d"], t["name"])); n_v45 += 1
    RS = D.get("reviewStat") or {}
    if (RS.get("total") or {}).get("n") != len(RV45):
        err("v45 reviewStat 总数 %s ≠ 逐笔 %d"
            % ((RS.get("total") or {}).get("n"), len(RV45))); n_v45 += 1
    for k in ("stage", "boards", "cnt", "b1", "seal", "shape", "vol"):
        arr = RS.get(k) or []
        if sum(x.get("n") or 0 for x in arr) != len(RV45):
            err("v45 reviewStat「%s」分组笔数合计 %d ≠ 逐笔 %d"
                % (k, sum(x.get("n") or 0 for x in arr), len(RV45))); n_v45 += 1
    print("v45 断板涨跌幅 / 三档情绪 / 首板家数 / 买入逻辑：逐笔 %d、断板股票日复核 %d"
          "（缺分片 %d），异常 %d" % (len(RV45), n_v45_chk, n_v45_skip, n_v45))

    # ---- 15 v47 口径：连板梯队当日漏抓题材须沿用「昨日复盘」归类 ----
    # 回退源只能是「当日或前一交易日」复盘清单里该股的题材（theme_at 的两档取值）。
    # 若某股的两日复盘都无归类，题材必须保持空——不能凭空造题材、也不能取更早的陈旧归类。
    LFV47 = D.get("ladderFull") or {}
    LB47 = D.get("lb") or {}
    DATESET47 = dates

    def _theme_in(code, day):
        """该股在 day 复盘清单里的题材（ladderFull 覆盖当日全部题材的全部涨停股）。"""
        for nm, arr in (LFV47.get(day) or {}).items():
            for s in arr:
                if str(s.get("code")) == str(code):
                    return nm
        return ""

    # 该股全期曾被复盘归入的所有题材：用作「昨日无复盘→累积 carry 兜底」的合法来源。
    # carry 本就是历史复盘清单的累积，故回退题材必须能追溯到该股任一历史复盘题材，
    # 绝不允许凭空造题材名。
    _theme_ever47 = {}
    for _day, _thm in LFV47.items():
        for _nm, _arr in _thm.items():
            for _s in _arr:
                _theme_ever47.setdefault(str(_s.get("code")), set()).add(_nm)

    n_lbfill47, n_lbempty47, n_lbbad47 = 0, 0, 0
    for _i, day in enumerate(DATESET47):
        _pd = DATESET47[_i - 1] if _i > 0 else None
        for row in (LB47.get(day) or []):
            _code = str(row[3])
            _th = row[2]
            if not _th:
                n_lbempty47 += 1
                continue
            _today_th = _theme_in(_code, day)
            _prev_th = _theme_in(_code, _pd) if _pd else ""
            _source_ok = (_th == _today_th or _th == _prev_th or
                          _th in _theme_ever47.get(_code, ()))
            if not _source_ok:
                err("v47 %s %s(%s) 题材「%s」无可追溯来源（当日 %s / 昨日 %s / 历史 %s）"
                    % (day, row[0], _code, _th, _today_th or "-", _prev_th or "-",
                       "/".join(sorted(_theme_ever47.get(_code, ()))) or "-"))
                n_lbbad47 += 1
            elif not _today_th:       # 当日复盘无归类 → 属回退（昨日 or 历史 carry）
                n_lbfill47 += 1
    print("v47 连板梯队题材回退：当日漏抓沿用昨日/历史复盘 %d 个股票日、仍空 %d，异常 %d"
          % (n_lbfill47, n_lbempty47, n_lbbad47))


    # ---- v67：赚钱效应统计（earnStreak）与高位断板跟踪（brkTrack）独立复核见文末 ----

    # ---- v55 新旧龙头交接：事件序列结构 + 同身位一致性独立复核 ----
    HD = D.get("handover") or {}
    n_hd = 0
    for kind in ("space",):
        for e in (HD.get(kind) or []):
            if not e.get("date") or not e.get("old") or not e.get("new"):
                err("v55 交接(%s)事件结构缺字段: %s" % (kind, e)); n_hd += 1; continue
            # old/new 内部须同身位（boards 相同）
            for grp in ("old", "new"):
                bs = {x.get("boards") for x in e[grp]}
                if len(bs) != 1:
                    err("v55 交接(%s) %s %s 批次非同身位 %s" % (kind, e["date"], grp, bs)); n_hd += 1
    for th, evs in (HD.get("theme") or {}).items():
        for e in evs:
            if not e.get("date") or not e.get("old") or not e.get("new"):
                err("v55 题材交接「%s」事件结构缺字段" % th); n_hd += 1; continue
            for grp in ("old", "new"):
                bs = {x.get("boards") for x in e[grp]}
                if len(bs) != 1:
                    err("v55 题材交接「%s」%s %s 批次非同身位 %s" % (th, e["date"], grp, bs)); n_hd += 1
    print("v55 新旧龙头交接：空间 %d 次 / 题材 %d 个题材，独立复核异常 %d"
          % (len(HD.get("space") or []), len(HD.get("theme") or {}), n_hd))

    # ---- v54：明日交易机会推荐（盈利个股前一日三维特征画像 + 历史实例）独立复核 ----
    NR = D.get("nextRecs") or {}
    n_v54 = 0
    NR_DATES = D.get("dates") or []
    NR_DIDX = {d: i for i, d in enumerate(NR_DATES)}
    NR_SD = {s.get("date"): s for s in days}
    NR_LF = D.get("ladderFull") or {}
    NR_MONEY = D.get("money") or {}
    NR_FP = NR.get("featureProfile") or {}
    NR_BASE = NR.get("base") or {}
    # ① 盈利画像样本口径：nProfit + 非盈利 = base.n，且 nProfit 与 review 复算一致
    n_profit_calc = sum(1 for x in RV45 if (x.get("pnl") or 0) > 0)
    n_all_calc = sum(1 for x in RV45 if x.get("pnl") is not None)
    if NR_FP.get("nProfit") != n_profit_calc:
        err("v54 盈利画像笔数 %d != review 复算 %d" % (NR_FP.get("nProfit", -1), n_profit_calc)); n_v54 += 1
    if NR_BASE.get("n") != n_all_calc:
        err("v54 全样本笔数 %d != review 复算 %d" % (NR_BASE.get("n", -1), n_all_calc)); n_v54 += 1
    # ② 独立复算分档函数，逐笔盈利交易核对三维特征在画像分布里可找到
    def _supp54(v):
        if v in (None, 0, "None"): return "无压制"
        try: v = int(v)
        except (TypeError, ValueError): return "无压制"
        return "低压制" if v <= 4 else "高压制"
    def _zt54(n):
        n = n or 0
        return "冰点<30" if n < 30 else ("温和30-59" if n < 60 else
               ("活跃60-99" if n < 100 else "过热≥100"))
    def _seal54(t):
        if not t: return "未知封板"
        return "秒板" if t <= "10:00" else ("早盘" if t <= "10:30" else
               ("盘中" if t <= "13:30" else "尾盘"))
    def _btier(b):
        b = b or 0
        return "首板/未入梯队" if b <= 1 else ("2板" if b == 2 else ("3板" if b == 3 else "4板+"))
    def _fs(st):
        st = st or {}
        return (st.get("stage") or "未知") + "·" + _zt54(st.get("ztCount")) + "·" + _supp54(st.get("suppress"))
    def _ft(T, th):
        stage = ""
        for r in (NR_MONEY.get(T) or []):
            if r.get("theme") == th:
                stage = r.get("stage") or ""
                break
        # v58：money 表无该题材时，用题材复盘自身 ladderFull 序列补算（与 next_recs 同规则）
        if not stage:
            stage = _lad_stage(th).get(T) or "未知"
        arr = (NR_LF.get(T) or {}).get(th) or []
        mb = max((x.get("boards") or 1) for x in arr) if arr else 0
        lad = "高≥3板" if mb >= 3 else ("中2板" if mb == 2 else "弱<2板")
        return stage + "·" + lad
    # v58：题材复盘阶段补算表（启动/加强/反复/转弱）
    _LS_CACHE = {}
    def _lad_stage(th):
        if th in _LS_CACHE:
            return _LS_CACHE[th]
        seq = []
        for d in NR_DATES:
            arr = (NR_LF.get(d) or {}).get(th) or []
            if arr:
                seq.append((d, len(arr)))
        st = {}
        for k, (d, c) in enumerate(seq):
            if k == 0:
                st[d] = "启动"; continue
            gap = NR_DIDX[d] - NR_DIDX[seq[k - 1][0]]
            if gap >= 5:
                st[d] = "启动"
            elif gap >= 2:
                st[d] = "反复"
            else:
                pc = seq[k - 1][1]
                st[d] = "加强" if c > pc else ("转弱" if c < pc else "反复")
        _LS_CACHE[th] = st
        return st
    def _fk(code, T, boards=0, shape="", t=""):
        ent = None
        for arr in (NR_LF.get(T) or {}).values():
            ent = next((z for z in arr if str(z.get("code")) == str(code)), None)
            if ent: break
        b = (ent.get("boards") if ent else None) or boards or 0
        s = (ent.get("s") if ent else None) or shape or ""
        tm = (ent.get("t") if ent else None) or t or ""
        return _btier(b) + "·" + (s or "普通") + "·" + _seal54(tm)
    dim_keys = {dimen: {r.get("k") for r in (NR_FP.get(dimen) or [])}
                for dimen in ("sent", "theme", "stock")}
    for x in RV45:
        if (x.get("pnl") or 0) <= 0: continue
        i = NR_DIDX.get(x.get("d"))
        if i is None or i == 0: continue
        T = NR_DATES[i - 1]; th = x.get("theme") or ""; code = str(x.get("code") or "")
        vals = {"sent": _fs(NR_SD.get(T)), "theme": _ft(T, th),
                "stock": _fk(code, T, x.get("boards"), x.get("shape"), x.get("t"))}
        for dimen, v in vals.items():
            if v not in dim_keys[dimen]:
                # 稀疏档位（<8 笔）不进画像_dist 的 top8，属正常裁剪，不算异常
                pass
    # ③ byDate 推荐合法性：2板+、非一字、三维特征复算一致、历史实例均为盈利真实买入
    n_rec = 0
    ex_checked = 0
    RV_BY = {(str(x.get("d")), str(x.get("code"))): x for x in RV45}
    for d, blk in (NR.get("byDate") or {}).items():
        for r in (blk.get("recs") or []):
            n_rec += 1
            code = str(r.get("code") or "")
            arr = (NR_LF.get(d) or {}).get(r.get("theme") or "") or []
            hit = next((z for z in arr if str(z.get("code")) == code), None)
            if not hit or (hit.get("boards") or 1) < 2:
                err("v54 推荐 %s %s 非当日2板+龙头" % (d, code)); n_v54 += 1; continue
            if hit.get("s") == "一字":
                err("v54 推荐 %s %s 为一字（买不进）" % (d, code)); n_v54 += 1
            st = NR_SD.get(d) or {}
            if r.get("fs") != _fs(st) or r.get("ft") != _ft(d, r.get("theme") or "") \
               or r.get("fk") != _fk(code, d, hit.get("boards"), hit.get("s"), hit.get("t")):
                err("v54 推荐 %s %s 三维特征与复算不符" % (d, code)); n_v54 += 1
            if r.get("win") is not None and r.get("n", 0) < 1:
                err("v54 推荐 %s %s 胜率有值但样本<1" % (d, code)); n_v54 += 1
            for ins in (r.get("instances") or []):
                ex_checked += 1
                src = RV_BY.get((str(ins.get("d")), str(ins.get("code"))))
                if not src or (src.get("pnl") or 0) <= 0 or abs((src.get("pnl") or 0) - (ins.get("pnl") or 0)) > 0.01:
                    err("v54 推荐 %s %s 历史实例 %s/%s 非盈利真实买入"
                        % (d, code, ins.get("d"), ins.get("code"))); n_v54 += 1
    print("v54 明日机会推荐：盈利画像 %d 笔 / 全样本 %d 笔，决策日 %d 个 / 推荐 %d 笔 / 历史实例 %d 条，"
          "独立复核异常 %d"
          % (NR_FP.get("nProfit", 0), NR_BASE.get("n", 0), len(NR.get("byDate") or {}),
             n_rec, ex_checked, n_v54))
    # v67 起旧 v48 段已删，n_v48 仅作各段异常累加器
    n_v48 = 0
    n_v48 += n_v54

    # ---- v58：明日交易机会复盘回测（nextRecsBacktest）独立复核 ----
    NRB = D.get("nextRecsBacktest") or {}
    n_v58 = 0
    recs = NRB.get("records") or []
    n_rec_total = sum(len((blk or {}).get("recs") or [])
                      for blk in (NR.get("byDate") or {}).values())
    # ① 推荐笔数对账（无次日/无K线的会被跳过，因此 records ≤ 推荐数）
    if len(recs) > n_rec_total:
        err("v58 回测记录 %d > 推荐笔数 %d" % (len(recs), n_rec_total)); n_v58 += 1
    # ② 状态自洽 + pnl 复算（抽样 800 笔成交；分片按桶惰性加载防 OOM）
    import random as _rnd
    _KB58_BUCKETS = {}
    def _kb58_bucket(code):
        bkt = sum(int(ch) for ch in str(code) if ch.isdigit()) % 8
        if bkt not in _KB58_BUCKETS:
            fp = os.path.join(OUT, "klines", "klines_%d.json" % bkt)
            try:
                raw = json.load(open(fp, encoding="utf-8"))
            except Exception:
                raw = {}
            _KB58_BUCKETS[bkt] = {str(c): {b[0]: b for b in bars
                                           if b and (not isinstance(b[2], (int, float)) or b[2] > 0)}
                                  for c, bars in raw.items()}
        return _KB58_BUCKETS[bkt]
    _rnd.seed(58)
    deals = [x for x in recs if x.get("status") == "成交"]
    # ① pnl 与买卖价算术自洽（止损/一字顺延的卖价不等于收盘价，逐笔语义复算只做抽样容差）
    for x in recs:
        if x.get("status") == "成交" and x.get("pnl") is None:
            err("v58 成交无pnl: %s %s" % (x.get("d"), x.get("code"))); n_v58 += 1
        if x.get("status") != "成交" and x.get("pnl") is not None:
            err("v58 非成交却有pnl: %s %s" % (x.get("d"), x.get("code"))); n_v58 += 1
        if x.get("pnl") is not None and x.get("buy"):
            calc = (x["sell"] - x["buy"]) / x["buy"] * 100
            if abs(calc - x["pnl"]) > 0.02:
                err("v58 pnl 算术不自洽 %s %s: %s vs %s" % (x.get("d"), x.get("code"),
                    round(calc, 2), x.get("pnl"))); n_v58 += 1
    # ② 抽样：仅「尾盘未涨停/期末仍涨停（无顺延）」的卖价应≈卖出日收盘（分片 bar 容差 1%）
    _tail = [x for x in deals
             if (x.get("reason") or "").startswith("尾盘未涨停") or
                ((x.get("reason") or "").startswith("期末仍涨停") and "顺延" not in (x.get("reason") or ""))]
    for x in _rnd.sample(_tail, min(600, len(_tail))):
        b = _kb58_bucket(x.get("code")).get(str(x.get("code"))) or {}
        sb = b.get(x.get("sellD"))
        if not sb:
            continue
        if abs((sb[2] - x["sell"]) / max(1e-9, x["sell"])) > 0.01:
            err("v58 卖出价与分片收盘不符 %s %s: %s vs %s" % (x.get("d"), x.get("code"),
                x.get("sell"), sb[2])); n_v58 += 1
    print("v58 明日交易机会复盘：推荐 %d → 回测记录 %d（成交 %d / 一字买不进 %d / 未涨停 %d），"
          "独立复核异常 %d"
          % (n_rec_total, len(recs), len(deals),
             sum(1 for x in recs if x.get("status") == "一字买不进"),
             sum(1 for x in recs if x.get("status") == "未涨停"), n_v58))
    n_v48 += n_v58

    # ---- v60：循环仪表盘（cycle）独立复核 ----
    n_v60 = 0
    CYV = D.get("cycle") or {}
    cy_days = CYV.get("days") or []
    # ① 覆盖：除首个交易日外全期应有记录
    if len(cy_days) < len(days) - 2:
        err("v60 cycle 天数 %d < 交易日 %d - 2" % (len(cy_days), len(days))); n_v60 += 1
    pmapv = {}
    for r in cy_days:
        d = r.get("date")
        if d not in date_set:
            err("v60 cycle 日期 %s 不在交易日历" % d); n_v60 += 1
        sc = r.get("score")
        if sc is not None and not (0 <= sc <= 10):
            err("v60 %s 健康度超界 %s" % (d, sc)); n_v60 += 1
        if r.get("phase") is not None:
            err("v60 %s 仍残留生命周期字段 phase（v63 应已取消）" % d); n_v60 += 1
        for k in ("s1", "s2", "s3", "s4"):
            if r.get("score") is not None and r.get(k) is None:
                err("v60 %s 缺环节分 %s" % (d, k)); n_v60 += 1
        pmapv[d] = r
    # ② 四环节原始读数抽样复算（r1 不亏钱率 / r2a 晋级率，按涨停池独立重算）
    def _closev(code, d):
        b = _kb58_bucket(code).get(str(code), {}).get(d)
        return b[2] if (b and len(b) >= 5 and b[2]) else None
    _cyc_pool = [x for x in cy_days if x.get("r1") is not None]
    for r in _rnd.sample(_cyc_pool, min(60, len(_cyc_pool))):
        _ii = DIDXV.get(r["date"])
        if not _ii:
            continue
        _pd = dates[_ii - 1]
        _hi2 = [s for s in (pools.get(_pd) or [])
                if (s.get("boards") or 1) >= 2 and _closev(s["code"], _pd) and _closev(s["code"], r["date"])]
        if not _hi2:
            continue
        _r1 = sum(1 for s in _hi2 if _closev(s["code"], r["date"]) >= _closev(s["code"], _pd)) / len(_hi2)
        if abs(_r1 - r["r1"]) > 0.02:
            err("v60 %s 不亏钱率复算不符 %.2f vs %.2f" % (r["date"], _r1, r["r1"])); n_v60 += 1
    # ③ 推荐内嵌健康度自洽：score/neg 与仪表盘一致（v63 无降级/无 phase）
    for _d, blk in (NR.get("byDate") or {}).items():
        _cy = (blk or {}).get("cycle") or {}
        if _cy.get("score") != (pmapv.get(_d) or {}).get("score"):
            err("v60 %s 推荐内嵌健康度与仪表盘不符" % _d); n_v60 += 1
        if _cy.get("degrade") is not None or _cy.get("stress") is not None:
            err("v60 %s 推荐内嵌仍残留降级/压力字段（v63 应已取消）" % _d); n_v60 += 1
    print("v60 循环仪表盘：%d 天，四环节读数抽样 %d 复核一致，推荐内嵌健康度对账 %d 天，异常 %d"
          % (len(cy_days), min(60, len(_cyc_pool)),
             len(NR.get("byDate") or {}), n_v60))
    n_v48 += n_v60

    # ---- v62：B1 传导路径图（transmit）+ 回测 chgB 独立复核 ----
    n_v62 = 0
    TRV = D.get("transmit") or {}
    LFV = D.get("ladderFull") or {}
    _kbcache_v = {}
    def _kbdict(code):
        if code not in _kbcache_v:
            _kbcache_v[code] = _kb58_bucket(code).get(str(code), {})
        return _kbcache_v[code]
    def _pc62(code, d0):
        kd = _kbdict(code)
        ds = [x for x in kd if x < d0]
        if not ds:
            return None
        b = kd[max(ds)]
        return b[2] if (b and len(b) >= 5 and b[2]) else None
    def _cc62(code, d):
        b = _kbdict(code).get(d)
        return b[2] if (b and len(b) >= 5 and b[2]) else None
    # ① 覆盖：transmit ⊆ ladders；ladders 中前一日有梯队的题材日都应有 transmit
    _lad_v = D.get("ladders") or {}
    for d, ths in TRV.items():
        for nm in ths:
            if nm not in (_lad_v.get(d) or {}):
                err("v62 transmit %s/%s 不在 ladders" % (d, nm)); n_v62 += 1
    _n_expect = 0
    for _i, d in enumerate(dates):
        if _i == 0:
            continue
        d0 = dates[_i - 1]
        for nm in (_lad_v.get(d) or {}):
            if (LFV.get(d0) or {}).get(nm):
                _n_expect += 1
                if nm not in TRV.get(d, {}):
                    err("v62 %s/%s ladders 有前日梯队但缺 transmit" % (d, nm)); n_v62 += 1
    if len(TRV) and sum(len(v) for v in TRV.values()) > sum(len(v) for v in _lad_v.values()):
        err("v62 transmit 条目数超过 ladders"); n_v62 += 1
    # ② 抽样 40：分层家数/名单/新增首板/层均涨 逐项独立重算
    _pool_v62 = {d: {s["code"]: s for s in pools.get(d, [])} for d in dates}
    _tr_flat = [(d, nm) for d, ths in TRV.items() for nm in ths]
    for d, nm in _rnd.sample(_tr_flat, min(40, len(_tr_flat))):
        _i = DIDXV.get(d)
        if not _i:
            continue
        d0 = dates[_i - 1]
        yst = (LFV.get(d0) or {}).get(nm) or []
        t = TRV[d][nm]
        exp_n = [sum(1 for s in yst if (s.get("boards") or 1) >= 3),
                 sum(1 for s in yst if (s.get("boards") or 1) == 2),
                 sum(1 for s in yst if (s.get("boards") or 1) == 1)]
        got_n = [x.get("n") for x in t.get("L") or []]
        if got_n != exp_n:
            err("v62 %s/%s 分层家数不符 %s vs %s" % (d, nm, got_n, exp_n)); n_v62 += 1
            continue
        # 层均涨/晋级重算
        for li, lo, hi in ((0, 3, 99), (1, 2, 2), (2, 1, 1)):
            stocks = [s for s in yst if lo <= (s.get("boards") or 1) <= hi]
            chgs, pr = [], 0
            for s in stocks:
                code = str(s.get("code") or "")
                b0 = s.get("boards") or 1
                b1 = (_pool_v62.get(d) or {}).get(code)
                if b1 and (b1.get("boards") or 1) > b0:
                    pr += 1
                pv, cc = _pc62(code, d), _cc62(code, d)
                if pv and cc:
                    chgs.append((cc / pv - 1) * 100)
            g = (t.get("L") or [])[li]
            if (g.get("cn") or 0) != len(chgs) or (g.get("pr") or 0) != pr:
                err("v62 %s/%s 层%d 可算数/晋级不符" % (d, nm, li)); n_v62 += 1
            elif chgs and abs(g.get("avg") - sum(chgs) / len(chgs)) > 0.11:
                err("v62 %s/%s 层%d 均涨不符" % (d, nm, li)); n_v62 += 1
        _yc = {str(s.get("code")) for s in yst}
        _exp_n1 = sum(1 for s in ((LFV.get(d) or {}).get(nm) or [])
                      if (s.get("boards") or 1) == 1 and str(s.get("code")) not in _yc)
        if t.get("n1") != _exp_n1:
            err("v62 %s/%s 新增首板不符" % (d, nm)); n_v62 += 1
    # ③ 回测 chgB 抽样 60：买入日收盘涨幅独立重算
    _bt_v = (D.get("nextRecsBacktest") or {}).get("records") or []
    _bt_chg = [r for r in _bt_v if r.get("chgB") is not None]
    for r in _rnd.sample(_bt_chg, min(60, len(_bt_chg))):
        pv, cc = _pc62(r["code"], r["B"]), _cc62(r["code"], r["B"])
        if pv and cc:
            _e = round((cc / pv - 1) * 100, 2)
            if abs(_e - r["chgB"]) > 0.02:
                err("v62 %s %s chgB 复算不符 %s vs %s" % (r["B"], r["name"], _e, r["chgB"])); n_v62 += 1
    print("v62 传导路径图：%d 个题材日（覆盖核对 %d），抽样 %d 复核 + chgB %d 笔抽样，异常 %d"
          % (sum(len(v) for v in TRV.values()), _n_expect,
             min(40, len(_tr_flat)), min(60, len(_bt_chg)), n_v62))
    n_v48 += n_v62

    # ---- v67：赚钱效应统计（earnStreak：连续两日换手赚钱机会个股，3板+）独立复核 ----
    n_v67 = 0
    ESV = D.get("earnStreak") or {}
    ES_EV = ESV.get("eventsAll") or []
    rng_es = ESV.get("allRange") or []
    if not ES_EV:
        err("v67 earnStreak 事件池为空"); n_v67 += 1
    _pm_es = {d: {s["code"]: s for s in pools.get(d, [])} for d in dates}
    for e in ES_EV:
        code, d, d1 = str(e.get("code", "")), e.get("d", ""), e.get("d1", "")
        if rng_es and not (rng_es[0] <= d <= rng_es[1]):
            err("v67 eventsAll %s %s 超出全量区间" % (d, code)); n_v67 += 1
        # t 日在涨停池、板位 ≥3
        st_ = (_pm_es.get(d) or {}).get(code)
        if not st_:
            err("v67 %s %s 不在 t 日涨停池" % (d, code)); n_v67 += 1; continue
        if (st_.get("boards") or 1) < 3:
            err("v67 %s %s t日板位%d<3" % (d, code, st_.get("boards") or 1)); n_v67 += 1
        # t-1 日在池且板位恰为 t板-1（连续晋级）
        s1_ = (_pm_es.get(d1) or {}).get(code)
        if not s1_ or (s1_.get("boards") or 1) != (st_.get("boards") or 1) - 1:
            err("v67 %s %s t-1 日非换手连板晋级" % (d, code)); n_v67 += 1
        # 两日非一字
        if code in (yz.get(d) or ()) or code in (yz.get(d1) or ()):
            err("v67 %s %s 含一字板" % (d, code)); n_v67 += 1
        # 今日涨幅 ≈ 涨停幅度
        lim = 0.05 if "ST" in e.get("name", "") else (0.20 if code.startswith(("30", "688"))
              else (0.30 if code.startswith(("83", "87", "43", "92")) else 0.10))
        if abs(e.get("chg", 0) - lim * 100) > 1.0:
            err("v67 eventsAll %s %s t日涨幅异常 %s" % (d, code, e.get("chg"))); n_v67 += 1
        # 抽样核对缺口标记（开盘 > 前日最高）
        if _rnd.random() < 0.2:
            _kb = _kb58_bucket(code).get(code) or {}
            _b1 = _kb.get(d1)
            _bt_ = _kb.get(d)
            if _b1 and _bt_ and len(_b1) >= 6:
                _exp_gap = 1 if (_b1[4] and _bt_[1] > _b1[4]) else 0
                if e.get("gapT") != _exp_gap:
                    err("v67 %s %s gapT 标记不符 %s vs %s" % (d, code, e.get("gapT"), _exp_gap)); n_v67 += 1
    # 全量池完整性：窗口内每个「t-1/t 连续换手涨停且 t 板位≥3」的个股都应在池中（反向抽样）
    _es_set = {(str(e.get("code")), e.get("d")) for e in ES_EV}
    _n_es_expect = 0
    for _i in range(max(1, len(dates) - 6), len(dates)):
        _t, _t1 = dates[_i], dates[_i - 1]
        for _s0 in (pools.get(_t) or []):
            _c0 = str(_s0["code"])
            if (_s0.get("boards") or 1) < 3 or _c0 in (yz.get(_t) or ()) or _c0 in (yz.get(_t1) or ()):
                continue
            _s1 = (_pm_es.get(_t1) or {}).get(_c0)
            if not _s1 or (_s1.get("boards") or 1) != (_s0.get("boards") or 1) - 1:
                continue
            _n_es_expect += 1
            if (_c0, _t) not in _es_set:
                err("v67 %s %s 满足连续两日换手连板(≥3板)但不在事件池" % (_t, _c0)); n_v67 += 1
    print("v67 赚钱效应统计：全量池 %d 笔（近5交易日应达标 %d），独立复核异常 %d"
          % (len(ES_EV), _n_es_expect, n_v67))
    n_v48 += n_v67

    # ---- v67：高位断板跟踪（brkTrack：昨日≥3板断板 → 跟踪3日）独立复核 ----
    n_brk = 0
    BTV = D.get("brkTrack") or {}
    BT_DAYS = BTV.get("days") or {}
    _bt_expect = {}
    def _cls67(code, d):
        _b = _kb58_bucket(code).get(str(code), {}).get(d)
        return _b[2] if (_b and len(_b) >= 5 and _b[2]) else None
    for _i in range(1, len(dates)):
        _p, _d0 = dates[_i - 1], dates[_i]
        _pc_map = {s["code"]: s for s in (pools.get(_d0) or [])}
        for _s0 in (pools.get(_p) or []):
            _b0 = _s0.get("boards") or 1
            _c0 = str(_s0["code"])
            if _b0 < 3:
                continue
            _cur = _pc_map.get(_c0)
            if _cur and (_cur.get("boards") or 1) > _b0:
                continue                      # 晋级，未断板
            if _cls67(_c0, _p) is None:
                continue                      # 断板日前收缺失（缓存截断/停牌豁免，build 同口径）
            for _k in range(3):
                _j = _i + _k
                if _j >= len(dates):
                    break
                if _cls67(_c0, dates[_j]) is None:
                    continue                  # 展示日收盘缺失，build 同口径跳过
                _bt_expect.setdefault(dates[_j], set()).add((_c0, _d0, _k))
    for _d, _items in BT_DAYS.items():
        if _d not in date_set:
            err("v67 brkTrack 展示日 %s 不在交易日历" % _d); n_brk += 1
        for _x in _items:
            _key = (str(_x.get("code")), _x.get("bd"), _x.get("n"))
            if _key not in _bt_expect.get(_d, set()):
                err("v67 brkTrack %s %s(%s) 多余条目 bd=%s n=%s"
                    % (_d, _x.get("name"), _x.get("code"), _x.get("bd"), _x.get("n"))); n_brk += 1
            _bt_expect.get(_d, set()).discard(_key)
    _n_missing = sum(len(v) for v in _bt_expect.values())
    for _d, _miss in _bt_expect.items():
        if _miss:
            err("v67 brkTrack %s 缺 %d 条断板跟踪（%s）"
                % (_d, len(_miss), list(_miss)[:3])); n_brk += len(_miss)
    print("v67 高位断板跟踪：%d 个展示日 / %d 条跟踪（应含 %d 条），独立复核异常 %d"
          % (len(BT_DAYS), sum(len(v) for v in BT_DAYS.values()),
             sum(len(v) for v in _bt_expect.values()) + sum(len(x) for x in BT_DAYS.values()), n_brk))
    n_v48 += n_brk

    # ---- v69：昨日梯队今日反馈（relayFb）独立复核 ----
    n_rf = 0
    RFV = D.get("relayFb") or {}
    RF_DAYS = RFV.get("days") or {}
    _pm_rf = {d: {str(s["code"]): s for s in (pools.get(d) or [])} for d in dates}
    _rf_rows = []
    for _d, _rows in RF_DAYS.items():
        _ii = DIDXV.get(_d)
        if not _ii:
            err("v69 relayFb 展示日 %s 不在交易日历" % _d); n_rf += 1; continue
        _dp = dates[_ii - 1]
        for _r in _rows:
            for _L in (_r.get("L") or []):
                for _x in (_L.get("st") or []):
                    _rf_rows.append((_d, _dp, _r, _L, _x))
    for _d, _dp, _r, _L, _x in _rnd.sample(_rf_rows, min(60, len(_rf_rows))):
        _code, _yb, _chg, _jb = str(_x[1]), _x[2], _x[3], _x[4]
        _s0 = (_pm_rf.get(_dp) or {}).get(_code)
        if not _s0 or (_s0.get("boards") or 1) != _yb:
            err("v69 %s %s 昨日板位不符（记录%d）" % (_dp, _code, _yb)); n_rf += 1
        # 角色与层板位一致
        _exp_role = "龙头" if _yb == _r.get("top") else ("助攻" if _yb >= 2 else "跟风")
        if _L.get("role") != _exp_role or _L.get("b") != _yb:
            err("v69 %s %s 角色/层板位不符 %s/%s" % (_d, _code, _L.get("role"), _L.get("b"))); n_rf += 1
        # 今日涨幅复算
        _c0 = _closev(_code, _dp); _c1 = _closev(_code, _d)
        if _c0 and _c1:
            _exp_chg = round((_c1 / _c0 - 1) * 100, 2)
            if _chg is None or abs(_exp_chg - _chg) > 0.05:
                err("v69 %s %s 今日涨幅不符 %s vs %s" % (_d, _code, _chg, _exp_chg)); n_rf += 1
            _cur = (_pm_rf.get(_d) or {}).get(_code)
            _exp_jb = 1 if (_cur and (_cur.get("boards") or 1) > _yb) else 0
            if _jb != _exp_jb:
                err("v69 %s %s 晋级标记不符 %s vs %s" % (_d, _code, _jb, _exp_jb)); n_rf += 1
        else:
            if _jb != -1:
                err("v69 %s %s 无价格却非 -1" % (_d, _code)); n_rf += 1
    # 反向抽样：最新交易日的每个「昨日梯队在册个股」都应在当日的反馈里
    if dates:
        _d_last = dates[-1]
        _dp_last = dates[-2] if len(dates) > 1 else None
        if _dp_last and _d_last in RF_DAYS:
            _seen = set()
            for _r in RF_DAYS[_d_last]:
                for _L in (_r.get("L") or []):
                    for _x in (_L.get("st") or []):
                        _seen.add((_r.get("th"), str(_x[1])))
            _prev_lf = {}
            for _th, _arr in (D.get("ladderFull") or {}).get(_dp_last, {}).items():
                for _s in _arr:
                    _prev_lf[(_th, str(_s.get("code")))] = 1
            _miss = [k for k in _prev_lf if k not in _seen]
            if _miss:
                err("v69 %s 反馈缺 %d 条昨日梯队个股（%s）" % (_d_last, len(_miss), _miss[:3])); n_rf += len(_miss)
    print("v69 昨日梯队今日反馈：%d 个展示日 / %d 条个股行，抽样 %d 复核，独立复核异常 %d"
          % (len(RF_DAYS), len(_rf_rows), min(60, len(_rf_rows)), n_rf))
    n_v48 += n_rf

    # ---- v70：个股当日状态（dayZT 涨停首封时间 / dayZB 炸板池）独立复核 ----
    n_st = 0
    ZT = D.get("dayZT") or {}
    ZB = D.get("dayZB") or {}
    if not ZT:
        err("v70 dayZT 为空"); n_st += 1
    for _d, _m in ZT.items():
        if _d not in date_set:
            err("v70 dayZT 日期 %s 不在交易日历" % _d); n_st += 1; continue
        _pc = {str(s["code"]): s for s in (pools.get(_d) or [])}
        _want = set(_pc)
        _got = set(_m)
        if _want != _got:
            err("v70 dayZT %s 个股集合与涨停池不符（多 %d / 少 %d）"
                % (_d, len(_got - _want), len(_want - _got))); n_st += 1
        # 首封时间必须与池内 first_seal 同日（HH:MM）
        for _c, _v in list(_m.items())[:40]:
            _s = _pc.get(_c) or {}
            _fs = str(_s.get("first_seal") or "")
            if _fs and len(_fs) >= 5 and _fs[:2].isdigit() and _v[1] and _v[1] != _fs[:5]:
                err("v70 dayZT %s %s 首封时间 %s ≠ 池内 %s" % (_d, _c, _v[1], _fs[:5])); n_st += 1
    for _d, _m in ZB.items():
        if _d not in date_set:
            err("v70 dayZB 日期 %s 不在交易日历" % _d); n_st += 1; continue
        _pc = {str(s["code"]) for s in (pools.get(_d) or [])}
        _over = _pc & set(_m)                 # 炸板股不可能同时收盘涨停
        if _over:
            err("v70 dayZB %s 有 %d 只同时出现在涨停池（%s）"
                % (_d, len(_over), list(_over)[:3])); n_st += len(_over)
        for _c, _v in _m.items():
            if _v[1] and (len(_v[1]) != 5 or not _v[1][:2].isdigit()):
                err("v70 dayZB %s %s 首封时间格式异常 %s" % (_d, _c, _v[1])); n_st += 1
    print("v70 个股当日状态：涨停池 %d 天 / 炸板池 %d 天（%d 个炸板股票日），独立复核异常 %d"
          % (len(ZT), len(ZB), sum(len(v) for v in ZB.values()), n_st))
    n_v48 += n_st

    # ---- v71：昨日梯队反馈右栏「题材今日梯队」（relayFb tl 字段）独立复核 ----
    # 口径：tl 个股必须当日真实涨停且板位与池一致；昨日梯队中的晋级股必须出现在 tl
    # （题材映射漂移时靠「昨日成员今日仍涨停」路径兜底）。
    n_tl = 0
    for _d, _rows in RF_DAYS.items():
        _pc = {str(s["code"]): s for s in (pools.get(_d) or [])}
        for _r in _rows:
            _tl = _r.get("tl")
            if _tl is None:
                err("v71 %s %s 缺 tl 字段" % (_d, _r.get("th"))); n_tl += 1; continue
            _tlset = set()
            for _x in _tl:
                _code = str(_x.get("code"))
                _tlset.add(_code)
                _s = _pc.get(_code)
                if not _s:
                    err("v71 %s %s tl 个股 %s 不在当日涨停池" % (_d, _r.get("th"), _code)); n_tl += 1
                elif (_s.get("boards") or 1) != (_x.get("boards") or 1):
                    err("v71 %s %s tl 个股 %s 板位不符 %s vs %s"
                        % (_d, _r.get("th"), _code, _x.get("boards"), _s.get("boards"))); n_tl += 1
            for _L in (_r.get("L") or []):
                for _x in (_L.get("st") or []):
                    if _x[4] == 1 and str(_x[1]) not in _tlset:
                        err("v71 %s %s 晋级股 %s 未进 tl" % (_d, _r.get("th"), _x[1])); n_tl += 1
    print("v71 题材今日梯队（relayFb tl）：%d 个展示日 / 独立复核异常 %d"
          % (len(RF_DAYS), n_tl))
    n_v48 += n_tl

    # ---- v72：炸板股题材映射（zbTheme，题材涨停时间表炸板列）独立复核 ----
    # 口径：zbTheme 个股必须在当日炸板池；其题材必须属于当日梯队（ladderFull）题材集。
    n_zbt = 0
    ZBTM = D.get("zbTheme") or {}
    for _d, _m in ZBTM.items():
        if _d not in ZB:
            err("v72 zbTheme %s 不在 dayZB 日期集" % _d); n_zbt += 1; continue
        _lfth = set((D.get("ladderFull") or {}).get(_d) or {})
        for _c, _th in _m.items():
            if _c not in ZB[_d]:
                err("v72 zbTheme %s %s 不在当日炸板池" % (_d, _c)); n_zbt += 1
            if _th not in _lfth:
                err("v72 zbTheme %s %s 题材 %s 不在当日梯队题材集" % (_d, _c, _th)); n_zbt += 1
    print("v72 炸板股题材映射（zbTheme）：%d 天 / %d 个炸板股票日，独立复核异常 %d"
          % (len(ZBTM), sum(len(v) for v in ZBTM.values()), n_zbt))
    n_v48 += n_zbt

    # ---- v73：历史复盘（赚钱效应股断板 3 日跟踪 + 空间龙头叠加）独立复核 ----
    # 口径复算：自事件日向前，断板日起 3 个交易日内再次涨停 → alive；否则 dead 移除。
    # 校验：抽样事件的存活判定 / 板位 / 题材；展示股必须复算存活且在事件池；
    #       当日空间龙头与 stages.spaceFull[0] 一致。
    n_hr = 0
    HRD = ((D.get("histReview") or {}).get("days")) or {}
    _ztset = {dd: {str(s["code"]) for s in (pools.get(dd) or [])} for dd in dates}
    _date_idx = {dd: i for i, dd in enumerate(dates)}
    _hr_best = {}
    for _e in ((D.get("earnStreak") or {}).get("eventsAll") or []):
        if not _e or _e.get("d") not in _date_idx:
            continue
        _k = (str(_e.get("code")), _e.get("d"))
        if _k not in _hr_best or (_e.get("b") or 1) > _hr_best[_k].get("b", 0):
            _hr_best[_k] = _e

    def _hr_alive(_code, _d):
        _i = _date_idx.get(_d)
        if _i is None:
            return False
        _N = len(dates)
        while True:
            _j = _i
            while _j + 1 < _N and str(_code) in _ztset[dates[_j + 1]]:
                _j += 1
            if _j + 1 >= _N:
                return True
            _bd = _j + 1
            _re, _cnt = None, 0
            for _k in range(3):
                if _bd + _k >= _N:
                    break
                _cnt += 1
                if _k >= 1 and str(_code) in _ztset[dates[_bd + _k]]:
                    _re = _bd + _k
                    break
            if _re is None:
                return _cnt < 3               # 窗口未走完（数据末端）视为存活
            _i = _re

    for (_hc, _hd2), _he in _rnd.sample(list(_hr_best.items()), min(80, len(_hr_best))):
        _day = HRD.get(_hd2) or {}
        _codes = {str(s.get("code")) for _t in (_day.get("themes") or [])
                  for s in (_t.get("stks") or [])}
        _alive = _hr_alive(_hc, _hd2)
        if _alive != (_hc in _codes):
            err("v73 %s %s 存活判定不符（复算%s / 页面%s）"
                % (_hd2, _hc, _alive, _hc in _codes)); n_hr += 1
            continue
        if _alive:
            _row = next((s for _t in (_day.get("themes") or [])
                         for s in (_t.get("stks") or []) if str(s.get("code")) == _hc), None)
            if not _row:
                err("v73 %s %s alive 却无行" % (_hd2, _hc)); n_hr += 1
                continue
            if (_row.get("boards") or 1) != (_he.get("b") or 1):
                err("v73 %s %s 板位不符 %s vs %s"
                    % (_hd2, _hc, _row.get("boards"), _he.get("b"))); n_hr += 1
            if (_row.get("theme") or "") != (_he.get("theme") or ""):
                err("v73 %s %s 题材不符 %s vs %s"
                    % (_hd2, _hc, _row.get("theme"), _he.get("theme"))); n_hr += 1
    for _hd2, _y in HRD.items():
        for _t in (_y.get("themes") or []):
            for _s in (_t.get("stks") or []):
                _c = str(_s.get("code"))
                if (_c, _hd2) not in _hr_best:
                    err("v73 %s %s 不在赚钱效应事件池" % (_hd2, _c)); n_hr += 1
                elif not _hr_alive(_c, _hd2):
                    err("v73 %s %s 断板 3 日未再板却仍显示" % (_hd2, _c)); n_hr += 1
    _stg_by = {x.get("date"): x for x in (D.get("stages") or [])}
    for _hd2, _y in HRD.items():
        _sf = ((_stg_by.get(_hd2) or {}).get("spaceFull") or [])
        _ld = _y.get("leader")
        if _sf and not _ld:
            err("v73 %s 缺当日空间龙头" % _hd2); n_hr += 1
        elif _ld and _sf and str(_ld.get("code")) != str(_sf[0].get("code")):
            err("v73 %s 空间龙头与 stages 不符" % _hd2); n_hr += 1
    print("v73 历史复盘：%d 个事件日 / 抽样 %d + 全量对账，独立复核异常 %d"
          % (len(HRD), min(80, len(_hr_best)), n_hr))
    n_v48 += n_hr

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
