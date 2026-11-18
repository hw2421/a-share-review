# -*- coding: utf-8 -*-
"""为「空间龙头 + 题材龙头」生成按需加载的日K分片：klines/klines_0..7.json
- 需求点：明细表空间龙头（每个空间日）+ 题材卡龙头（每个题材日）
- 每只股按其需求日聚簇（间隔 ≤45 交易日同簇），只保留 [簇首-20, 簇尾+30] 根日K
- 日K源：新浪（1800 根、约 2019-06 至今，不复权），缓存 .stock_kline_cache.json（与压制股缓存共用一份）
- 分片规则：code 各位数字之和 % 8，前后端保持一致
"""
import json
import os
import sys
import time

OUT = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, OUT)
import fetch_data as fd

CACHE_F = os.path.join(OUT, ".stock_kline_cache.json")
SUP_CACHE_F = os.path.join(OUT, ".sup_kline_cache.json")
KDIR = os.path.join(OUT, "klines")
# 用户口径：每只抓取的日K至少要显示到「断板后 5 个交易日」。
# 需求日簇尾之后再多留 30 根（远大于 5），并强制把缓存尾部补到最新交易日，
# 否则最近刚断板的票会缺后面几根，弹窗里看不到断板后走势。
PRE, POST = 60, 30
GAP = 45
NBUCKET = 8
# 历史扩展到 2021 年起：新浪 datalen=1800 根（约 2019-06 至今）才能覆盖
# 2021-2024 的需求日；原 520 根只到 2024-05，历史票会全部缺窗。
DATALEN = 1800

log = lambda m: print("[stock_k] %s" % m, flush=True)


def bucket_of(code):
    return sum(int(c) for c in code if c.isdigit()) % NBUCKET


def merge_bars(old, new):
    """按日期合并两段日K（new 覆盖 old），保持升序；用于只补尾部几根而不重抓全量。"""
    m = {b[0]: b for b in (old or [])}
    for b in (new or []):
        m[b[0]] = b
    return [m[k] for k in sorted(m)]


def refresh(dates, days, theme_detail, pools=None):
    # ---- 收集需求：code -> set(date) ----
    # 不只是「空间龙头 + 题材龙头」：页面上所有可点个股都要能调出日K，
    # 否则点题材块里的中位股 / 连板梯队 / 赚钱效应个股会静默无反应。
    need = {}
    def add(code, dt):
        c = str(code or "").strip()
        if c and dt:
            need.setdefault(c, set()).add(dt)
    for day in days:
        for s in day.get("spaceFull") or []:
            add(s.get("code"), day["date"])
        for s in day.get("suppressStocks") or []:
            add(s.get("code"), day["date"])
    for d, ts in theme_detail.items():
        for t in ts:
            for s in t.get("stocks") or []:        # 题材块内全部个股（不止龙头）
                add(s.get("code"), d)
    # 从当前 data.json 补齐其余可点来源（连板梯队 / 赚钱效应 / 近期涨停池 / 今日池）。
    # data.json 是上一轮产物，页面个股集合与本轮一致，足以覆盖全部点击点。
    try:
        D = json.load(open(os.path.join(OUT, "data.json"), encoding="utf-8"))
    except Exception:
        D = {}
    for day in D.get("stages", []):
        for s in day.get("spaceFull") or []:
            add(s.get("code"), day.get("date"))
        for s in day.get("suppressStocks") or []:
            add(s.get("code"), day.get("date"))
    for dt, blks in (D.get("themeDetail") or {}).items():
        for t in blks:
            for s in t.get("stocks") or []:
                add(s.get("code"), dt)
    for dt, rows in (D.get("lb") or {}).items():
        for r in rows:
            if isinstance(r, (list, tuple)) and len(r) >= 4:
                add(r[3], dt)
    for dt, groups in (D.get("money") or {}).items():
        for g in groups or []:
            for s in (g.get("items") or []) + (g.get("mids") or []):
                add(s.get("code") if isinstance(s, dict) else None, dt)
    for dt, arr in (D.get("recentPools") or {}).items():
        for s in arr:
            add(s.get("code") if isinstance(s, dict) else None, dt)
    for s in D.get("todayStocks") or []:
        add(s.get("code") if isinstance(s, dict) else None, D.get("today"))
    # 本轮全期涨停池：新增历史年份的 2 板+ 个股若既不是空间龙头、
    # 也不在题材块里，上一轮 data.json 里没有，必须直接从 pools 收集，
    # 否则连板梯队 / 赚钱效应里点这些个股会静默无日K。
    if pools:
        for d, arr in pools.items():
            for s in arr:
                add(s.get("code"), d)
    log("需求个股 %d 只 / %d 个股票日（全站可点个股）"
        % (len(need), sum(len(v) for v in need.values())))

    # ---- 日K缓存（继承压制股缓存，避免重复抓） ----
    try:
        cache = json.load(open(CACHE_F, encoding="utf-8"))
    except Exception:
        cache = {}
    if os.path.exists(SUP_CACHE_F):
        try:
            sup = json.load(open(SUP_CACHE_F, encoding="utf-8"))
            for k, v in sup.items():
                cache.setdefault(k, v)
        except Exception:
            pass
    idx = {d: i for i, d in enumerate(dates)}
    changed = 0
    today = dates[-1]
    for i, (code, ds) in enumerate(sorted(need.items()), 1):
        bars = cache.get(code) or []
        bds = {b[0] for b in bars}
        tail = bars[-1][0] if bars else ""
        # 近期仍有涨停的个股：缓存尾部必须补到最新交易日，
        # 才能完整覆盖「断板日 + 5 个交易日」的走势
        last_need = max((idx[d] for d in ds if d in idx), default=-1)
        stale = bool(tail) and tail < today and last_need >= len(dates) - 20
        # 缺历史需求日（2021-2024）时必须整段重抓 1800 根；只补 15 根尾部不够
        need_full = (not bars or len(bars[0]) < 6 or
                     not all(d in bds for d in ds if d in idx))
        if need_full or stale:
            fresh = fd.sina_kline(fd.fix_code(code), DATALEN if need_full else 15)
            if fresh:
                bars = merge_bars(bars, fresh)
                cache[code] = bars
                changed += 1
            time.sleep(0.06)
        if i % 200 == 0:
            log("  抓取进度 %d/%d（新抓 %d）" % (i, len(need), changed))
            if changed:                 # 分批落盘，中断也不丢已抓数据
                with open(CACHE_F, "w", encoding="utf-8") as f:
                    json.dump(cache, f, ensure_ascii=False)
            time.sleep(0.6)
    if changed:
        with open(CACHE_F, "w", encoding="utf-8") as f:
            json.dump(cache, f, ensure_ascii=False)
        log("新抓 %d 只（缓存 %d 只）" % (changed, len(cache)))

    # ---- 聚簇切窗 + 分桶 ----
    buckets = [{} for _ in range(NBUCKET)]
    empty = 0
    for code, ds in need.items():
        bars = cache.get(code) or []
        if not bars:
            empty += 1
            continue
        pos = {b[0]: i for i, b in enumerate(bars)}
        ix = sorted({pos[d] for d in ds if d in pos})
        if not ix:
            empty += 1
            continue
        groups = [[ix[0]]]
        for i in ix[1:]:
            if i - groups[-1][-1] <= GAP:
                groups[-1].append(i)
            else:
                groups.append([i])
        keep = set()
        for g in groups:
            keep.update(range(max(0, g[0] - PRE), min(len(bars), g[-1] + POST + 1)))
        out = [bars[i] for i in sorted(keep)]
        if out:
            buckets[bucket_of(code)][code] = out

    os.makedirs(KDIR, exist_ok=True)
    total = 0
    for b, obj in enumerate(buckets):
        p = os.path.join(KDIR, "klines_%d.json" % b)
        with open(p, "w", encoding="utf-8") as f:
            json.dump(obj, f, ensure_ascii=False, separators=(",", ":"))
        total += len(obj)
        log("  分片 %d：%d 只 / %.0f KB"
            % (b, len(obj), os.path.getsize(p) / 1024))
    log("分片个股合计 %d 只（无K线 %d 只）" % (total, empty))
    return total
