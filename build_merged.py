# -*- coding: utf-8 -*-
"""合并 2025（Excel 湖南人复盘 OCR）与 2026（同花顺接口）数据 -> data.json / data.js
用法：python3 build_merged.py   （需先跑 fetch_data.py 与 excel_ingest.py）"""
import json
import os
import sys
import re
import datetime

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import fetch_data as fd

OUT = os.path.dirname(os.path.abspath(__file__))
CUT = "20260101"          # 2025/2026 分界

D = json.load(open(os.path.join(OUT, "data.json"), encoding="utf-8"))
ec = json.load(open(os.path.join(OUT, ".excel_cache.json"), encoding="utf-8"))
pool_cache = fd.load_pool_cache()

# ---- 1. 合并涨停池 ----
pools = {}
for d, v in ec["pools"].items():          # 2025：Excel OCR 池（板数取图上标注）
    if d < CUT and v:
        pools[d] = v
for d, v in pool_cache.items():           # 2026：同花顺原始池
    if d >= CUT and v:
        pools[d] = v
# 复盘图 OCR 产生的无效个股（乱码名 / 无行情代码、且无法反推真实代码）：
# 从涨停池剔除，否则会出现前端点不出日K的僵尸股，并触发覆盖率校验失败。
# 能反推真实代码的错码走 fetch_data.CODE_FIX 映射（数据 code 保持不变）。
OCR_INVALID = {"003100", "800130", "660000"}
_n_bad = 0
for d in list(pools):
    _before = len(pools[d])
    pools[d] = [s for s in pools[d]
                if str(s.get("code") or "") not in OCR_INVALID]
    _n_bad += _before - len(pools[d])
pools = {d: v for d, v in pools.items() if v}
dates = sorted(pools)
print("OCR 无效个股剔除 %d 条；合并后交易日 %d 天（%s ~ %s）"
      % (_n_bad, len(dates), dates[0], dates[-1]))

# 连板数口径：湖南人复盘标注优先（锚点），其余由池递推
try:
    hunan_boards = json.load(open(os.path.join(OUT, ".hunan_boards.json"),
                                  encoding="utf-8"))
except Exception:
    hunan_boards = {}

# 人工核对修正（OCR 误读，优先级最高）
ovp = os.path.join(OUT, ".board_overrides.json")
if os.path.exists(ovp):
    try:
        _ov = json.load(open(ovp, encoding="utf-8"))
        n_ov = 0
        for _d, _m in _ov.items():
            if _d.startswith("_"):
                continue
            for _c, _b in _m.items():
                hunan_boards.setdefault(_d, {})[_c] = _b
                n_ov += 1
        print("人工修正连板数 %d 处" % n_ov)
    except Exception as e:
        print("修正表读取失败：%s" % e)

# 连板数口径（v36）：模拟交易数据全部以同花顺复盘为准。
# 同花顺涨停池接口最早只支持 20260101（更早 date 参数不合法，官方无历史数据），
# 因此 2026 起连板数完全由同花顺涨停池逐日递推，湖南人 OCR 标注不再覆盖；
# 2025 及以前维持复盘 OCR 锚点（同花顺无历史可替换）。
streaks = fd.compute_streaks(pools,
                             {d: v for d, v in hunan_boards.items() if d < CUT})
for d in dates:
    for s in pools[d]:
        s["boards"] = streaks[d].get(s["code"], 1)
pool_map = {d: {s["code"]: s for s in pools[d]} for d in dates}

# ---- 1.5 次新股标记：上市不足 60 个交易日 -> 连板不纳入空间统计 ----
try:
    import bisect
    _kc = {}
    for _f in (".stock_kline_cache.json",):
        _p = os.path.join(OUT, _f)
        if os.path.exists(_p):
            for c, b in json.load(open(_p, encoding="utf-8")).items():
                _kc.setdefault(c, b)
    NEW_D = 60
    _ipo = {}
    for c, b in _kc.items():
        # 仅用 520 根全历史缓存判断；K线不足 500 根且首日落在数据区间内 = 数据区间内上市
        if b and len(b) >= 100 and len(b) < 500 and b[0][0] > dates[0]:
            _ipo[c] = bisect.bisect_left(dates, b[0][0])
    n_new = 0
    for i, d in enumerate(dates):
        for s in pools[d]:
            ii = _ipo.get(s["code"])
            if ii is not None and i - ii < NEW_D:
                s["new"] = True
                n_new += 1
    print("次新股标记：%d 只 / %d 个股票日不纳入空间统计" % (len(_ipo), n_new))
    _kc.clear()          # 379MB 全历史日K缓存用完立即释放，避免与后续 KBARS/CLOSE 叠加 OOM
    del _kc
except Exception as e:
    print("次新标记失败（不影响主数据）：%s" % e)

# ---- 2. 四阶段 / 空间龙头（含一字板排除） ----
print("重算四阶段模型 ...")
days = fd.build_stages(pools)

# ---- 3. 合并题材（全部以湖南人复盘为准：Excel OCR + 微博/淘股吧文章） ----
import theme_alias
THEME_ALIAS = theme_alias.ALIAS
excel_days = set(ec["themes"].keys())
theme_detail, theme_source = {}, {}
for d, ts in ec["themes"].items():        # Excel OCR：2021-01 ~ 2026-09 全期复盘长图
    theme_detail[d] = ts
    theme_source[d] = "excel"
# v30：文章输入改用固定快照。旧实现每轮都从 data.json 的 themeDetail（上一轮 3.4
# 的输出）回读文章块，于是「题材炒作图门槛一变 → 输出块数变 → 下一轮输入变 →
# 个股题材映射渐变 → 战法逐笔漂移」。首次从当前 data.json 抽出非池生成块写入
# 独立缓存，之后永远用同一份，保证重复构建幂等、每日自动更新口径不再跳动。
ART_F = os.path.join(OUT, ".theme_articles.json")
if os.path.exists(ART_F):
    article_cache = json.load(open(ART_F, encoding="utf-8"))
else:
    article_cache = {}
    for d, ts in D.get("themeDetail", {}).items():
        keep = [t for t in (ts or []) if t.get("src") not in ("pool", "ztpool")]
        if keep:
            article_cache[d] = keep
    with open(ART_F, "w", encoding="utf-8") as f:
        json.dump(article_cache, f, ensure_ascii=False)
    print("固定文章输入快照 .theme_articles.json：%d 天" % len(article_cache))
for d, ts in article_cache.items():      # 历史 tgb 文章（固定快照）
    if ts:
        theme_detail[d] = ts
        theme_source.setdefault(d, "tgb")
bf_dates = set()                      # 本次补抓（微博/文章）的日期：需按池严格清洗
bf_f = os.path.join(OUT, ".theme_backfill.json")
if os.path.exists(bf_f):                  # 新补抓（微博/移动版文章），优先级最高
    try:
        bf = json.load(open(bf_f, encoding="utf-8"))
        n_bf = 0
        for d, obj in bf.items():
            ts = (obj or {}).get("themes") or []
            if not ts:
                continue
            theme_detail[d] = ts
            theme_source[d] = obj.get("via") or "tgb"
            bf_dates.add(d)
            n_bf += 1
        print("补抓题材 %d 天（%s）" % (n_bf, bf_f.split("/")[-1]))
    except Exception as e:
        print("补抓题材读取失败：%s" % e)

# 题材块清洗：
#  1) 个股连板数按锚点递推结果校正（与空间口径一致）
#  2) 同块内按代码去重（OCR 会重复识别同一行）
#  3) 「必须涨停才统计」：任何来源的题材块，个股必须在当日真实涨停池内，
#     午间版/昨日复盘残留的未涨停个股一律剔除；文章标注的涨停家数不再用作展示值
#     （后续 3.4 统一取池内真实涨停只数），这里只用于「补抓日整块判废」
for d in list(theme_detail.keys()):
    sm = streaks.get(d, {})
    today = pool_map.get(d, {})
    strict = d in bf_dates
    for t in theme_detail[d]:
        seen, keep = set(), []
        for s in t.get("stocks", []):
            c = s.get("code")
            if not c or c in seen:
                continue
            if c not in today:            # 当日未涨停 → 不计入该题材
                continue
            seen.add(c)
            if c in sm:
                s["boards"] = sm[c]
            keep.append(s)
        raw_cnt = t.get("count") or len(keep)
        t["stocks"] = keep
        t["count"] = (raw_cnt if len(keep) >= raw_cnt * 0.6 else 0) \
            if strict else raw_cnt
    # 只保留有真实涨停个股的块；入链门槛在 3.4 按「≥3板且非孤股」统一过滤
    theme_detail[d] = [t for t in theme_detail[d]
                       if t.get("stocks") and (not strict or t["count"] > 0)]

for d in list(theme_detail.keys()):
    ts = theme_detail[d]
    agg = {}
    for t in ts:
        if t["name"] in fd.NON_THEME:      # 旧缓存里残留的非题材块（其他个股等）
            continue
        t["name"] = THEME_ALIAS.get(t["name"], t["name"])
        maxb = max([s["boards"] for s in t.get("stocks", [])] or [0])
        t["maxB"] = maxb
        o = agg.get(t["name"])
        if not o:
            agg[t["name"]] = t
            continue
        seen = {s["code"] for s in o["stocks"]}
        o["stocks"].extend(s for s in t["stocks"] if s["code"] not in seen)
        o["count"] = max(o["count"], t["count"])
        o["maxB"] = max(o["maxB"], t["maxB"])
        o["amount"] = max(o.get("amount", 0), t.get("amount", 0))
    # 题材瘦身（与主流程一致：每板数档最多2只、仅保留最高6档、去 reason）
    # v39.2 修复：瘦身只作用于「展示 stocks」。完整清单先存 _full ——
    # 个股题材映射 theme_at（3.2 的 day_map）必须用完整清单生成，
    # 否则映射被瘦身截断（如 20261118 国产算力 26 只只剩 5 只），
    # 连带题材梯队家数、战法板块归属、决策页全部失真。
    for t in agg.values():
        t["_full"] = [{"code": s["code"], "name": s.get("name"),
                       "boards": s.get("boards") or 1}
                      for s in t.get("stocks", []) if s.get("code")]
        by = {}
        for s in t.get("stocks", []):
            by.setdefault(s["boards"], []).append(s)
        out = []
        for b in sorted(by, reverse=True)[:6]:
            out.extend(by[b][:2])
        for s in out:
            s.pop("reason", None)
        t["stocks"] = out
    theme_detail[d] = list(agg.values())
theme_detail = {d: ts for d, ts in theme_detail.items() if ts}
theme_dates = sorted(theme_detail)

# 题材名归并（OCR 错字）
matrix = {}
for d, ts in theme_detail.items():
    for t in ts:
        e = matrix.setdefault(t["name"], {})
        e[d] = {"count": t["count"], "maxB": t["maxB"],
                "amount": t.get("amount", 0)}
matrix = fd.merge_theme_names(matrix)
for d, ts in theme_detail.items():
    for t in ts:
        for r, series in matrix.items():
            if d in series and t["name"] != r and fd._edit_dist(t["name"], r) <= 1:
                t["name"] = r
                break

# ---- 3.2 个股题材映射（当日文章块优先，缺失则沿用最近一次） ----
# 湖南人尚未发布复盘的交易日：用同花顺「涨停原因」给无归属个股补题材。
# 涨停原因取自当日真实涨停池（必须涨停才统计），不是估算；题材名只允许
# 落到全期已有题材，避免凭空造出新题材名。
_REASON_KW = [
    ("GPU", "半导体"), ("芯片", "半导体"), ("半导体", "半导体"), ("存储", "半导体"),
    ("光刻", "半导体"), ("靶材", "半导体"), ("MicroLED", "半导体"), ("封装", "半导体"),
    ("AI眼镜", "消费电子"), ("消费电子", "消费电子"),
    ("小家电", "消费"), ("家电", "消费"), ("百货", "消费"), ("零售", "消费"),
    ("食品", "消费"), ("免税", "消费"),
    ("AI算力", "算力"), ("算力", "算力"), ("液冷", "算力"),
    ("服务器", "算力"), ("数据中心", "算力"), ("云计算", "算力"),
    ("机器人", "机器人"), ("光伏", "光伏"), ("储能", "光伏"),
    ("固态电池", "固态电池"), ("锂电池", "锂电池"),
    ("变压器", "智能电网"), ("电网", "智能电网"),
    ("可控核聚变", "可控核聚变"), ("核聚变", "可控核聚变"),
    ("短剧", "短剧"), ("影视", "短剧"), ("游戏", "短剧"),
    ("创新药", "医药"), ("医药", "医药"),
    ("商业航天", "商业航天"), ("航天", "商业航天"), ("军工", "商业航天"),
    ("深海", "深海科技"), ("房地产", "房地产"), ("地产", "房地产"),
    ("跨境电商", "跨境电商"), ("跨境支付", "跨境电商"),
    ("光模块", "光通信"), ("光通信", "光通信"),
    ("人工智能", "AI应用"), ("AI应用", "AI应用"),
]
_all_names = {t["name"] for ts in theme_detail.values() for t in ts}

def reason_theme(s):
    for tag in (s.get("reason") or "").replace("＋", "+").split("+"):
        tag = tag.strip()
        if not tag:
            continue
        for kw, nm in _REASON_KW:
            if kw in tag and nm in _all_names:
                return nm
    return ""

theme_at, carry = {}, {}
n_reason = 0
for d in dates:
    day_map = {}
    # v39.2：映射必须用完整清单 _full（瘦身只截断展示，不截断映射）
    for t in theme_detail.get(d, []):
        for s in (t.get("_full") or t.get("stocks", [])):
            if s.get("code"):
                day_map[s["code"]] = t["name"]
    if not theme_detail.get(d):        # 无复盘日 → 涨停原因兜底
        for s in pools[d]:
            c = s["code"]
            if c not in carry and c not in day_map:
                nm = reason_theme(s)
                if nm:
                    day_map[c] = nm
                    n_reason += 1
    # v39.2：有复盘日严格「以湖南人复盘表为准」—— theme_at 只认当日清单，
    # 清单外涨停股不做题材映射。历史 carry / 涨停原因会把湖南人未列的股票
    # 多算进同名题材（如化工应 5 实 11），必须杜绝。
    carry.update(day_map)
    theme_at[d] = dict(day_map) if theme_detail.get(d) else dict(carry)
# _full 只服务于映射生成，输出前删除（避免进 data.js 膨胀）
for _ts in theme_detail.values():
    for _t in _ts:
        _t.pop("_full", None)
print("无复盘文章日：按真实涨停原因补题材映射 %d 个股票日" % n_reason)

# ---- 3.3 每日题材梯队：当日涨停池 × 题材映射（这才是明细表「题材·梯队」的唯一数据源） ----
# 旧实现用「当日文章块」算梯队，块不足 5 只被丢弃后前端回看 6 天，
# 于是出现「4 板龙头配 3-1 梯队」的错配；改为按当日池重建后，梯队必然含当日最高板。
def slim_stocks(ss):
    by = {}
    for s in ss:
        by.setdefault(s["boards"], []).append(s)
    out = []
    for b in sorted(by, reverse=True)[:6]:
        out.extend(by[b][:2])
    return out


def seal_time(s):
    """个股首次封板（涨停）时间 HH:MM。
    2026 同花顺池字段 first_seal='09:35:42'，2021-2025 Excel OCR 字段 time='9:31'。
    统一规范成 5 位 HH:MM；无法识别返回空串（前端不显示时间标签）。"""
    t = (s.get("first_seal") or s.get("time") or "").strip()
    m = re.match(r"^(\d{1,2}):(\d{2})", t)
    if not m:
        return ""
    h = int(m.group(1))
    return "%02d:%s" % (h, m.group(2)) if 0 <= h <= 23 else ""


ladders = {}
# 日期 -> 题材 -> 当日梯队「完整」个股：覆盖当日全部题材（不只入链题材），
# 保证题材卡 / 赚钱效应卡点「梯队」浮层都能列出当日全部涨停个股
ladder_full = {}
for d in dates:
    m = theme_at.get(d, {})
    grp = {}
    for s in pools[d]:
        nm = m.get(s["code"])
        if not nm:
            continue
        grp.setdefault(nm, []).append(
            {"code": s["code"], "name": s["name"], "boards": s["boards"],
             "time": seal_time(s)})
    if not grp:
        continue
    lad = {}
    for nm, ss in grp.items():
        bl = sorted({x["boards"] for x in ss}, reverse=True)
        lad[nm] = {"count": len(ss), "boards": bl,
                   "ladder": "-".join(str(b) for b in bl), "maxB": bl[0],
                   "stocks": sorted(ss, key=lambda x: (-x["boards"],
                                                       x["time"] or "99:99", x["code"]))}
    # 梯队完全由「当日真实涨停池」个股构成：文章标注家数不再并入、文章里
    # 未真实涨停的个股也不并入（用户口径：必须涨停才统计，禁止"≈约等于"估算）。
    # 卡片龙头、梯队、家数、连板梯队页因此天然同源，不会再出现错配。
    for nm, e in lad.items():
        # 浮层用完整清单（按板数降序；v44 同一身位再按涨停时间升序，早封板的排前面）；
        # 明细表/卡片仍用 slim 版控制体积
        e["full"] = [{"code": x["code"], "name": x["name"],
                      "boards": x["boards"], "t": x["time"]}
                     for x in sorted(e["stocks"],
                                     key=lambda x: (-x["boards"], x["time"] or "99:99",
                                                    x["code"]))]
        ladder_full.setdefault(d, {})[nm] = e["full"]
        e["stocks"] = slim_stocks(e["stocks"])
    ladders[d] = lad

# ---- 3.4 题材炒作入链门槛（v43 用户口径）----
# 「有连板且涨停个股 > 3 只的题材就纳入展示」：
#   板块 maxB >= 2（有连板）且池内该题材真实涨停股 >= 4 只（>3）。
# 家数 / 梯队 / 个股一律取当日真实涨停池，不再使用文章标注家数，
# 也不再有「池推估算(≈)」块 —— 必须涨停才统计。
# 注：本段只重建 themeDetail / themeDates（题材炒作图与线路图的数据源）。
# theme_at / ladders / ladderFull / money 均在本段之前生成，strategies.build
# 也不读 theme_detail，因此战法、机会池、交易复盘、boardEarn 口径完全不受影响。
MIN_BOARD, MIN_STOCKS = 2, 4
n_syn = 0
for d in dates:
    lad = ladders.get(d, {})
    by_name = {t["name"]: t for t in (theme_detail.get(d) or [])}
    # v35（用户口径）：当日有湖南人复盘（淘股吧文章 / 微博 / Excel 长图）时，
    # 题材定义只认博客里列出的板块——不再把「历史题材映射 carry + 同花顺涨停原因」
    # 聚合出来的板块混进当天的题材炒作图。没有复盘的交易日才用池兜底。
    has_art = theme_source.get(d) in ("tgb", "weibo", "excel")
    allowed = set(by_name) if has_art else None
    keep = []
    for nm, e in lad.items():
        if allowed is not None and nm not in allowed:
            continue
        if nm in fd.NON_THEME:
            continue
        if e["maxB"] < MIN_BOARD or e["count"] < MIN_STOCKS:
            continue
        is_new = nm not in by_name
        if is_new:
            n_syn += 1
        t = by_name.get(nm) or {"name": nm, "amount": 0}
        t["name"] = nm
        t["count"] = e["count"]        # 池内真实涨停家数（准确值，非估算）
        t["maxB"] = e["maxB"]
        t["stocks"] = e["stocks"]      # 池内真实涨停个股
        t["src"] = "ztpool" if is_new else (t.pop("src", None) or "article")
        ladder_full.setdefault(d, {})[nm] = e["full"]
        keep.append(t)
    keep.sort(key=lambda x: (-x["maxB"], -x["count"], x["name"]))
    if keep:
        theme_detail[d] = keep
        theme_source.setdefault(d, "ztpool")
    else:
        theme_detail.pop(d, None)
        theme_source.pop(d, None)
print("按真实涨停池重建题材块，新建 %d 个（门槛：有连板≥2板且板块涨停 >3 只）" % n_syn)
# 非交易日（周末/节假日）的题材块无法挂到任何一行，直接丢弃（如 2026-09-13 周日）
_drop = [d for d in theme_detail if d not in pool_map]
if _drop:
    for d in _drop:
        theme_detail.pop(d, None)
        theme_source.pop(d, None)
    print("丢弃非交易日题材块 %d 天：%s" % (len(_drop), ",".join(sorted(_drop)[:6])))
theme_dates = sorted(theme_detail)

# ---- 4. 空间龙头回填题材 ----
def theme_of(code, idx):
    return (theme_at.get(dates[idx]) or {}).get(code, "")

for k, day in enumerate(days):
    for s in day.get("spaceFull", []):
        s["theme"] = theme_of(s["code"], k)
    for s in day.get("suppressStocks", []):
        s["theme"] = theme_of(s["code"], k)
        # 炒作概念取自该股最后一个涨停日（breakDate 为断板日，不在池内）
        prev_ds = [x for x in dates if x < s.get("breakDate", "")]
        if prev_ds:
            p = pool_map[prev_ds[-1]].get(s.get("code"))
            if p and p.get("reason"):
                s["reason"] = p["reason"]

# ---- 5. 空间龙头连板周期 ----
seen = {}
for day in days:
    for s in day.get("spaceFull", []):
        if s["code"] not in seen:
            seen[s["code"]] = {"name": s["name"],
                               "theme": s.get("theme", ""), "pts": []}
        seen[s["code"]]["theme"] = s.get("theme", "") or seen[s["code"]]["theme"]
space_series = []
for code, obj in seen.items():
    for dt in dates:
        s2 = pool_map[dt].get(code)
        if s2:
            obj["pts"].append([dt, s2["boards"]])
    if obj["pts"] and max(p[1] for p in obj["pts"]) >= 3:
        obj["maxB"] = max(p[1] for p in obj["pts"])
        space_series.append(obj)
space_series.sort(key=lambda o: (-o["maxB"], o["pts"][0][0]))

# ---- 5.5 空间压制股断板日附近日K（供前端弹窗展示断板后走势） ----
# SKIP_KLINES=1：日K分片 / 压制股日K 已由上一次构建写好且未变化，跳过刷新可避免
# 379MB 全历史缓存被重复加载（8GB 内存上限下曾触发 OOM）；日常更新不设此开关。
SKIP_KLINES = os.environ.get("SKIP_KLINES") == "1"
if SKIP_KLINES:
    sup_kl = D.get("supKlines") or {}
    print("SKIP_KLINES：沿用已有 supKlines %d 段，跳过日K刷新" % len(sup_kl))
else:
    try:
        import sup_klines
        sup_kl = sup_klines.refresh(dates, days)
    except Exception as e:
        print("压制股日K爬取失败（不影响主数据）: %s" % e)
        sup_kl = {}

    # ---- 5.55 空间龙头 / 题材龙头 日K分片（点击明细表个股、题材卡时按需加载） ----
    try:
        import stock_klines
        stock_klines.refresh(dates, days, theme_detail, pools)
    except Exception as e:
        print("个股日K分片生成失败（不影响主数据）: %s" % e)

# ---- 5.6 本地日K（收盘价）缓存：赚钱效应涨跌幅 + 主包日K尾部补齐，避免重复网络请求 ----
def _load_kbars():
    kb = {}
    for fn in (".stock_kline_cache.json", ".sup_kline_cache.json", ".kline_cache.json"):
        p = os.path.join(OUT, fn)
        if not os.path.exists(p):
            continue
        try:
            for c, bars in json.load(open(p, encoding="utf-8")).items():
                if not isinstance(bars, list):
                    continue
                rec = kb.setdefault(str(c), {})
                for b in bars:
                    if b and len(b) >= 5:
                        rec[b[0]] = b
        except Exception as e:
            print("日K缓存读取失败 %s：%s" % (fn, e))
    return kb

KBARS = _load_kbars()
CLOSE = {c: {d: b[2] for d, b in rec.items()} for c, rec in KBARS.items()}
DIDX = {d: i for i, d in enumerate(dates)}

def chg_on(code, d):
    """个股在交易日 d 的涨跌幅（不复权收盘价环比上一交易日），无数据返回 None。"""
    rec = CLOSE.get(str(code))
    if not rec or d not in rec or not rec[d]:
        return None
    i = DIDX.get(d)
    if i is None or i == 0:
        return None
    for j in range(i - 1, -1, -1):
        if dates[j] in rec:
            prev = rec[dates[j]]
            return round((rec[d] / prev - 1) * 100, 2) if prev else None
    return None

# ---- 5.6.0 题材阶段（四分类，与前端 TStages 完全同一算法） ----
# 赚钱效应卡片「题材后」展示的当日阶段，必须与题材炒作页同一口径：
#   启动=全期首次 / 相隔≥5个交易日重新出现；加强/转弱=相邻交易日涨停家数增减；
#   反复=断了1~3日重新出现或相邻日家数持平。
_TIDX = {d: i for i, d in enumerate(dates)}
_occ = {}
for _d in sorted(theme_detail):
    for _t in theme_detail[_d]:
        _arr = _occ.setdefault(_t["name"], [])
        if _arr[-1:] != [_d]:
            _arr.append(_d)
_stage_map = {}
for _nm, _ds in _occ.items():
    _st = {}
    for _k, _d in enumerate(_ds):
        _c = next((t["count"] for t in theme_detail[_d] if t["name"] == _nm), 0)
        if _k == 0:
            _st[_d] = "启动"; continue
        _gap = _TIDX[_d] - _TIDX[_ds[_k - 1]]
        if _gap >= 5:
            _st[_d] = "启动"; continue
        if _gap >= 2:
            _st[_d] = "反复"; continue
        _pc = next((t["count"] for t in theme_detail[_ds[_k - 1]]
                    if t["name"] == _nm), 0)
        _st[_d] = "加强" if _c > _pc else ("转弱" if _c < _pc else "反复")
    _stage_map[_nm] = _st

# ---- 5.6.1 赚钱效应（用户口径，全期按当日真实涨停池重算，不再依赖文章/估算） ----
# 只显示「板块有三连板或以上个股、今日再次涨停」且「板块当日至少 3 只涨停」的题材：
#   当日涨停池里该题材最高板 >= 3（这些股今日确实涨停、且是连板晋级），
#   且池内该题材真实涨停个股 >= 3 只。
# 板块日均涨幅：只统计板块内「昨日 2 连板及以上」的个股今日涨跌幅，剔除首板。
def _dbar(c, d):
    b = (KBARS.get(str(c)) or {}).get(d)
    return b if (b and len(b) >= 6) else None

def _prev_close(code, d):
    rec = KBARS.get(str(code))
    if not rec or d not in rec or not rec[d]:
        return None
    i = DIDX.get(d)
    if i is None:
        return None
    for j in range(i - 1, -1, -1):
        b = rec.get(dates[j])
        if b and len(b) >= 3 and b[2]:
            return b[2]
    return None

def _lim_of(code, name=""):
    if "ST" in (name or "").upper():
        return .05
    if code.startswith(("30", "688")):
        return .20
    if code.startswith(("8", "4", "92")):
        return .30
    return .10

MIN_MONEY_STOCKS = 3
money = {}
for i in range(1, len(dates)):
    d, pd_ = dates[i], dates[i - 1]
    mt = theme_at.get(d, {})
    today_grp, prev_grp = {}, {}
    for s in pool_map[d].values():
        nm = mt.get(s["code"])
        if nm and nm not in fd.NON_THEME:
            today_grp.setdefault(nm, []).append(s)
    for s in pool_map[pd_].values():
        # 昨日个股也按「当日题材归属」分组：避免题材跨日改名导致 2 板+ 个股
        # 被算到旧题材名下，出现明明有 3 板龙头却 avg2 为空
        nm = mt.get(s["code"])
        if nm and nm not in fd.NON_THEME and (s["boards"] or 1) >= 2:
            prev_grp.setdefault(nm, []).append(s)
    rows = []
    for nm, ss in today_grp.items():
        bl = sorted({(s["boards"] or 1) for s in ss}, reverse=True)
        if bl[0] < 3:                     # 无 3 板以上个股今日再涨停 → 不纳入
            continue
        if len(ss) < MIN_MONEY_STOCKS:    # 板块当日涨停不足 3 只 → 不纳入
            continue
        prev2 = prev_grp.get(nm, [])
        chgs = [c for c in (chg_on(s["code"], d) for s in prev2) if c is not None]
        lead = max(ss, key=lambda s: (s["boards"] or 1))
        # v40：龙头一字板不统计为赚钱效应 —— 板块须有「换手」连板龙头
        _lb = (KBARS.get(str(lead["code"])) or {}).get(d)
        _lpv = _prev_close(str(lead["code"]), d)
        if _lb and len(_lb) >= 5 and _lpv:
            _lm = _lim_of(str(lead["code"]),
                          ((pool_map.get(d) or {}).get(lead["code"]) or {}).get("name"))
            if _lb[2] / _lpv - 1 >= _lm - .006 and _lb[1] == _lb[3] == _lb[2]:
                continue
        rows.append({
            "theme": nm,
            "ladder": "-".join(str(b) for b in bl),
            "maxB": bl[0],
            "count": len(ss),
            "lead": lead["name"],
            "leadCode": lead["code"],
            "avg2": round(sum(chgs) / len(chgs), 2) if chgs else None,
            "n2": len(chgs),
            "stage": _stage_map.get(nm, {}).get(d, ""),   # 当日题材炒作页阶段
        })
    if rows:
        rows.sort(key=lambda g: (-g["maxB"], -g["count"], g["theme"]))
        money[d] = rows

# ---- 5.6.1b 题材阶段补全（v35）----
# 题材炒作页只保留「湖南人博客里列出的板块」，因此有些赚钱效应题材（由涨停原因
# 归属出来的板块）当天没有题材块、拿不到阶段。这类题材用「自身赚钱效应序列」
# 按同一套规则补算阶段，避免卡片上出现空阶段。
_mseq = {}
for _d in sorted(money):
    for _g in money[_d]:
        _mseq.setdefault(_g["theme"], []).append((_d, _g["count"]))
_mstage = {}
for _nm, _arr in _mseq.items():
    _arr.sort()
    _st = {}
    for _k, (_d, _c) in enumerate(_arr):
        if _k == 0:
            _st[_d] = "启动"; continue
        _gap = DIDX[_d] - DIDX[_arr[_k - 1][0]]
        if _gap >= 5:
            _st[_d] = "启动"; continue
        if _gap >= 2:
            _st[_d] = "反复"; continue
        _pc = _arr[_k - 1][1]
        _st[_d] = "加强" if _c > _pc else ("转弱" if _c < _pc else "反复")
    _mstage[_nm] = _st
_n_fill = 0
for _d in money:
    for _g in money[_d]:
        if not _g.get("stage"):
            _g["stage"] = _mstage.get(_g["theme"], {}).get(_d, "")
            _g["stageSrc"] = "money"
            _n_fill += 1
if _n_fill:
    print("题材阶段补全：%d 个赚钱效应题材日无对应题材块，按赚钱效应序列补算" % _n_fill)
print("赚钱效应：%d 个交易日 / %d 个题材（板块当日有 ≥3 板个股再涨停、"
      "且板块至少 %d 只涨停；日均涨幅仅统计 2 板+个股）"
      % (len(money), sum(len(v) for v in money.values()), MIN_MONEY_STOCKS))

# ---- 5.6.2 连板周期 → 断板后 5 个交易日（前端日K窗口至少显示到断板后 5 天）----
# 格式：code -> [[周期首涨停日, 最后涨停日, 最后涨停日后第6个交易日], ...]
# 连续涨停（相邻交易日）归为同一周期；第 6 个交易日 = 断板日之后 5 个交易日。
stock_break = {}
for code in {s["code"] for v in pools.values() for s in v}:
    ds = sorted(d for d in dates if code in pool_map[d])
    if not ds:
        continue
    runs, cur = [], [ds[0]]
    for x in ds[1:]:
        if DIDX[x] - DIDX[cur[-1]] == 1:
            cur.append(x)
        else:
            runs.append(cur)
            cur = [x]
    runs.append(cur)
    stock_break[code] = [
        [r[0], r[-1], dates[min(DIDX[r[-1]] + 6, len(dates) - 1)]]
        for r in runs
    ]

# ---- 5.6.25 三战法复盘 / 板块赚钱效应验证 / K线骷髅头（统一由 strategies.py 计算）----
import strategies
import money_effect as ME

# v29：板块赚钱效应验证（题材梯队 + 同花顺日K），既是三战法共同门槛，
# 也写入 data.json 供前端展示与校验脚本独立复算（口径唯一来源 money_effect.compute）。
BOARD_EARN = ME.compute(dates, ladder_full, money, KBARS)
BOARD_EARN_SLIM = {}
for (_d, _nm), _v in BOARD_EARN.items():
    BOARD_EARN_SLIM.setdefault(_d, {})[_nm] = {
        "ok": bool(_v["ok"]), "ladder": _v["ladder"], "lead": _v["lead"],
        "low": _v["low"], "mid": _v["mid"], "board": _v["board"],
        "nFol": _v["nFol"], "reason": _v["reason"] if not _v["ok"] else "",
    }
print("板块赚钱效应验证：%d 个题材日，通过 %d（%.1f%%）" %
      (len(BOARD_EARN), sum(1 for v in BOARD_EARN.values() if v["ok"]),
       sum(1 for v in BOARD_EARN.values() if v["ok"]) / len(BOARD_EARN) * 100
       if BOARD_EARN else 0))

S = strategies.build({
    "dates": dates, "DIDX": DIDX, "days": days, "pool_map": pool_map,
    "theme_at": theme_at, "theme_detail": theme_detail, "money": money,
    "KBARS": KBARS, "CLOSE": CLOSE, "carry": carry,
    "yz": fd.yizi_map(pools),
    "ladder_full": ladder_full,     # 当日真实涨停梯队（题材卡浮层 / 板块助攻同源）
    "board_earn": BOARD_EARN,       # v29：三战法共同前置（板块赚钱效应验证）
})
review = S["review"]              # {method: [trade,...]}
reviewMeta = S["reviewMeta"]
chances = S["chances"]            # v17：当日机会池 d -> [opp]（含一字买不进 / 炸板）
kMarks = S["kMarks"]
excludedConsecYizi = S.get("excludedConsecYizi", [])   # v18：连续一字开盘、不属于战法标的
v23stat = S.get("v23stat", {})          # 战法 T日复盘→T+1 参与 的触发统计

# ---- 5.6.3 主包内嵌日K：已移除（占原包 30%，且线上托管无 gzip）----
# 全站可点个股日K由 klines/klines_0..7.json 分片按「需求日聚簇」完整提供，
# 窗口含断板后 5 个交易日；旧主包只有近 60 根，既冗余又会在分片缺失时把
# 可视窗口截断到错误位置。保留空字段兼容旧前端的 (REVIEW.klines||{}) 读取。
kl_main = {}

# ---- 5.7 当日连板梯队（2 板及以上，含题材；供「连板梯队」页） ----
lb = {}
for d in dates:
    m = theme_at.get(d, {})
    # 第 5 列 = 首次封板时间（连板梯队页展示「涨停时间」）
    rows = [[s["name"], s["boards"], m.get(s["code"], ""), s["code"],
             seal_time(s)]
            for s in pools[d] if s["boards"] >= 2]
    rows.sort(key=lambda x: (-x[1], x[2] or "zzz", x[3]))
    if rows:
        lb[d] = rows

# 梯队输出瘦身：只留展示需要的计数字段（个股明细见 lb / themeDetail，避免 data.js 过大）
ladders_out = {d: {nm: {"count": e["count"], "boards": e["boards"],
                       "ladder": e["ladder"], "maxB": e["maxB"]}
                   for nm, e in v.items()}
               for d, v in ladders.items()}

# ---- 5.8 复盘决策页数据（v38 简化版，v41 扩展）----
# 只统计三件事：
#   ① 今天市场阶段；
#   ② 上一个空间龙头断板后今天的表现（断板大亏＝跌停 / 连续杀跌 → 提示风险）；
#   ③ 有赚钱效应且有 3 板龙头的题材梯队：连板股给涨停时间 + 涨停形态
#      （一字 / T字 / 换手），首板只报数量；昨日次高板断板股给今日涨跌幅。
# v41 追加（用户复盘方法论）：
#   ④ nextday：昨日涨停股今日分组表现（晋级 / 红盘 / 均值 / 最差），
#      识别持续赚钱效应与人气板块；
#   ⑤ pools：三大选股池（盘前关注池 / 当日涨停备选池 / 活跃股池）。
def _up_shape(code, d, name=""):
    """涨停形态（v43 用户口径，六分类；非涨停返回空串）：

      一字  —— 涨停开盘且全天未开板（最低价 = 涨停价）；
      赚T   —— 涨停开盘、振幅 < 8%（T 字板，盘中开板又封回）；
      地天  —— 跌停开盘、涨停收盘（地天板），优先级最高；
      普通  —— 振幅 8%~12%；
      长腿  —— 振幅大于 12%（大长腿）；
      赚钱  —— 高开涨停（开盘高于昨收、振幅 < 8%）；
      低开  —— 低开涨停且振幅 < 8%（用户口径未单列，保留中性标注避免误判）。

    振幅 = (最高价 − 最低价) / 昨收；价格全部用不复权日K，涨停/跌停价按
    主板 10% / 创业板科创板 20% / 北交所 30% / ST 5% 四舍五入到分。"""
    b = _dbar(code, d)
    pv = _prev_close(code, d)
    if not b or len(b) < 5 or not pv:
        return ""
    lim = _lim_of(code, name)
    up_lim = round(pv * (1 + lim), 2)
    dn_lim = round(pv * (1 - lim), 2)
    if b[2] < up_lim - .011:                     # 收盘不在涨停价 → 不是涨停股
        return ""
    amp = (b[4] - b[3]) / pv * 100               # 振幅（相对昨收）
    if b[1] <= dn_lim + .011:
        return "地天"
    if b[3] >= up_lim - .011:                    # 最低价即涨停价：全天一字未开板
        return "一字"
    if b[1] >= up_lim - .011:                    # 涨停开盘、盘中开板
        return "赚T" if amp < 8 else ("普通" if amp <= 12 else "长腿")
    if amp > 12:
        return "长腿"
    if amp >= 8:
        return "普通"
    return "赚钱" if b[1] > pv else "低开"


# 梯队浮层的个股涨停形态：ladderFull 生成于本段之前，这里统一回填（减少前端重复计算）
_n_shape = 0
for _sd, _sthm in ladder_full.items():
    for _sarr in _sthm.values():
        for _sx in _sarr:
            _sx["s"] = _up_shape(_sx["code"], _sd, _sx.get("name") or "")
            if _sx["s"]:
                _n_shape += 1
print("梯队个股涨停形态回填：%d 个股票日（一字/赚T/地天/普通/长腿/赚钱/低开）" % _n_shape)

def _big_loss(entries, end_d):
    """断板后大亏判定（上一空间龙头批次）：断板日大长阴 / 跌停 / 连续两日跌≥6%。"""
    reasons, seen = [], set()
    for code, nm, bd in entries:
        rec = KBARS.get(str(code)) or {}
        lim = _lim_of(code, nm)
        bb, pv = rec.get(bd), _prev_close(code, bd)
        if bb and pv:
            chg = bb[2] / pv - 1
            # 大长阴：收盘跌幅 ≥7%，或收阴且实体 ≥5%（须当日收跌——涨停开盘
            # 回落但现价仍上涨不算大亏）
            if chg <= -.07 or (chg < 0 and bb[2] < bb[1] and (bb[1] - bb[2]) / pv >= .05):
                reasons.append("%s 断板日大长阴（%+.1f%%）" % (nm, chg * 100))
        i0, i1 = DIDX.get(bd), DIDX.get(end_d)
        if i0 is None or i1 is None:
            continue
        consec = 0
        for j in range(i0 + 1, i1 + 1):
            dd = dates[j]
            b, p = rec.get(dd), _prev_close(code, dd)
            if not b or not p:
                continue
            chg = b[2] / p - 1
            if chg <= -(lim - .006):
                reasons.append("%s %s 跌停（%+.1f%%）" % (nm, dd, chg * 100))
            consec = consec + 1 if chg <= -.06 else 0
            if consec >= 2:
                reasons.append("%s 连续杀跌（连两日跌≥6%%）" % nm)
                consec = -999
    out = []
    for r in reasons:
        if r not in seen:
            seen.add(r)
            out.append(r)
    return out

decision = {}
for _i, _d in enumerate(dates):
    _pd = dates[_i - 1] if _i > 0 else None
    _pool_d = {s["code"] for s in (pools.get(_d) or [])}
    _dec = {"stage": next((x.get("stage") for x in days if x["date"] == _d), None)}

    # ① 空间龙头断板情况（v44 用户口径）：取「最近一次断板批次」——
    #    · 若今日空间龙头涨停晋级（今日无新断板）→ 今日仍带上一批的断板龙头，
    #      展示的是上一个空间龙头断板后的情况；
    #    · 若昨日空间龙头今日断板 → 今日记录已被改写为这一批（breakDate=今日），
    #      优先取今日，展示最新这个断板空间龙头的情况。
    #    实现：起点从 _i 开始回溯（旧版从 _i−1 起，永远拿不到今日断板批次）。
    _ev = next((days[_k] for _k in range(_i, -1, -1)
                if days[_k].get("suppressStocks")), None)
    if _ev:
        _stocks = []
        for _s in _ev["suppressStocks"]:
            _c = str(_s.get("code") or "")
            _bd = str(_s.get("breakDate") or "")
            if not _c or not _bd or _bd > _d:
                continue
            _rec = KBARS.get(_c) or {}
            _c0 = (_rec.get(_bd) or [None, None, None])[2]
            _cum = None
            if _c0 and _rec.get(_d) and _rec[_d] and _rec[_d][2]:
                _cum = round((_rec[_d][2] / _c0 - 1) * 100, 2)
            _stocks.append({"code": _c, "name": _s.get("name"),
                            "boards": _s.get("boards"), "brk": _bd,
                            "today": chg_on(_c, _d), "cum": _cum})
        _bd0 = min((str(s.get("breakDate") or _ev["date"])
                    for s in _ev["suppressStocks"]), default=_ev["date"])
        _loss = _big_loss([(s["code"], s["name"], s["brk"]) for s in _stocks], _d)
        _dec["dragon"] = {
            "ev": _ev["date"], "stocks": _stocks, "loss": _loss,
            # fresh=True：昨日空间龙头今日断板 → 展示的就是今天刚断的这批
            "fresh": _bd0 == _d,
            "brk": _bd0,
            "verdict": ("risk" if _loss else "ok") if _stocks else None,
        }

    # ② 赚钱效应题材梯队（板块有 3 板龙头）
    _themes = []
    _LF_d = ladder_full.get(_d) or {}
    _LF_pd = (ladder_full.get(_pd) or {}) if _pd else {}
    for _g in sorted(money.get(_d) or [],
                     key=lambda x: (-x.get("maxB") or 0, -x.get("count") or 0)):
        if (_g.get("maxB") or 0) < 3:
            continue
        _nm = _g["theme"]
        _full = _LF_d.get(_nm) or []
        # v43：决策页不展示首板股 → 只保留 2 板及以上（涨停形态由 _up_shape 统一给出）
        _big = [{"code": x["code"], "name": x["name"], "boards": x["boards"],
                 "t": x.get("t") or "",
                 "shape": x.get("s") or _up_shape(x["code"], _d, x.get("name") or "")}
                for x in _full if (x.get("boards") or 1) >= 2]
        # 昨日次高板断板 → 今日涨跌幅
        _nb = []
        if _pd:
            _yl = _LF_pd.get(_nm) or []
            _ylb = sorted({(x.get("boards") or 1) for x in _yl}, reverse=True)
            if len(_ylb) >= 2 and _ylb[1] >= 2:
                for _x in _yl:
                    if (_x.get("boards") or 1) != _ylb[1] or _x["code"] in _pool_d:
                        continue
                    _nb.append({"name": _x.get("name"), "code": _x["code"],
                                "b": _ylb[1], "chg": chg_on(_x["code"], _d)})
        _themes.append({"name": _nm, "count": _g.get("count"), "maxB": _g.get("maxB"),
                        "lead": _g.get("lead"), "big": _big, "nb": _nb})
    _dec["themes"] = _themes

    # ③ 昨日涨停 → 今日赚钱效应（v41 用户方法论，v42 只展示有具体题材的板块）：
    #    复盘核心 = 昨天的涨停板今天的赚钱效应——哪类涨停股次日有肉、持续有肉
    #    （今天买进的人明天能出，明天买进的人后天可以出 = 持续赚钱效应），
    #    这类股中最强的板块就是人气板块；反之昨日涨停今日闷杀的题材要小心。
    #    口径：昨日涨停池按题材分组（无题材映射的「其他」不展示）→ 每组给个股明细
    #    （今日连板高度 / 今日涨停时间 / 今日涨跌幅）+ 晋级 / 红盘 / 均值 / 最差；
    #    按持续赚钱效应排序，首位若确实有肉（晋级且均值为正，或晋级 ≥2）标人气板块。
    _nd = []
    if _pd:
        _today = {str(_s["code"]): _s for _s in (pools.get(_d) or [])}
        _m_pd = theme_at.get(_pd, {})
        _grp = {}
        for _s in (pools.get(_pd) or []):
            _nm = _m_pd.get(_s["code"])
            if not _nm:
                continue                      # 无具体题材 → 不展示（v42）
            _grp.setdefault(_nm, []).append(_s)
        for _nm, _ss in _grp.items():
            _vals, _rows = [], []
            for _s in _ss:
                _c = str(_s["code"])
                _now = _today.get(_c)
                _chg = chg_on(_c, _d)
                if _chg is not None:
                    _vals.append(_chg)
                _bd = (_now.get("boards") if _now else _s.get("boards")) or 1
                _t = seal_time(_now) if _now else seal_time(_s)
                _rows.append([_s["name"], _c, _bd, _t, _chg])
            _rows.sort(key=lambda _r: (-_r[2], _r[3] or "99:99", _r[1]))
            _gain = [_r[0] for _r in _rows if _r[1] in _today]
            # v43：决策页不展示首板股 → 个股明细只留 2 板及以上（组内统计仍按全部昨日涨停股）
            _rows2 = [_r for _r in _rows if _r[2] >= 2]
            _nd.append({"name": _nm, "n": len(_rows), "lim": len(_gain),
                        "gain": _gain[:6], "stocks": _rows2, "n2": len(_rows2),
                        "up": sum(1 for _v in _vals if _v > 0),
                        "avg": round(sum(_vals) / len(_vals), 2) if _vals else None,
                        "min": round(min(_vals), 2) if _vals else None})
        _nd.sort(key=lambda _r: (-_r["lim"],
                                 -(_r["up"] / _r["n"] if _r["n"] else 0),
                                 -(_r["avg"] if _r["avg"] is not None else -99)))
        if _nd and ((_nd[0]["lim"] >= 1 and (_nd[0]["avg"] or 0) > 0)
                    or _nd[0]["lim"] >= 2):
            _nd[0]["hot"] = 1
    _dec["nextday"] = _nd

    # ④ 活跃股池（v42 用户口径，近 120 个交易日生成，历史日期不生成）：
    #    条件：近 10 个交易日涨停 ≥5 次；近两个交易日有过跌停的剔除；
    #    按题材分组排列显示（组内按涨停次数、最高板排序）。
    #    v42 已删除：盘前关注池、当日涨停备选池。
    if _i >= len(dates) - 120:
        _win = dates[max(0, _i - 9):_i + 1]
        _cnt = {}
        for _wd in _win:
            for _s in (pools.get(_wd) or []):
                _e = _cnt.setdefault(str(_s["code"]),
                                     {"name": _s["name"], "t": 0, "b": 0})
                _e["t"] += 1
                _e["b"] = max(_e["b"], _s.get("boards") or 1)
        # 近两日跌停 → 剔除
        _down2 = set()
        for _xd in dates[max(0, _i - 1):_i + 1]:
            for _c in _cnt:
                _b, _pv = _dbar(_c, _xd), _prev_close(_c, _xd)
                if not _b or not _pv:
                    continue
                if _b[2] / _pv - 1 <= -(_lim_of(_c, _cnt[_c]["name"]) - .006):
                    _down2.add(_c)
        _grp = {}
        _m_d = theme_at.get(_d, {})
        for _c, _e in _cnt.items():
            if _e["t"] < 5 or _c in _down2 or _e["b"] < 2:
                continue                      # v43：不展示只有首板的个股
            _nm = _m_d.get(_c) or carry.get(_c, "")
            if not _nm:
                continue
            _grp.setdefault(_nm, []).append([_e["name"], _c, _e["t"], _e["b"]])
        _active = []
        for _nm, _arr in _grp.items():
            _arr.sort(key=lambda _r: (-_r[2], -_r[3], _r[1]))
            _active.append({"theme": _nm, "stocks": _arr})
        _active.sort(key=lambda _g: (-len(_g["stocks"]),
                                     -max(_x[2] for _x in _g["stocks"]),
                                     _g["theme"]))
        _dec["pools"] = {"active": _active, "down2": sorted(_down2),
                         "rule": "近10个交易日涨停≥5次 · 近两日跌停剔除 · 按题材分组"}

    decision[_d] = _dec

# ---- 6. 组装输出 ----
data = {
    "updated": datetime.datetime.now().strftime("%Y-%m-%d %H:%M"),
    "today": dates[-1],             # 始终等于最新交易日（原值可能滞后一天）
    "dates": dates,
    "stages": days,
    "themeDates": theme_dates,
    "themeDetail": theme_detail,
    "ladders": ladders_out,          # 日期 -> 题材 -> {count,boards,ladder,maxB}（明细表/赚钱效应统一口径）
    "ladderFull": ladder_full,       # 日期 -> 题材 -> 当日梯队完整涨停个股（题材卡点梯队浮层）
    "stockTheme": carry,            # 个股 -> 最近一次归属题材（刷新时补当天题材用）
    "lb": lb,                       # 日期 -> 当日连板股（2板+，含题材）
    "themeSource": {d: theme_source.get(d, "tgb") for d in theme_dates},
    "money": money,                 # 赚钱效应：板块当日有 ≥3 板个股再涨停的题材
    "moneyDays": dates,             # 全期均有本地行情口径（区分"有行情"与"当日无达标题材"）
    "review": review,               # 三战法逐笔（前端按自定义时间段筛选）
    "chances": chances,            # v17：当日机会池（全量符合买点候选，含一字/炸板）
    "reviewMeta": reviewMeta,      # 各战法买入 / 卖出逻辑说明
    "boardEarn": BOARD_EARN_SLIM,  # v29：板块赚钱效应验证（题材日 -> 题材 -> 明细）
    "kMarks": kMarks,              # K线骷髅头标记：1=跌停，2=天地板 / 炸板大跌
    "excludedConsecYizi": excludedConsecYizi,  # v18：连续一字开盘剔除清单（d, code）
    "v23stat": v23stat,            # 战法触发统计（供校验与口径说明）
    "stockBreak": stock_break,      # 个股连板周期 -> 断板后 5 个交易日（日K窗口锚点）
    "decision": decision,           # v38：复盘决策页（市场阶段 / 空间龙头断板表现 / 赚钱效应题材梯队；
                                    #      v41 追加：nextday 昨日涨停今日赚钱效应 + pools 三大选股池）
    "klines": kl_main,              # 兼容字段（空）：日K一律走 klines_N.json 分片
    "tgb": D.get("tgb", ""),
    "spaceSeries": space_series,
    "recentPools": {d: [{"code": s["code"], "name": s["name"],
                         "boards": s["boards"], "ay": 1 if fd.is_yizi(s) else 0}
                        for s in pools[d]] for d in dates[-12:]},
    "supKlines": sup_kl,
}
# 当日收盘涨停池（真实涨停、含连板/题材/一字标记）：浏览器直连代理失败时，
# 「刷新」用它本地兜底重算，不会卡在「拉取中」也不会用盘中快照算错空间
_yz_all = fd.yizi_map(pools)
_ti, _td = len(dates) - 1, dates[-1]
data["todayStocks"] = [
    {"code": s["code"], "name": s["name"], "boards": s["boards"],
     "theme": (theme_at.get(_td) or {}).get(s["code"], ""),
     "ay": 1 if (not s.get("new") and
                 fd.all_yizi_run(s["code"], s["boards"], _ti, dates, _yz_all)) else 0,
     "chg": s.get("chg"), "reason": s.get("reason", "")}
    for s in pools[_td]]
import datajs
_JS = json.dumps(data, ensure_ascii=False, separators=(",", ":"))
# datajs.write_data 同时写 data.json / data.js / version.js（指纹唯一来源）
print("data.js 写出：" + datajs.write_data(_JS, OUT))
print("题材日 %d 天；周期线 %d 条 -> data.js / data.json"
      % (len(theme_dates), len(space_series)))
print("完成")
