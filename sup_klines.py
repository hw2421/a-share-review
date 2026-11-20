# -*- coding: utf-8 -*-
"""爬取空间压制股断板日附近的日K走势 -> data.supKlines
数据源：新浪日K（不复权，datalen=1800 覆盖 2021 年至今）；按 code 缓存 .sup_kline_cache.json。
build_merged.py 组装输出前调用 refresh()；update_daily.py 每日更新自动继承。
supKlines 格式：{ "code_breakDate": [[date,open,close,low,high], ...] }（断板前 PRE 根 ~ 后 POST 根）
"""
import json
import os
import sys
import time

OUT = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, OUT)
import fetch_data as fd

CACHE_F = os.path.join(OUT, ".sup_kline_cache.json")
# 用户口径：日K至少要显示到断板后 5 天；这里留 30 根，并把缓存尾部补到最新交易日
PRE, POST = 8, 30            # 断板日前取 8 根、后取 30 根（>> 5 天）
DATALEN = 1800               # 新浪日K拉取根数（约 2019-06 至今，覆盖 2021 起的历史压制股）

log = lambda m: print("[sup_klines] %s" % m, flush=True)

STOCK_CACHE_F = os.path.join(OUT, ".stock_kline_cache.json")


def merge_bars(old, new):
    m = {b[0]: b for b in (old or [])}
    for b in (new or []):
        m[b[0]] = b
    return [m[k] for k in sorted(m)]


def _atomic_dump(obj, path):
    """原子写 JSON：先写临时文件再 os.replace，进程被 SIGKILL 也不会截断正本。"""
    import tempfile
    d = os.path.dirname(path) or "."
    fd_, tmp = tempfile.mkstemp(prefix=".tmp_sup_", dir=d)
    try:
        with os.fdopen(fd_, "w", encoding="utf-8") as f:
            json.dump(obj, f, ensure_ascii=False)
        os.replace(tmp, path)
    except Exception:
        try:
            os.remove(tmp)
        except Exception:
            pass
        raise


def _merge_stock_cache(cache, codes):
    """v60b：从全量日K缓存（含 THS 历史填充）流式抽取压制股缺失 bar，只补缺不覆盖。

    背景：sup 缓存条目会被新浪重抓/重写，而新浪 1800 根回溯上限 ≈2019-06，
    更早的断板日（如 002902 20190606）靠 fill_klines 的 THS 历史填充——那份数据
    在 .stock_kline_cache.json 里。若 sup 条目缩水，本函数每次构建自愈。
    """
    if not os.path.exists(STOCK_CACHE_F) or not codes:
        return 0
    s = open(STOCK_CACHE_F, encoding="utf-8", errors="ignore").read()
    dec = json.JSONDecoder()
    i, n = 0, len(s)
    while i < n and s[i] != "{":
        i += 1
    i += 1
    fixed = 0
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
        if str(key) not in codes:
            # 跳过该 value（压制股之外的不关心）
            try:
                _, i = dec.raw_decode(s, i)
            except Exception:
                nxt = s.find(',"', i)
                if nxt < 0:
                    break
                i = nxt + 1
            continue
        try:
            val, i = dec.raw_decode(s, i)
            if isinstance(val, list):
                old = {b[0]: b for b in (cache.get(str(key)) or [])
                       if isinstance(b, list) and b}
                add = 0
                for b in val:
                    if isinstance(b, list) and len(b) >= 5 and b[0] and b[0] not in old:
                        old[b[0]] = b
                        add += 1
                if add:
                    cache[str(key)] = [old[k] for k in sorted(old)]
                    fixed += 1
        except Exception:
            nxt = s.find(',"', i)
            if nxt < 0:
                break
            i = nxt + 1
    return fixed


def _bars_cover(bars, break_dates):
    """缓存日K是否覆盖全部断板日窗口（断板日在序列中，且其后至少还有 POST 根或已是末尾）"""
    if not bars:
        return False
    ds = [b[0] for b in bars]
    last = ds[-1]
    for bd in break_dates:
        if bd not in ds:
            return False
        i = ds.index(bd)
        if i + POST >= len(bars) and last < bd:   # 理论不会出现
            return False
    return True


def refresh(dates, days):
    """dates: 全部交易日; days: stages（含 suppressStocks）。返回 supKlines dict。"""
    try:
        cache = json.load(open(CACHE_F, encoding="utf-8"))
    except Exception:
        cache = {}
    pairs = {}                       # code -> set(breakDate)
    for day in days:
        for s in day.get("suppressStocks") or []:
            bd = s.get("breakDate")
            if bd and s.get("code"):
                pairs.setdefault(s["code"], set()).add(bd)
    log("压制股 %d 只 / %d 个断板点" % (len(pairs), sum(len(v) for v in pairs.values())))
    # v60b：先从全量缓存自愈缺失 bar（THS 历史填充），再做新浪补抓
    _fixed = _merge_stock_cache(cache, set(pairs))
    if _fixed:
        _atomic_dump(cache, CACHE_F)
        log("从全量缓存自愈 %d 只缺失 bar" % _fixed)

    idx = {d: i for i, d in enumerate(dates)}
    today = dates[-1]
    changed = 0
    for i, (code, bds) in enumerate(sorted(pairs.items()), 1):
        bars = cache.get(code)
        tail = bars[-1][0] if bars else ""
        # 近期断板的压制股：缓存补到最新交易日，保证断板后走势完整
        stale = bool(tail) and tail < today and any(
            idx.get(bd, -1) >= len(dates) - 20 for bd in bds)
        if not _bars_cover(bars, bds) or stale:
            # 老缓存只有 520 根（起点约 2024-05），历史断板日不在其中时若只补
            # 15 根尾部会永远补不上 2021/2023 起的压制股 → 必须整段重抓 1800 根
            bset = {b[0] for b in (bars or [])}
            need_full = (not bars) or any(bd not in bset for bd in bds)
            fresh = fd.sina_kline(fd.fix_code(code),
                                  DATALEN if need_full else 15)
            if fresh:
                cache[code] = merge_bars(bars, fresh)
                changed += 1
            time.sleep(0.12)
        if i % 60 == 0:
            log("  进度 %d/%d" % (i, len(pairs)))
    if changed:
        _atomic_dump(cache, CACHE_F)
        log("新抓日K %d 只（缓存 %d 只）" % (changed, len(cache)))

    sup = {}
    for code, bds in pairs.items():
        bars = cache.get(code) or []
        ds = [b[0] for b in bars]
        for bd in sorted(bds):
            if bd in ds:
                bi = ds.index(bd)
            else:                    # 退市/停牌缺失：取断板日前最近一根
                prev = [x for x in ds if x <= bd]
                if not prev:
                    continue
                bi = ds.index(prev[-1])
            seg = bars[max(0, bi - PRE): bi + POST + 1]
            if seg:
                sup["%s_%s" % (code, bd)] = seg
    log("supKlines %d 段" % len(sup))
    return sup
