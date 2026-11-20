# -*- coding: utf-8 -*-
"""v58：新旧龙头交接图 K 线缺口补全（同花顺逐年日K接口）。

背景：交接弹窗（v57 上下小图真实K线）按交接日取前后 ±8/±6 个交易日窗口，
但本地 K 线（新浪 1800 根 ≈ 2019-06 起 + 缓存漂移残缺）对 2017-2019 及
部分漂移时段大量缺 bar，窗口内出现成段 '-'，交接图「K线显示不全」。

方案：
  同花顺 v6 逐年日K接口 https://d.10jqka.com.cn/v6/line/hs_<code>/01/<year>.js
  返回某年「全部交易日」日K（JSONP），历史完整、无需代理。对交接龙头
  （data.json handover 空间 + 题材全部新旧龙头）逐只检查「交接窗口内缺失日期」，
  按年抓取补齐，只补缺、不覆盖既有 bar（避免口径冲突）。
  结果同时写回 .stock_kline_cache.json 与 klines/klines_N.json 分片
  （两者都是 build_merged._load_kbars 与前端 stockKBars 的数据源，只增不减）。
"""
import json
import os
import re
import sys
import threading
import time
import urllib.request
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor

OUT = os.path.dirname(os.path.abspath(__file__))
CACHE_F = os.path.join(OUT, ".stock_kline_cache.json")
KDIR = os.path.join(OUT, "klines")
YEAR_FROM = 2017          # 复盘数据起点
PAD = 10                  # 交接窗口向外多补 10 个交易日（前端窗口 8/6 + 余量）
WORKERS = 4

log = lambda m: print("[fill_k] %s" % m, flush=True)


def load_kbars():
    kb = {}
    import glob as _glob
    sources = [os.path.join(OUT, fn) for fn in
               (".kline_cache.json", ".sup_kline_cache.json", ".stock_kline_cache.json")]
    sources += sorted(_glob.glob(os.path.join(KDIR, "klines_*.json")))
    for p in sources:
        if not os.path.exists(p):
            continue
        try:
            for c, bars in json.load(open(p, encoding="utf-8")).items():
                if not isinstance(bars, list):
                    continue
                rec = kb.setdefault(str(c), {})
                for b in bars:
                    if b and len(b) >= 5 and b[2]:
                        old = rec.get(b[0])
                        if old is None or (b[5] if len(b) > 5 else 0) >= (old[5] if len(old) > 5 else 0):
                            rec[b[0]] = b
        except Exception as e:
            log("读取失败 %s: %s" % (p, e))
    return {c: sorted(r.values(), key=lambda x: x[0]) for c, r in kb.items()}


def bucket_of(code):
    return sum(int(ch) for ch in code if ch.isdigit()) % 8


def ths_symbol(code):
    if code.startswith(("8", "4", "92")):
        return None                    # 北交所：同花顺 v6 无数据，跳过
    return "hs_" + code


_lock = threading.Lock()
_stat = {"req": 0, "ok": 0, "empty": 0, "fail": 0, "fill": 0, "codes": 0}


def fetch_year(sym, year, retries=5):
    """抓同花顺某年日K，返回 [[date,open,close,low,high,vol], ...] 或 None。"""
    url = "https://d.10jqka.com.cn/v6/line/%s/01/%d.js" % (sym, year)
    for k in range(retries):
        try:
            req = urllib.request.Request(url, headers={
                "Referer": "https://q.10jqka.com.cn/",
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"})
            with urllib.request.urlopen(req, timeout=15) as r:
                txt = r.read().decode("utf-8", "ignore")
            m = re.search(r"\((\{.*\})\)\s*$", txt, re.S)
            if not m:
                return None
            obj = json.loads(m.group(1))
            data = obj.get("data") or ""
            out = []
            for row in str(data).split(";"):
                p = row.split(",")
                if len(p) < 6 or not re.match(r"^\d{8}$", p[0]):
                    continue
                # 同花顺字段：0日期 1开 2高 3低 4收 5量(手) —— 与 thsJsonp 同口径
                try:
                    o, c, l, h = float(p[1]), float(p[4]), float(p[3]), float(p[2])
                except ValueError:
                    continue
                # v63 防线：除权股（如山煤国际 600546 2019 配股）同花顺 v6 "01"
                # 返回归一化负值而非真实价格——close<=0 的 bar 一律丢弃，绝不入缓存。
                if c <= 0:
                    continue
                out.append([p[0], o, c, l, h, float(p[5] or 0)])
            return out
        except Exception:
            time.sleep(0.8 * (k + 1))
    return None


def main():
    D = json.load(open(os.path.join(OUT, "data.json"), encoding="utf-8"))
    dates = D["dates"]
    didx = {d: i for i, d in enumerate(dates)}
    ho = D.get("handover") or {}

    need = defaultdict(set)          # code -> 需求交易日集合（交接窗口 ±PAD）
    def add_event(evs):
        for e in evs or []:
            di = didx.get(e.get("date"))
            if di is None:
                continue
            lo = max(0, di - 8 - PAD)
            hi = min(len(dates) - 1, di + 6 + PAD)
            for x in (e.get("old") or []) + (e.get("new") or []):
                c = str(x.get("code") or "")
                if c:
                    need[c].update(dates[lo:hi + 1])
    add_event(ho.get("space"))
    for evs in (ho.get("theme") or {}).values():
        add_event(evs)
    log("交接龙头 %d 只" % len(need))

    KB = load_kbars()
    # 检查缺失：需求日内无 bar 或 close 非法
    miss = {}
    for c, ds in need.items():
        have = {b[0] for b in (KB.get(c) or []) if len(b) >= 5 and b[2]}
        bad = {d for d in ds if d not in have}
        if bad:
            miss[c] = bad
    log("有缺口的龙头 %d 只，共缺失 %d 个股票日"
        % (len(miss), sum(len(v) for v in miss.values())))

    # 按年聚合请求
    tasks = defaultdict(set)         # (code) -> set(year)
    for c, bad in miss.items():
        for d in bad:
            y = int(d[:4])
            for yy in (y - 1, y, y + 1):        # 前后年一并取，弥补整段丢失
                if YEAR_FROM <= yy <= 2026:
                    tasks[c].add(yy)
    n_req = sum(len(v) for v in tasks.values())
    log("需抓取 (股票,年) %d 组（并发 %d）" % (n_req, WORKERS))

    ths_ok = {}                      # code -> {year: bars}
    tls = threading.local()

    def work(item):
        c, years = item
        sym = ths_symbol(c)
        if not sym:
            with _lock:
                _stat["empty"] += len(years)
            return
        for y in sorted(years):
            bars = fetch_year(sym, y)
            with _lock:
                _stat["req"] += 1
                if bars is None:
                    _stat["fail"] += 1
                elif not bars:
                    _stat["empty"] += 1
                else:
                    _stat["ok"] += 1
                    ths_ok.setdefault(c, {})[y] = bars

    items = sorted(tasks.items())
    done = 0
    with ThreadPoolExecutor(max_workers=WORKERS) as ex:
        for _ in ex.map(work, items):
            done += 1
            if done % 200 == 0:
                log("进度 %d/%d 股票（请求 %d 成功 %d 失败 %d）"
                    % (done, len(items), _stat["req"], _stat["ok"], _stat["fail"]))
    log("抓取完成：请求 %d / 有效年 %d / 空年 %d / 失败 %d"
        % (_stat["req"], _stat["ok"], _stat["empty"], _stat["fail"]))

    # ---- 回填：只补缺失日期，不覆盖既有 bar ----
    filled_cache = defaultdict(list)      # 写回 cache 的增量
    filled_shard = defaultdict(lambda: defaultdict(list))   # bucket -> code -> bars
    for c, bad in miss.items():
        have = {b[0]: b for b in (KB.get(c) or []) if len(b) >= 5 and b[2]}
        add = {}
        for y, bars in (ths_ok.get(c) or {}).items():
            for b in bars:
                if b[0] in bad and b[0] not in have:
                    add[b[0]] = b
        if not add:
            continue
        _stat["fill"] += len(add)
        _stat["codes"] += 1
        merged = sorted(list(have.values()) + list(add.values()), key=lambda x: x[0])
        filled_cache[c] = merged
        filled_shard[bucket_of(c)][c] = merged

    if filled_cache:
        try:
            cache = json.load(open(CACHE_F, encoding="utf-8")) if os.path.exists(CACHE_F) else {}
            for c, bars in filled_cache.items():
                cache[c] = merge_keep(cache.get(c), bars)
            with open(CACHE_F, "w", encoding="utf-8") as f:
                json.dump(cache, f, ensure_ascii=False)
        except Exception as e:
            log("写缓存失败: %s" % e)
        for b, obj in filled_shard.items():
            p = os.path.join(KDIR, "klines_%d.json" % b)
            try:
                old = json.load(open(p, encoding="utf-8")) if os.path.exists(p) else {}
            except Exception:
                old = {}
            for c, bars in obj.items():
                old[c] = merge_keep(old.get(c), bars)
            with open(p, "w", encoding="utf-8") as f:
                json.dump(old, f, ensure_ascii=False, separators=(",", ":"))
    log("回填完成：%d 只龙头补 %d 个股票日" % (_stat["codes"], _stat["fill"]))

    # ---- 复检 ----
    KB2 = load_kbars()
    left = 0
    for c, ds in miss.items():
        have = {b[0] for b in (KB2.get(c) or []) if len(b) >= 5 and b[2]}
        left += sum(1 for d in ds if d not in have)
    log("复检：窗口内仍缺 %d 个股票日（停牌/退市/无数据属正常）" % left)


def merge_keep(old, new):
    m = {b[0]: b for b in (old or []) if b and len(b) >= 5}
    for b in new:
        if b[0] not in m:
            m[b[0]] = b
    return [m[k] for k in sorted(m)]


if __name__ == "__main__":
    main()
