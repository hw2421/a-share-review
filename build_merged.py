# -*- coding: utf-8 -*-
"""合并 2025（Excel 湖南人复盘 OCR）与 2026（同花顺接口）数据 -> data.json / data.js
用法：python3 build_merged.py   （需先跑 fetch_data.py 与 excel_ingest.py）"""
import json
import os
import sys
import re
import datetime
import bisect

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import fetch_data as fd

OUT = os.path.dirname(os.path.abspath(__file__))
CUT = "20260101"          # 2025/2026 分界

D = json.load(open(os.path.join(OUT, "data.json"), encoding="utf-8"))
ec = json.load(open(os.path.join(OUT, ".excel_cache.json"), encoding="utf-8"))
pool_cache = fd.load_pool_cache()

# ---- 1. 合并涨停池 ----
# v59 内存瘦身：data.json 全量（~1.5GB）整轮持有是 OOM 主因。提前抽出后段
# 仅需的 supKlines / tgb / 文章快照兜底三块，随后立即释放，给 KBARS（~3.5GB）腾内存。
_D_SUPKL = D.get("supKlines") or {}
_D_TGB = D.get("tgb", "")
_D_PREV_TD = None          # 仅 .theme_articles.json 缺失时的兜底快照源
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
    # v52：共用 new_stock_start —— 历史早期(<2021)日K缺失时，用复盘图「新股/次新」
    # 涨停原因兜底判次新（否则 2017 年新股连板会被误当市场空间龙头）。
    _ipo = fd.new_stock_start(pools, dates, _kc)
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
    for d, ts in (D.get("themeDetail") or {}).items():
        keep = [t for t in (ts or []) if t.get("src") not in ("pool", "ztpool")]
        if keep:
            article_cache[d] = keep
    with open(ART_F, "w", encoding="utf-8") as f:
        json.dump(article_cache, f, ensure_ascii=False)
    print("固定文章输入快照 .theme_articles.json：%d 天" % len(article_cache))
del D                       # v59：data.json 立即释放（后段只用 _D_SUPKL/_D_TGB）
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

# ---- 3.1b v94 湖南人题材卡「原始个股归类」快照（补充全局题材库用）----
# 上面的清洗规则「个股必须当日真实涨停才计入题材」会剔除一批个股：
#   · 午间版/昨日复盘残留的个股（当日未涨停）
#   · OCR 把涨停时间识别错位、池里对不上代码的个股
# 这些个股在湖南人复盘题材卡里**明明有题材归类**（用户口径：所有数据都有、
# 不可能缺失），只因当日未涨停被删，导致全局题材库 stockTheme 缺记录，前端
# themeOf() 三级反查返回 null → 显示「无题材」。这里在清洗前先存全量快照，
# 供 3.2 段补全 stockTheme（仅作兜底归类，不改 theme_at / 题材炒作图门槛）。
import theme_alias as _TA_v94
# 关键：theme_detail 在 3.1 段已被文章快照 / 补抓逐日「覆盖」（theme_detail[d]=ts），
# 覆盖日会丢掉 Excel OCR 的原始个股清单；必须直接从三个原始源叠加收集，
# 否则被覆盖日的个股归类会永久缺失（用户口径：所有数据都有、不可能缺失）。
_RAW_CARD = {}          # code -> (最新日期, 题材名)，题材名已归一别名
_FAKE_V94 = {"公告", "其他", "其他个股", "新股", "次新股", "其他概念",
             "其他板块", "其他热点", "业绩", "业绩增长", "回购", "增持", "重组"}
_SKIP_V94 = fd.NON_THEME | _FAKE_V94
def _snapshot_card(_src):
    for _d0, _ts0 in _src.items():
        for _t0 in (_ts0 or []):
            _nm0 = _TA_v94.ALIAS.get(_t0.get("name", ""), _t0.get("name", ""))
            if _nm0 in _SKIP_V94:
                continue
            for _s0 in (_t0.get("stocks") or []):
                _c0 = str(_s0.get("code") or "").strip()
                if not _c0:
                    continue
                _prev = _RAW_CARD.get(_c0)
                if _prev is None or _d0 > _prev[0]:
                    _RAW_CARD[_c0] = (_d0, _nm0)
_snapshot_card(ec.get("themes", {}))                 # Excel OCR 复盘长图
_snapshot_card(article_cache)                        # 淘股吧/历史文章快照
if os.path.exists(bf_f):                             # 微博/移动版补抓（最高优先级）
    try:
        _snapshot_card({d: (o or {}).get("themes") or [] for d, o in
                        json.load(open(bf_f, encoding="utf-8")).items()})
    except Exception:
        pass
print("v94 湖南人题材卡原始归类快照：%d 只个股" % len(_RAW_CARD))

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
    ("CPO", "光通信"), ("光芯片", "光通信"), ("MPO", "光通信"), ("硅光", "光通信"),
    ("人工智能", "AI应用"), ("AI应用", "AI应用"), ("AI", "AI应用"),
    ("数据要素", "AI应用"), ("具身", "机器人"), ("人形机器人", "机器人"),
    ("钠电池", "锂电池"), ("固态电池", "固态电池"),
    ("黄金", "黄金"), ("有色金属", "有色金属"), ("有色", "有色金属"),
    ("物流", "物流"), ("航运", "航运"), ("钢铁", "钢铁"), ("铁矿", "钢铁"),
    ("化工", "化工"), ("聚乳酸", "化工"), ("可降解", "化工"), ("乳酸", "化工"),
    ("农业", "农业"), ("养殖", "农业"), ("生猪", "农业"), ("猪", "农业"),
    ("石油", "石油"), ("油气", "石油"), ("天然气", "天然气"),
    ("全钢巨胎", "汽车零部件"), ("汽车", "汽车产业链"),
    ("传媒", "传媒"), ("出版", "传媒"), ("影视", "短剧"), ("微短剧", "短剧"),
    ("卫星", "商业航天"), ("军工", "商业航天"),
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

# ---- 3.2b v94 补全全局题材库 stockTheme（carry）----
# 用户口径：湖南人复盘里所有出现过的个股都有题材归类，不可能缺失。
# 只补 carry（个股→最近题材，前端 themeOf 第三级 / 全局兜底），
# 不改 theme_at（当日严格映射）、不改 themeDetail / 题材炒作图入链门槛。
# ① 原始题材卡快照补全（被「当日未涨停」清洗规则误删的个股）
_n_card = 0
for _c0, (_d0, _nm0) in sorted(_RAW_CARD.items(), key=lambda x: x[1][0]):
    if _c0 not in carry:
        carry[_c0] = _nm0
        _n_card += 1
# ② 涨停原因归一补全（当日有涨停原因但未入题材卡的个股，取最近一次可归类原因）
_n_rsn = 0
for _d0 in dates:
    for _s0 in pools.get(_d0, []):
        _c0 = str(_s0.get("code") or "").strip()
        if not _c0 or _c0 in carry:
            continue
        _nm0 = reason_theme(_s0)
        if _nm0:
            carry[_c0] = _nm0
            _n_rsn += 1
print("v94 补全全局题材库 stockTheme：题材卡 %d 只 + 涨停原因 %d 只 = %d 只"
      % (_n_card, _n_rsn, _n_card + _n_rsn))

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
    sup_kl = _D_SUPKL
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
    """合并三份日K缓存。同一「股票日」只能有一条（v45 修正）：

    旧版按文件顺序直接覆盖，`sup_kline_cache`（压制股日K，盘中抓取）里的
    盘中快照会盖掉 `stock_kline_cache` 的收盘价——例如 20261119 华东重机
    收盘 7.67（+4.07%），盘中快照 7.56（+2.58%）覆盖后，决策页「断板当日」
    与赚钱效应均值全都算错。改为：同一交易日取成交量更大的一条
    （收盘完整日的成交量必然 ≥ 盘中快照），成交量相同（除权口径差异）时
    以最后加载的 .stock_kline_cache 为准。
    """
    kb, fixed = {}, 0
    # v55 根治缓存漂移：把 klines/ 分片也纳入合并（与 verify_space 同源）。
    # 后台定时刷新会重写 .stock_kline_cache（新浪近1800根）而丢弃 2017-2020 历史 bar，
    # 分片「只增不减」保有全量历史；纳入分片后 build(缓存) 与 verify(分片) 永远一致。
    import glob as _glob
    sources = [os.path.join(OUT, fn) for fn in
               (".kline_cache.json", ".sup_kline_cache.json", ".stock_kline_cache.json")]
    sources += sorted(_glob.glob(os.path.join(OUT, "klines", "klines_*.json")))
    for p in sources:                                 # 越靠后越权威
        if not os.path.exists(p):
            continue
        try:
            for c, bars in json.load(open(p, encoding="utf-8")).items():
                if not isinstance(bars, list):
                    continue
                rec = kb.setdefault(str(c), {})
                for b in bars:
                    if not b or len(b) < 5:
                        continue
                    # v63 防线：close<=0 的非法 bar（同花顺 v6 对除权股返回归一化负值）
                    # 一律不入库——build 与 verify（分片加载）同规则，保证两边永远一致。
                    if not isinstance(b[2], (int, float)) or b[2] <= 0:
                        continue
                    old = rec.get(b[0])
                    if old is not None and (b[5] if len(b) > 5 else 0) < (old[5] if len(old) > 5 else 0):
                        fixed += 1
                        continue
                    rec[b[0]] = b
        except Exception as e:
            print("日K缓存读取失败 %s：%s" % (p, e))
    if fixed:
        print("日K缓存去重：丢弃 %d 条盘中快照/漂移（保留收盘完整日）" % fixed)
    return kb

KBARS = _load_kbars()
CLOSE = {c: {d: b[2] for d, b in rec.items()} for c, rec in KBARS.items()}
DIDX = {d: i for i, d in enumerate(dates)}
# 每只个股自己的日K日期表：涨跌幅一律按「该股上一根日K」算，而不是按全站交易日历。
# 全站 dates 由涨停池推出，个别交易日（如 2022-05-05/06 涨停池为空）会缺席，
# 按日历回退会把「跨 3 个交易日」的涨幅当成单日涨幅（湖南发展 2022-05-09 曾算出 +27.4%）。
CODE_DATES = {c: sorted(rec) for c, rec in KBARS.items()}

def _prev_bar(code, d):
    """交易日 d 之前最近一根有完整 OHLC 的日K（停牌则顺延到停牌前最后一根）。"""
    code = str(code)
    ds = CODE_DATES.get(code)
    if not ds:
        return None
    rec = KBARS.get(code) or {}
    k = bisect.bisect_left(ds, d)
    for j in range(k - 1, -1, -1):
        b = rec.get(ds[j])
        if b and len(b) >= 5 and b[2]:
            return b
    return None

def chg_on(code, d):
    """个股在交易日 d 的涨跌幅（不复权收盘价环比上一根日K），无数据返回 None。"""
    b = (KBARS.get(str(code)) or {}).get(d)
    if not b or len(b) < 3 or not b[2]:
        return None
    pb = _prev_bar(code, d)
    if not pb or not pb[2]:
        return None
    return round((b[2] / pb[2] - 1) * 100, 2)

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
    """上一根日K的收盘价（停牌顺延到停牌前最后一天；按个股日K而非全站日历）。"""
    pb = _prev_bar(code, d)
    return pb[2] if pb else None

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

def _up_shape(code, d, name=""):
    """涨停形态（v110 用户口径，六分类；非涨停返回空串）：

      一字  —— 涨停开盘且全天未开板（最低价 = 涨停价），特别标记；
      地天  —— 跌停开盘、涨停收盘（地天板），优先级最高；
      赚钱  —— 有缺口（今日最低价 > 昨日涨停价＝昨收）的换手涨停股。
               v110「赚T」并入「赚钱」统一称赚钱：原赚T（涨停开盘振幅<8%）与
               原赚钱（高开涨停）本质都是「今低>昨涨停价」的缺口股；好当家这类
               高开涨停且今低>昨涨停价（振幅8%~12%原判普通）也归赚钱。
               ——缺口是赚钱效应的硬性条件，故有缺口的换手涨停一律标「赚钱」。
      普通  —— 无缺口、振幅 8%~12%；
      长腿  —— 无缺口、振幅大于 12%（大长腿）；
      低开  —— 低开涨停且振幅 < 8%、无缺口（保留中性标注避免误判）。

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
    has_gap = b[3] > pv - .004                   # v110 缺口口径统一：今低 > 昨涨停价(=昨收)
    if b[1] <= dn_lim + .011:
        return "地天"
    if b[3] >= up_lim - .004:                    # 最低价即涨停价：全天一字未开板（特别标记）
        return "一字"
    if has_gap:                                  # v110：有缺口的换手涨停 → 赚钱（含原赚T+原赚钱+好当家类）
        return "赚钱"
    if amp > 12:
        return "长腿"
    if amp >= 8:
        return "普通"
    return "低开"


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
    "shape_of": _up_shape,          # v45：涨停形态（明细表「买入逻辑」列同源）
    "board_earn": BOARD_EARN,       # v29：三战法共同前置（板块赚钱效应验证）
})
review = S["review"]              # {method: [trade,...]}
reviewStat = S.get("reviewStat", {})   # v45：买入逻辑共性统计
reviewMeta = S["reviewMeta"]
chances = S["chances"]            # v17：当日机会池 d -> [opp]（含一字买不进 / 炸板）
kMarks = S["kMarks"]

# ---- 5.6.26 v67 连续两日换手赚钱机会个股（3板+，今日复盘页赚钱效应统计） ----
# 口径：t-2 买入 → t-1 换手连板赚钱；t-1 买入 → t 换手连板赚钱；
#       即 t-1、t 连续两日换手涨停（非一字），且 t 日板位 ≥3。跳空缺口单独标注。
import earn_streak as ES
_NAME_ALL = {s["code"]: s["name"] for d in dates for s in pools[d]}
EARN_STREAK = ES.compute(dates, pools, fd.yizi_map(pools), KBARS, theme_at,
                         NAME=_NAME_ALL, ALL_N=140)
print("v67 连续两日换手赚钱机会（3板+）：全量池 %d 笔（近5日 %d 笔）"
      % (len(EARN_STREAK["eventsAll"]),
         sum(1 for e in EARN_STREAK["eventsAll"] if e["d"] >= dates[-5])))
# ---- 5.6.27 v67 高位断板跟踪（昨日≥3板断板 → 梯队最下一行跟踪3日） ----
import brk_track as BT
BRK_TRACK, _n_brk = BT.compute(dates, pools, KBARS, NAME=_NAME_ALL,
                               THEME=theme_at, CARRY=carry)
print("v67 高位断板跟踪：共 %d 个跟踪股票日（断板日 + 后续2日）" % _n_brk)
# ---- 5.6.27b v69 昨日梯队 → 今日盈亏传导（逐股反馈，龙头/助攻/跟风分层） ----
import relay_fb as RF
RELAY_FB, _n_rf = RF.compute(dates, pools, KBARS, ladder_full, NAME=_NAME_ALL,
                             THEME=theme_at)
print("v69 昨日梯队今日反馈：%d 个展示日 / %d 个题材（逐股涨幅+晋级）"
      % (len(RELAY_FB["days"]), _n_rf))
# ---- 5.6.27b v70 个股当日状态：涨停首封时间 + 炸板（含首封时间） ----
# 今日复盘页要「涨停→显示涨停时间；炸板→显示炸板（取到首封时间也显示）」。
# dayZT：近 25 个交易日全量涨停股（不只是达标题材），取同花顺涨停池 first_seal。
# dayZB：同花顺「涨停炸板」池（曾涨停未封住），带首封时间与开板次数；
#        抓不到的日子由前端按日K（最高≥涨停价、收盘未封）降级判定，不显示时间。
_ZT_DAYS = dates[-25:]
DAY_ZT = {}
for _d in _ZT_DAYS:
    _m = {}
    _tm0 = theme_at.get(_d) or {}
    for _s in pools.get(_d, []):
        _c = str(_s["code"])
        _t = str(_s.get("first_seal") or _s.get("t") or "")
        # v74 扩展：[名称, 首封, 形态, 板位, 题材]——题材涨停时间表据此展示当日
        # 全量涨停股（复盘清单外个股用 carry 最近题材兜底，再无则前端归「未归类」）
        _m[_c] = [_s.get("name") or "", _t[:5] if _t[:2].isdigit() else "",
                  _up_shape(_c, _d, _s.get("name") or ""),
                  _s.get("boards") or 1,
                  _tm0.get(_c) or carry.get(_c) or ""]
    DAY_ZT[_d] = _m
DAY_ZB_RAW = fd.collect_zb(_ZT_DAYS)
DAY_ZB = {d: {c: [v.get("name") or "", v.get("t") or "", v.get("chg") or 0]
              for c, v in m.items()}
          for d, m in DAY_ZB_RAW.items()}
# v72 炸板股题材映射：{d:{code: 题材}}（仅收当日有梯队的题材，供题材涨停时间表
# 在题材内显示炸板股；映射口径与 ladderFull 一致，均取 theme_at[d]）
DAY_ZBT = {}
for _d, _m in DAY_ZB.items():
    _tm = theme_at.get(_d) or {}
    _lth = set((ladder_full.get(_d) or {}).keys())
    _z = {c: _tm[c] for c in _m if _tm.get(c) in _lth}
    if _z:
        DAY_ZBT[_d] = _z
print("v70 个股当日状态：涨停池 %d 天 / 炸板池 %d 天（炸板 %d 个股票日，题材化 %d 个股票日）"
      % (len(DAY_ZT), len(DAY_ZB), sum(len(v) for v in DAY_ZB.values()),
         sum(len(v) for v in DAY_ZBT.values())))
# ---- 5.6.27b2 v76 个股涨停形式映射 gapMap：{d:{code: 1缺口/0长腿}} ----
# v108 缺口口径统一：当日最低价 > 昨日涨停价 = 缺口，否则长腿。
# 昨涨停价＝昨收（昨日为涨停板）；涨停板昨高＝昨收＝昨涨停价，与旧口径「今低>昨高」等价（全量核对 0 差异）。
# 供今日复盘梯队个股显示涨停形式、明日候选缺口标。
def _gap_flag(code, d):
    b = (KBARS.get(str(code)) or {}).get(d)
    if not b or len(b) < 5 or not b[2]:
        return None
    pb = _prev_bar(code, d)
    if not pb or len(pb) < 5:
        return None
    return 1 if b[3] > pb[2] - .004 else 0
GAP_MAP = {}
for _d in _ZT_DAYS:
    _m = {}
    for _s in pools.get(_d, []):
        _g = _gap_flag(str(_s["code"]), _d)
        if _g is not None:
            _m[str(_s["code"])] = _g
    if _m:
        GAP_MAP[_d] = _m
print("v76 个股涨停形式映射（gapMap）：%d 天 / %d 个股票日（缺口 %d）"
      % (len(GAP_MAP), sum(len(v) for v in GAP_MAP.values()),
         sum(1 for v in GAP_MAP.values() for g in v.values() if g)))
# ---- 5.6.27d v82 明日赚钱效应候选（今日换手涨停≥2板 → 明日高开涨停晋级≥3板即兑现） ----
import next_earn as NE
NEXT_EARN = NE.compute(dates, pools, fd.yizi_map(pools), KBARS, theme_at,
                       (EARN_STREAK or {}).get("eventsAll") or [], _up_shape,
                       NAME=_NAME_ALL, ALL_N=140)
# 回填今日缺口标（与 gapMap 同口径，供候选 chip 显示形态）
for _d, _arr in NEXT_EARN["byDate"].items():
    _gm = GAP_MAP.get(_d) or {}
    for _e in _arr:
        if _e["code"] in _gm:
            _e["gap"] = _gm[_e["code"]]
print("v82 明日赚钱效应候选：%d 天 / %d 条候选（今日样本 %d 只）"
      % (len(NEXT_EARN["days"]),
         sum(len(v) for v in NEXT_EARN["byDate"].values()),
         len(NEXT_EARN["byDate"].get(dates[-1], []))))
# ---- 5.6.27c v73 历史复盘（赚钱效应股按日期/题材/高度排列 + 断板3日跟踪 +
#      空间龙头与上一个断板空间龙头叠加，3日不涨停移除） ----
import hist_review as HR
HIST_REVIEW, _n_hr = HR.compute(dates, pools, KBARS,
                                (EARN_STREAK or {}).get("eventsAll") or [],
                                days, NAME=_NAME_ALL, days_num=140, zb_days=DAY_ZB)
print("v73 历史复盘：%d 个事件日 / %d 只存活赚钱效应股"
      % (len(HIST_REVIEW["days"]), _n_hr))
# ---- 5.6.28 v55 新旧龙头交接事件序列（空间 + 题材） ----
import handover as HO
HANDOVER = HO.compute(dates, days, ladder_full, NAME=_NAME_ALL, limit=40)
print("新旧龙头交接（v55）：空间 %d 次 / 题材 %d 个题材共 %d 次"
      % (len(HANDOVER["space"]), len(HANDOVER["theme"]),
         sum(len(x) for x in HANDOVER["theme"].values())))
excludedConsecYizi = S.get("excludedConsecYizi", [])   # v18：连续一字开盘、不属于战法标的
v23stat = S.get("v23stat", {})          # 战法 T日复盘→T+1 参与 的触发统计

# ---- 5.6.3 主包内嵌日K（v65 恢复精简版）：近 60 日活跃股（全部涨停池去重）
# 每股尾部 130 根（约半年）。v55 起日K全靠 8 个 20MB 分片，点击个股要先下完
# 20MB 才出图，手机/慢网络直接表现为「点了没反应」。现在点击高频股（近期涨停
# 池 + 赚钱画像全部个股均在其中）秒开；更早历史与内嵌未覆盖股仍走分片兜底。
# 压缩后增量约 1MB（zlib 9），远小于一次 20MB 分片下载。
_codes60 = set()
for _d in dates[-60:]:
    for _s in pools.get(_d, []):
        _codes60.add(str(_s["code"]))
kl_main = {}
for _c in _codes60:
    _rec = KBARS.get(_c)
    if not _rec:
        continue
    _bars = [_rec[x] for x in sorted(_rec.keys()) if _rec[x]]
    kl_main[_c] = _bars[-130:]
print("内嵌日K（v65）：近60日活跃股 %d 只，命中 %d 只 × 尾部130根" %
      (len(_codes60), len(kl_main)))

# ---- 5.7 当日连板梯队（2 板及以上，含题材；供「连板梯队」页） ----
# v47：当日没抓到题材的连板股，沿用「昨日复盘」的题材归类。
#   theme_at[d] 严格只认当日湖南人复盘清单（v39.2 口径，战法/题材梯队不能污染），
#   清单外的连板股题材会留空。连板股是连续涨停的强势股，昨日复盘通常已有题材归类，
#   当日一旦漏抓就回退到前一交易日的 theme_at（即「昨日复盘的题材归类」）。
#   注意：只改连板梯队展示层 lb，不动 theme_at，故不影响战法逐笔/赚钱效应统计口径。
lb = {}
_n_lb_fill = 0
for _i, d in enumerate(dates):
    m = theme_at.get(d, {})
    _pd = dates[_i - 1] if _i > 0 else None
    pm = theme_at.get(_pd, {}) if _pd else {}       # 昨日复盘的题材归类
    # 第 5 列 = 首次封板时间（连板梯队页展示「涨停时间」）
    rows = []
    for s in pools[d]:
        if s["boards"] < 2:
            continue
        _th = m.get(s["code"], "")
        if not _th:                                  # 当日没抓到 → 沿用昨日复盘
            _th = pm.get(s["code"], "")
            if _th:
                _n_lb_fill += 1
        rows.append([s["name"], s["boards"], _th, s["code"], seal_time(s)])
    rows.sort(key=lambda x: (-x[1], x[2] or "zzz", x[3]))
    if rows:
        lb[d] = rows
print("连板梯队：当日漏抓题材、沿用昨日复盘归类 %d 个股票日" % _n_lb_fill)

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
# 梯队浮层的个股涨停形态：ladderFull 生成于本段之前，这里统一回填（减少前端重复计算）
_n_shape = 0
for _sd, _sthm in ladder_full.items():
    for _sarr in _sthm.values():
        for _sx in _sarr:
            _sx["s"] = _up_shape(_sx["code"], _sd, _sx.get("name") or "")
            if _sx["s"]:
                _n_shape += 1
print("梯队个股涨停形态回填：%d 个股票日（一字/赚T/地天/普通/长腿/赚钱/低开）" % _n_shape)

# 空间情绪明细表的龙头股：名称旁同样展示涨停形态缩写（v45）
_n_sf = 0
for _day in days:
    for _s in (_day.get("spaceFull") or []):
        _s["s"] = _up_shape(_s.get("code"), _day.get("date"), _s.get("name") or "")
        if _s["s"]:
            _n_sf += 1
print("空间龙头涨停形态回填：%d 个股票日" % _n_sf)

# ---- v60：赚钱效应循环仪表盘（四环节健康度 + 五段生命周期 + 负反馈起点） ----
# 放在推荐之前：推荐需要按决策日读取循环分数（低分降级提示）。
import cycle as CY
CYCLE_OUT = CY.compute(dates, pools, days, KBARS, summit=review["summit"])
CYCLE_MAP = {r["date"]: r for r in CYCLE_OUT["days"]}
print("v63 循环仪表盘：%d 天，健康度均值 %.1f；负反馈起点 %d 天（五段生命周期已取消）"
      % (len(CYCLE_OUT["days"]),
         (sum(r["score"] for r in CYCLE_OUT["days"] if r["score"] is not None) /
          max(1, sum(1 for r in CYCLE_OUT["days"] if r["score"] is not None))),
         sum(1 for r in CYCLE_OUT["days"] if r.get("neg"))))

# ---- v67：今日复盘页重建为「连续两日换手赚钱机会（3板+）」统计，
#      原 v64 赚钱效应复盘（earn_profile）与循环健康度展示撤出今日复盘页。
#      cycle 仍计算：明日交易机会推荐（next_recs）按决策日读取循环分数。
# ---- v51：明日交易机会推荐（复盘案例前一日前置条件统计 → 决策日匹配） ----
# 放在形态回填之后，确保 ladder_full 元素已带「s」形态字段，可正确排除一字（买不进）。
import next_recs as NR
NEXT_RECS = NR.compute(dates, days, ladder_full, ladders, review["summit"],
                       fd.yizi_map(pools), MONEY=money, CYCLE=CYCLE_MAP,
                       NAME={s["code"]: s["name"] for dd in dates for s in pools.get(dd, [])})
_today_recs = NEXT_RECS["byDate"].get(dates[-1], {}).get("recs", [])
print("v54 明日机会推荐：盈利画像 %d 笔盈利/%d 笔全部，决策日 %d 个，今日推荐 %d 只，今日实例 %d 条"
      % (NEXT_RECS["featureProfile"]["nProfit"], NEXT_RECS["base"]["n"],
         len(NEXT_RECS["byDate"]), len(_today_recs),
         sum(len(x["instances"]) for x in _today_recs)))

# ---- v62：B1 传导路径图数据（昨日梯队分层 → 今日传导效果） ----
# 口径：对每个「题材有连板」的题材日 (d, theme)，把前一交易日该题材梯队按板位分三层
# （龙头≥3板 / 2板助攻 / 首板跟风），统计各层今日表现：家数、可算家数、均涨（收盘 vs 昨收）、
# 红盘率、晋级家数（今日再板）；另计今日新增首板家数（跟风资金是否还在进场）。
# 回答「钱在板块内传导到第几层断了」——作战页题材卡与题材梯队浮层点「⚡传导」查看。
_didx_tx = {d: i for i, d in enumerate(dates)}

def _tx_layer(stocks, d):
    _arr = sorted(stocks, key=lambda x: -(x.get("boards") or 1))
    chgs, promo, names = [], 0, []
    for s in _arr:
        code = str(s.get("code") or "")
        b0 = s.get("boards") or 1
        if len(names) < 4:
            names.append(s.get("name") or code)
        b1 = (pool_map.get(d) or {}).get(code)
        if b1 and (b1.get("boards") or 1) > b0:
            promo += 1
        pv = _prev_close(code, d)
        bar = _dbar(code, d)
        if pv and bar and bar[2]:
            chgs.append((bar[2] / pv - 1) * 100)
    return {"n": len(_arr), "cn": len(chgs),
            "avg": round(sum(chgs) / len(chgs), 1) if chgs else None,
            "win": round(sum(1 for x in chgs if x >= 0) / len(chgs) * 100) if chgs else None,
            "pr": promo, "nm": names}

TRANSMIT = {}
for _td, _ths in ladders_out.items():
    _ti = _didx_tx.get(_td) or 0
    if _ti == 0:
        continue
    _td0 = dates[_ti - 1]
    for _tnm in _ths:
        _yst = (ladder_full.get(_td0) or {}).get(_tnm) or []
        if not _yst:
            continue
        _tdy = (ladder_full.get(_td) or {}).get(_tnm) or []
        _ycodes = {str(s.get("code")) for s in _yst}
        _new1 = sum(1 for s in _tdy
                    if (s.get("boards") or 1) == 1 and str(s.get("code")) not in _ycodes)
        _srt = sorted(_yst, key=lambda x: -(x.get("boards") or 1))
        _b_of = lambda s: (s.get("boards") or 1)
        TRANSMIT.setdefault(_td, {})[_tnm] = {
            "ladder": _ths[_tnm]["ladder"],
            "L": [_tx_layer([s for s in _srt if _b_of(s) >= 3], _td),
                  _tx_layer([s for s in _srt if _b_of(s) == 2], _td),
                  _tx_layer([s for s in _srt if _b_of(s) == 1], _td)],
            "n1": _new1,
        }
print("v62 传导路径图：%d 个题材日（昨日梯队分层→今日传导）"
      % sum(len(v) for v in TRANSMIT.values()))

# ---- v58：明日交易机会复盘（推荐个股「次日涨停买入」全量模拟回测） ----
# 口径：对复盘决策每个决策日的每只推荐股（明日机会推荐 recs），
#   次日 B=dates[i+1]：
#     · B 未涨停（未封且未盘中触板）→ 「未涨停」买不进，不计盈亏；
#     · B 一字涨停开盘封死 → 「一字买不进」；
#     · 其余 → 按涨停价买入（封住=收盘价；盘中触板未封=涨停价，炸板前已成交）。
#   卖出与山顶洞人完全同规则：次日继续涨停封住则持有；盘中破 -7% 止损；
#   未涨停尾盘收盘价卖出；一字跌停顺延；期末仍涨停按最后收盘价估值。
#   产出 nextRecsBacktest.records，前端交易复盘页按时间段筛选统计均盈亏。
STOP_PCT_NR = 0.07

def _nr_oneword(code, d, up=True):
    b, pv = _dbar(code, d), _prev_close(code, d)
    if not b or len(b) < 5 or not pv:
        return False
    lim = _lim_of(code)
    if b[1] != b[2] or b[2] != b[3] or b[3] != b[4]:
        return False
    chg = b[2] / pv - 1
    return chg >= (lim - .006) if up else chg <= -(lim - .006)

_NR_RECS = []
for _di, _d in enumerate(dates):
    for _r in (NEXT_RECS.get("byDate") or {}).get(_d, {}).get("recs", []):
        _NR_RECS.append((_di, _d, _r))

_NR_OUT = []
for _k, (_di, _d, _r) in enumerate(_NR_RECS):
    if _di + 1 >= len(dates):
        continue
    code, B = str(_r["code"]), dates[_di + 1]
    b, pv = _dbar(code, B), _prev_close(code, B)
    if not b or len(b) < 5 or not pv:
        continue
    lim = _lim_of(code)
    sealed = b[2] / pv - 1 >= lim - .006
    touch = b[4] / pv - 1 >= lim - .006
    rec = {
        "d": _d, "B": B, "code": code,
        "name": _r.get("name") or _NAME_ALL.get(code, code),
        "theme": _r.get("theme") or "", "boards": _r.get("boards") or 0,
        "level": _r.get("level") or "", "win": _r.get("win"),
        "status": "", "buy": None, "sell": None, "sellD": "",
        "hold": 0, "reason": "", "pnl": None,
        # v62 作战页结算屏：买入日收盘涨幅（vs 昨收，与是否涨停无关，纯展示）
        "chgB": round((b[2] / pv - 1) * 100, 2),
    }
    if not (sealed or touch):
        rec["status"] = "未涨停"
    elif _nr_oneword(code, B, True):
        rec["status"] = "一字买不进"
    else:
        buy = b[2] if sealed else round(pv * (1 + lim), 2)
        rec.update({"status": "成交", "buy": round(buy, 2)})
        # ---- 卖出模拟（与 strategies.simulate_exit 同规则） ----
        _ki = _di + 1
        deferred, out = 0, None
        for j in range(_ki + 1, len(dates)):
            t = dates[j]
            tb, tpv = _dbar(code, t), _prev_close(code, t)
            if not tb or len(tb) < 5 or not tpv:
                continue
            if _nr_oneword(code, t, False):
                deferred += 1
                continue
            tag = ("（一字跌停顺延%d日）" % deferred) if deferred else ""
            if tb[3] and tb[3] <= tpv * (1 - STOP_PCT_NR):
                out = (round(tpv * (1 - STOP_PCT_NR), 2), t, "盘中-7%止损" + tag, j - _ki)
                break
            if code in pool_map.get(t, {}):
                continue                      # 次日涨停（封住）→ 持仓
            out = (tb[2], t, "尾盘未涨停" + tag, j - _ki)
            break
        if out is None:
            _kb = KBARS.get(code) or {}
            tail = [(x, _kb[x][2]) for x in sorted(_kb)
                    if x > B and isinstance(_kb.get(x), list) and len(_kb[x]) >= 3 and _kb[x][2]]
            if not tail:
                rec["status"] = "无卖出数据"
            else:
                out = (tail[-1][1], tail[-1][0], "期末仍涨停·按末收盘", len(tail))
        if out:
            sell, sell_d, reason, hold = out
            rec.update({"sell": sell, "sellD": sell_d, "hold": hold,
                        "reason": reason,
                        "pnl": round((sell - buy) / buy * 100, 2)})
    _NR_OUT.append(rec)
    if _k % 5000 == 0 and _k:
        print("  机会回测进度 %d/%d" % (_k, len(_NR_RECS)))

NR_BT = {
    "records": _NR_OUT,
    "rule": ("统计口径：对复盘决策「明日交易机会推荐」的全部推荐个股，模拟次日涨停买入"
             "（封住=收盘价买入、盘中触板=涨停价买入、一字买不进、未涨停不计），"
             "卖出与山顶洞人战法完全同规则：继续涨停封住则持有、盘中破-7%止损、"
             "未涨停尾盘收盘卖出、一字跌停顺延、期末仍涨停按末收盘估值。"
             "盈亏=(卖出价−买入价)/买入价。支持自定义起止时间段筛选。"),
}
print("v58 明日交易机会复盘：推荐 %d 笔 → 成交 %d / 一字买不进 %d / 未涨停 %d；"
      "成交均盈亏 %s%%"
      % (len(_NR_OUT),
         sum(1 for x in _NR_OUT if x["status"] == "成交"),
         sum(1 for x in _NR_OUT if x["status"] == "一字买不进"),
         sum(1 for x in _NR_OUT if x["status"] == "未涨停"),
         (round(sum(x["pnl"] for x in _NR_OUT if x["pnl"] is not None) /
                max(1, sum(1 for x in _NR_OUT if x["pnl"] is not None)), 2)
          if any(x["pnl"] is not None for x in _NR_OUT) else "—")))


def _mood(entries, d, fresh):
    """空间龙头断板后的情绪三档（v45 用户口径）：

      risk   风险提示 —— 断板日大跌 / 跌停 / 连续杀跌，且今日仍在跌
      normal 风险一般 —— 今日止跌收红（fresh：断板当日自己收红）
      hot    情绪高涨 —— 今日反包（收盘 > 昨日最高）或较昨日创新高（最高 > 昨日最高且收红）

    返回 (verdict, note, per_stock)。多只龙头取「最弱」一档（风险优先提示）。
    """
    per, lv = [], []
    for code, nm, bd in entries:
        b, pv = _dbar(code, d), _prev_close(code, d)
        if not b or not pv:
            continue
        chg = b[2] / pv - 1
        lim = _lim_of(code, nm)
        pb = _prev_bar(code, d)
        kind, txt = "normal", ""
        if fresh:                                  # 断板当日就是今天：没有「昨日反包」可比
            if chg <= -(lim - .006) or chg <= -.07:
                kind, txt = "risk", "断板当日大跌"
            elif chg >= 0:
                kind, txt = "normal", "断板当日仍收红"
            else:
                kind, txt = "risk", "断板当日收绿"
        elif chg >= 0 and pb:
            if b[2] > pb[4]:
                kind, txt = "hot", "反包（收盘 > 昨日最高）"
            elif b[4] > pb[4] and b[2] > pb[2]:
                kind, txt = "hot", "较昨日创新高"
            else:
                kind, txt = "normal", "止跌收红"
        elif chg >= 0:
            kind, txt = "normal", "止跌收红"
        else:
            kind, txt = "risk", "今日继续收绿"
        per.append({"code": code, "name": nm, "chg": round(chg * 100, 2),
                    "mood": kind, "txt": txt})
        lv.append(kind)
    # 风险优先
    verdict = ("risk" if "risk" in lv else
               ("normal" if "normal" in lv else ("hot" if lv else None)))
    return verdict, per


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
        # v45：三档情绪判定（风险提示 / 风险一般 / 情绪高涨），逐股给出触发依据
        _vd, _moods = _mood([(s["code"], s["name"], s["brk"]) for s in _stocks],
                            _d, _bd0 == _d)
        for _s in _stocks:
            _s["mood"] = next((m for m in _moods if m["code"] == _s["code"]), None)
        _dec["dragon"] = {
            "ev": _ev["date"], "stocks": _stocks, "loss": _loss,
            # fresh=True：昨日空间龙头今日断板 → 展示的就是今天刚断的这批
            "fresh": _bd0 == _d,
            "brk": _bd0,
            "verdict": _vd if _stocks else None,
            "moods": _moods,
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
                        # v45：首板股数量（用户要求梯队里列明首板多少个）
                        "b1": sum(1 for x in _full if (x.get("boards") or 1) == 1),
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
    "earnStreak": EARN_STREAK,      # v67 赚钱效应统计：连续两日换手赚钱机会个股（3板+）
    "nextEarn": NEXT_EARN,          # v82 明日赚钱效应候选：今日换手涨停≥2板→明日高开晋级≥3板即兑现
    "brkTrack": BRK_TRACK,          # v67 高位断板跟踪：昨日≥3板断板 → 跟踪3日涨跌幅
    "relayFb": RELAY_FB,            # v69 昨日梯队 → 今日盈亏传导（逐股涨幅 + 是否晋级，按龙头/助攻/跟风分层）
    "dayZT": DAY_ZT,                # v74 近25日全量涨停股：{d:{code:[名称,首封,形态,板位,题材兜底]}}
    "dayZB": DAY_ZB,                # v70 近25日炸板池：{d:{code:[name,首封HH:MM,当日涨幅]}}
    "zbTheme": DAY_ZBT,             # v72 炸板股题材映射：{d:{code:题材}}（仅当日梯队题材）
    "gapMap": GAP_MAP,              # v76 个股涨停形式：{d:{code:1缺口/0长腿}}
    "nameMap": _NAME_ALL,           # v78 个股搜索：{code: 名称} 全量映射
    "histReview": HIST_REVIEW,      # v73 历史复盘：赚钱效应股断板跟踪 + 空间龙头叠加
    "lb": lb,                       # 日期 -> 当日连板股（2板+，含题材）
    "themeSource": {d: theme_source.get(d, "tgb") for d in theme_dates},
    "money": money,                 # 赚钱效应：板块当日有 ≥3 板个股再涨停的题材
    "moneyDays": dates,             # 全期均有本地行情口径（区分"有行情"与"当日无达标题材"）
    "review": review,               # 三战法逐笔（前端按自定义时间段筛选）
    "reviewStat": reviewStat,       # v45：买入逻辑共性（市场阶段 / 题材 / 形态 / 涨停时机）
    "handover": HANDOVER,          # v55：新旧龙头交接事件序列（空间 + 题材，含同身位备选）
    "nextRecs": NEXT_RECS,         # v51：明日交易机会推荐（复盘前一日前置条件画像 + 决策日匹配）
    "cycle": CYCLE_OUT,            # v60：赚钱效应循环仪表盘（健康度/生命周期/负反馈/交叉统计）
    "nextRecsBacktest": NR_BT,     # v58：明日交易机会复盘（推荐个股次日涨停买入模拟逐笔）
    "transmit": TRANSMIT,          # v62：B1 传导路径图（昨日梯队分层→今日传导效果）
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
    "tgb": _D_TGB,
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
