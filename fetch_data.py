# -*- coding: utf-8 -*-
"""A股复盘工具数据层 v3
1) 市场情绪：空间压制 + 空间高度 + 四阶段（节点/补涨/突破/主升）
2) 题材炒作图：抓取淘股吧"湖南人"(tgb.cn/blog/444409)每日复盘表（图片OCR），
   筛选"有连板且涨停>=5只"的题材，缺失日由同花顺涨停原因补充
3) 赚钱效应（文档口径）：龙头断板次日反馈链，反包=正反馈 / 补跌=负反馈
4) 四信号：龙头共振 / 一字助攻 / 一叶知秋 / 空间压缩到位
5) 龙头身份：空间突破 / 传承 / 隔位传承 / 卡位
输出: data.js / data.json
"""
import json
import os
import re
import subprocess
import sys
import time
from datetime import datetime, date as _date, timedelta

# ------------------------------------------------------------------ 常量
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36")
THS_REFERER = "https://data.10jqka.com.cn/datacenterph/limitup/limtupInfo.html"
THS_FIELDS = ("199112,10,9001,330323,330324,330325,9002,330329,"
              "133971,133970,1968584,3475914,9003,9004")
THS_INTERVAL = 2.0
THS_RETRY = 3

TGB_REFERER = "https://www.tgb.cn/"
TGB_BLOG = "https://www.tgb.cn/blog/444409"

OUT_DIR = os.path.dirname(os.path.abspath(__file__))
OCR_CACHE = os.path.join(OUT_DIR, ".ocr_cache.json")
POOL_CACHE = os.path.join(OUT_DIR, ".pool_cache.json")

STAGE_ORDER = ["节点", "补涨", "突破", "主升"]

# 历史 Excel OCR 造成的错码 → 真实代码。仅供「日K抓取」使用：
# 数据里的 code 保持不变（题材映射/连板递推仍按原 code 自洽），
# 只是抓新浪日K时换成真实代码，避免这些个股点开无K线。
CODE_FIX = {
    "609000": "000609",   # ST中迪（000609 前后颠倒）
    "686009": "600939",   # 重庆建工
    "500792": "300792",   # 壹网壹创（OCR 错字「壹网壹刚」）
    "606000": "000909",   # 数源科技（历史复盘图 OCR 错码）
    "066000": "000990",   # 诚志股份（2022 复盘图 OCR 错码，首位多 6）
    "966000": "000996",   # 中国中期（2021 复盘图 OCR 错码，首位 6→9）
}
def fix_code(code):
    c = str(code or "").strip()
    return CODE_FIX.get(c, c)
KLINE_DAYS = 90        # 悬停日K覆盖的最近交易日数


def log(msg):
    print(msg, flush=True)


# ------------------------------------------------------------------ HTTP

def http(url, referer, timeout=25, encoding=None):
    try:
        out = subprocess.run(
            ["curl", "-s", "--max-time", str(timeout),
             "-H", "User-Agent: " + UA,
             "-H", "Referer: " + referer,
             "-H", "Accept: application/json, text/plain, */*",
             url],
            capture_output=True, timeout=timeout + 8)
        raw = out.stdout
        if encoding:
            return raw.decode(encoding, errors="ignore")
        return raw.decode("utf-8", errors="ignore")
    except Exception:
        return ""


def http_json(url, referer, timeout=25):
    txt = http(url, referer, timeout).strip()
    if not txt.startswith("{"):
        return None
    try:
        return json.loads(txt)
    except Exception:
        return None


# ------------------------------------------------------------------ 同花顺涨停池

def ths_pool(date_str):
    """返回当日涨停池 list；非交易日/失败返回 None"""
    out = []
    for page in range(1, 4):
        url = ("https://data.10jqka.com.cn/dataapi/limit_up/limit_up_pool"
               "?page=%d&limit=100&field=%s&filter=HS,GEM2STAR"
               "&order_field=330324&order_type=0&date=%s"
               % (page, THS_FIELDS, date_str))
        d = None
        for attempt in range(THS_RETRY):
            d = http_json(url, THS_REFERER)
            if d and d.get("status_code") == 0 and d.get("data") is not None:
                break
            d = None
            time.sleep(THS_INTERVAL * (attempt + 1))
        if d is None:
            return None if page == 1 else out
        info = d["data"].get("info") or []
        out.extend(info)
        total = (d["data"].get("page") or {}).get("total") or 0
        if len(out) >= total or not info:
            break
        time.sleep(0.6)
    return out


def parse_boards(item):
    hd = item.get("high_days") or ""
    if "首板" in hd:
        return 1
    m = re.search(r"(\d+)\s*板", hd)
    if m:
        return int(m.group(1))
    v = item.get("high_days_value")
    if isinstance(v, (int, float)) and v > 65536:
        return int(v) // 65536 + 1
    return 1


def ts2str(ts):
    try:
        ts = int(ts)
        if ts > 1e12:
            ts = ts / 1000
        return datetime.fromtimestamp(ts).strftime("%H:%M:%S")
    except Exception:
        return "--:--"


def norm_stock(it):
    return {
        "code": it.get("code"),
        "name": it.get("name"),
        "chg": round(float(it.get("change_rate") or 0), 2),
        "boards": parse_boards(it),
        "days": it.get("high_days") or "",
        "reason": it.get("reason_type") or "",
        "open_num": int(it.get("open_num") or 0),
        "first_seal": ts2str(it.get("first_limit_up_time")),
        "seal_fund": float(it.get("order_amount") or 0),
        "turnover": round(float(it.get("turnover_rate") or 0), 2),
    }


def workdays_back(count):
    days, d = [], _date.today()
    while len(days) < count:
        if d.weekday() < 5:
            days.append(d.strftime("%Y%m%d"))
        d -= timedelta(days=1)
    return days


def is_yizi(s):
    """当日一字板：集合竞价即封死（09:26 前首次涨停）且全天未开板。
    一字板买不进、无换手，不构成可参与的市场空间"""
    seal = s.get("first_seal") or ""
    return bool(seal) and seal <= "09:26:00" and int(s.get("open_num") or 0) == 0


def yizi_map(pools):
    """{date: set(当日一字板 code)}"""
    return {d: {s["code"] for s in lst if is_yizi(s)} for d, lst in pools.items()}


def all_yizi_run(code, boards, i, dates, yz):
    """[i-boards+1, i] 整段连续涨停是否每天都是一字板（连续一字无换手）"""
    for k in range(max(0, i - boards + 1), i + 1):
        if code not in yz[dates[k]]:
            return False
    return True


def compute_streaks(pools, anchors=None):
    """连板数口径：由涨停池逐日连续性递推计算连续涨停天数。
    同花顺 high_days 是"N天M板"波段口径（允许中间断板），不能当连板数用。
    anchors: {date:{code:板数}} —— 湖南人复盘表标注的连板锚点，优先于递推
    （锚点日直接取湖南人数值，其后的交易日从锚点值继续递推）。
    返回 {date: {code: 连板数}}"""
    dates = sorted(pools)
    anchors = anchors or {}
    res, prev = {}, {}
    for d in dates:
        cur = {}
        an = anchors.get(d, {})
        for s in pools[d]:
            code = s["code"]
            cur[code] = an[code] if code in an else prev.get(code, 0) + 1
        res[d] = cur
        prev = cur
    return res


def load_pool_cache():
    if os.path.exists(POOL_CACHE):
        try:
            return json.load(open(POOL_CACHE, encoding="utf-8"))
        except Exception:
            return {}
    return {}


def save_pool_cache(c):
    json.dump(c, open(POOL_CACHE, "w", encoding="utf-8"),
              ensure_ascii=False)


def collect_range(start_date, sleep_s=0.9):
    """抓 start_date..今日 的全部交易日涨停池（用于全年数据）
    空交易日（假期）直接跳过；请求失败日做三轮补抓，避免断链导致连板数失真"""
    start = datetime.strptime(start_date, "%Y%m%d").date()
    end = _date.today()
    cands, d = [], start
    while d <= end:
        if d.weekday() < 5:
            cands.append(d.strftime("%Y%m%d"))
        d += timedelta(days=1)
    log("      区间 %s ~ %s，待抓 %d 个工作日" % (cands[0], cands[-1], len(cands)))

    cache = load_pool_cache()
    hist, failed = {}, []
    new_fetch = 0
    # 收盘后（≥15:05）强制重抓「当天」，覆盖盘中快照：盘中在池的票可能尾盘炸板
    # （如 2026-11-17 广百股份 12:33 在池、收盘未封住），不覆盖就会把「当日没涨停」
    # 算成 4 连板，污染空间高度 / 空间压制 / 梯队。收盘前仍沿用缓存，避免重复请求。
    today_s = end.strftime("%Y%m%d")
    force_today = datetime.now().strftime("%H:%M") >= "15:05" and today_s in cands
    for k, ds in enumerate(cands, 1):
        if cache.get(ds) and not (force_today and ds == today_s):
            hist[ds] = cache[ds]
            continue
        info = ths_pool(ds)
        if info is None and cache.get(ds):
            hist[ds] = cache[ds]        # 重抓失败保留原值，至少不丢当天
            continue
        if info is None:
            failed.append(ds)
        elif info:
            hist[ds] = [norm_stock(x) for x in info]
            cache[ds] = hist[ds]
            new_fetch += 1
        if k % 20 == 0:
            log("      ... %s  已取 %d 个交易日" % (ds, len(hist)))
        time.sleep(sleep_s)

    for rnd in range(3):
        if not failed:
            break
        retry, failed = failed, []
        for ds in retry:
            info = ths_pool(ds)
            if info is None:
                failed.append(ds)
            elif info:
                hist[ds] = [norm_stock(x) for x in info]
                cache[ds] = hist[ds]
                new_fetch += 1
            time.sleep(1.6)
        log("      补抓第%d轮，仍失败 %d 天" % (rnd + 1, len(failed)))
    if new_fetch:
        save_pool_cache(cache)
    if failed:
        log("      警告: %s 未取到" % ",".join(failed))
    return dict(sorted(hist.items()))


def collect_history(n):
    """抓最近 n 个交易日的涨停池 {date: [stock...]}
    失败日会破坏连板数递推链，故对缺失日做两轮补抓"""
    cands = workdays_back(n * 2 + 25)
    hist, failed = {}, []
    for d in cands:
        info = ths_pool(d)
        if info is None:
            failed.append(d)
            continue
        hist[d] = [norm_stock(x) for x in info]
        log("      %s  涨停 %3d 家   [%d/%d]" % (d, len(hist[d]), len(hist), n))
        if len(hist) >= n:
            break
        time.sleep(THS_INTERVAL)

    # 补抓失败日（仅补已有区间内的空洞，保持序列连续）
    for rnd in range(2):
        if not failed:
            break
        lo, hi = min(hist), max(hist) if hist else None
        retry = []
        for d in failed:
            if lo <= d <= hi:
                retry.append(d)
        failed = []
        for d in retry:
            log("      补抓 %s ..." % d)
            info = ths_pool(d)
            if info is None:
                failed.append(d)
                continue
            hist[d] = [norm_stock(x) for x in info]
            log("      %s  涨停 %3d 家 (补抓)" % (d, len(hist[d])))
            time.sleep(THS_INTERVAL)
    if failed:
        log("      警告: %s 仍未取到，连板数在这些日可能失真" % ",".join(failed))
    return dict(sorted(hist.items()))


# ------------------------------------------------------------------ 腾讯日线（跌停判定）

def tx_code(code):
    if code.startswith("6"):
        return "sh" + code
    if code.startswith(("0", "3")):
        return "sz" + code
    return "bj" + code


def tx_daily(code, n=60):
    """日线 {date: (open, close, preclose)}。
    腾讯接口已被 WAF 拦截，改用新浪历史日线（不复权）"""
    url = ("https://money.finance.sina.com.cn/quotes_service/api/json_v2.php/"
           "CN_MarketData.getKLineData?symbol=%s&scale=240&ma=no&datalen=%d"
           % (tx_code(code), n))
    txt = http(url, "https://finance.sina.com.cn/", timeout=25).strip()
    try:
        arr = json.loads(txt)
        if not isinstance(arr, list):
            return {}
    except Exception:
        return {}
    out, prec = {}, None
    for r in arr:
        d = r["day"].replace("-", "")
        openp, close = float(r["open"]), float(r["close"])
        out[d] = (openp, close, prec)
        prec = close
    return out


def limit_down_rate(name, code):
    if code.startswith("4") or code.startswith("8"):
        return 0.30
    if code.startswith(("30", "68")):
        return 0.20
    if "ST" in (name or "").upper():
        return 0.05
    return 0.10


# ------------------------------------------------------------------ 日K（同花顺优先）
#
# 用户口径：日K 实时从同花顺取。同花顺 v6 日K接口一次返回最近 140 根「不复权」日线，
# 与新辛接口的近期数据逐根一致（老数据因复权口径不同会有差异，故只用于增量刷新，
# 历史由本地缓存接管）。字段：date,open,high,low,close,volume,amount,...
THS_KLINE_URL = "https://d.10jqka.com.cn/v6/line/hs_%s/01/last.js"
THS_KLINE_N = 140


def ths_kline(code, n=THS_KLINE_N):
    """同花顺不复权日K [[date,open,close,low,high,volume], ...]（升序）"""
    code = fix_code(code)
    if not code:
        return []
    url = THS_KLINE_URL % code
    txt = http(url, "http://stockpage.10jqka.com.cn/%s/" % code, timeout=20).strip()
    if not txt or "quotebridge" not in txt:
        return []
    i, j = txt.find("("), txt.rfind(")")
    if i < 0 or j <= i:
        return []
    try:
        obj = json.loads(txt[i + 1:j])
    except Exception:
        return []
    out = []
    for row in (obj.get("data") or "").split(";"):
        p = row.split(",")
        if len(p) < 7:
            continue
        try:
            out.append([p[0], float(p[1]), float(p[4]),
                        float(p[3]), float(p[2]), int(float(p[5]))])
        except Exception:
            continue
    out.sort(key=lambda b: b[0])
    return out[-n:] if n else out


def merge_klines(old, new, n=520):
    """按日期合并：新数据覆盖旧数据同一天，历史更早的旧数据保留。"""
    book = {}
    for b in old or []:
        if b and len(b) >= 2:
            book[b[0]] = b
    for b in new or []:
        if b and len(b) >= 2:
            book[b[0]] = b
    bars = [book[d] for d in sorted(book)]
    return bars[-n:] if n else bars


def kline_live(code, n=520, prefer="ths"):
    """实时日K：同花顺优先，取不到（如北交所个别代码）回落新浪。"""
    if prefer == "ths":
        bars = ths_kline(code)
        if len(bars) >= 2:
            return bars
    return sina_kline(code, n)


def sina_kline(code, n=60):
    """不复权日K [[date,open,close,low,high,volume], ...]（同花顺取不到时兜底）"""
    url = ("https://money.finance.sina.com.cn/quotes_service/api/json_v2.php/"
           "CN_MarketData.getKLineData?symbol=%s&scale=240&ma=no&datalen=%d"
           % (tx_code(code), n))
    txt = http(url, "https://finance.sina.com.cn/", timeout=25).strip()
    try:
        arr = json.loads(txt)
        if not isinstance(arr, list):
            return []
    except Exception:
        return []
    out = []
    for r in arr:
        try:
            out.append([r["day"].replace("-", ""),
                        float(r["open"]), float(r["close"]),
                        float(r["low"]), float(r["high"]),
                        int(float(r.get("volume") or 0))])
        except Exception:
            continue
    return out


def is_limit_down(code, name, d, tx_cache):
    """d 日是否跌停（收盘价 <= 跌停价+容差）"""
    if code not in tx_cache:
        tx_cache[code] = tx_daily(code)
    daily = tx_cache[code]
    rec = daily.get(d)
    if not rec or rec[1] is None:
        return None
    close, prec = rec[1], rec[2]
    limit_price = round(prec * (1 - limit_down_rate(name, code)), 2)
    return close <= limit_price + 0.005


# ------------------------------------------------------------------ 淘股吧：文章列表与图片

def tgb_articles():
    """湖南人博客第一页 30 篇 [{url,title,date}]"""
    html = http(TGB_BLOG, TGB_REFERER, timeout=30)
    if not html:
        return []
    arts = []
    for block in re.split(r'class="article_tittle"', html)[1:]:
        mu = re.search(r'href="(/a/[A-Za-z0-9_-]+)"', block)
        mt = re.search(r'title="([^"]+)"', block)
        md = re.search(r'tittle_fbshijian[^>]*>\s*(\d{4}-\d\d-\d\d)', block)
        if mu and md:
            arts.append({
                "url": "https://www.tgb.cn" + mu.group(1),
                "title": (mt.group(1) if mt else "").strip(),
                "date": md.group(1).replace("-", ""),
            })
    return arts


def article_images(url):
    """主帖图片 URL 列表。
    PC 版正文图固定以 `_760w.png` 结尾；若是"登录可查看全文"的受限文章
    （正文只剩 [图片] 占位），回退移动版 m.tgb.cn（图以 `_max.png` 结尾）。"""
    html = http(url, TGB_REFERER, timeout=30)
    urls = list(dict.fromkeys(
        re.findall(r'data-original="(https://image\.tgb\.cn[^"]+_760w\.png)"', html)))
    if not urls:
        m = re.search(r"/a/([A-Za-z0-9_-]+)", url)
        if m:
            mh = http("https://m.tgb.cn/a/" + m.group(1), "https://m.tgb.cn/",
                      timeout=30)
            urls = list(dict.fromkeys(
                re.findall(r'(https://image\.tgb\.cn/[^"\'\s]+?_max\.png)', mh)))
    return urls


def _upgrade(u):
    """缩略图 URL -> 原图（下载函数里仍会再兜底一次）"""
    return u.replace("_760w.png", "_max.png")


def load_ocr_cache():
    if os.path.exists(OCR_CACHE):
        try:
            return json.load(open(OCR_CACHE, encoding="utf-8"))
        except Exception:
            return {}
    return {}


def save_ocr_cache(c):
    json.dump(c, open(OCR_CACHE, "w", encoding="utf-8"), ensure_ascii=False)


_ocr = None


def _download_image(url):
    """下载图片到临时文件；优先 _max 原图，失败退回原 URL"""
    import tempfile
    for u in (url.replace("_760w", "_max"), url):
        tmp = tempfile.NamedTemporaryFile(suffix=".png", delete=False)
        tmp.close()
        rc = subprocess.run(["curl", "-s", "--max-time", "60",
                             "-H", "User-Agent: " + UA,
                             "-H", "Referer: " + TGB_REFERER,
                             "-o", tmp.name, u]).returncode
        if rc == 0 and os.path.getsize(tmp.name) > 5000:
            return tmp.name
        try:
            os.unlink(tmp.name)
        except Exception:
            pass
    return None


def ocr_image(url):
    """下载并 OCR 一张图，返回文本行列表（带持久缓存）
    超长表格图先放大再切片识别，避免检测阶段过度缩放导致小字丢失"""
    cache = load_ocr_cache()
    if url in cache:
        return cache[url]
    path = _download_image(url)
    if not path:
        return []
    lines = []
    try:
        from PIL import Image
        im = Image.open(path).convert("RGB")
        w, h = im.size
        if w >= 700 or h < 600:      # 只要竖长的表格图
            return []
        global _ocr
        if _ocr is None:
            from rapidocr_onnxruntime import RapidOCR
            _ocr = RapidOCR()
        scale = 1.8 if w < 620 else 1.0
        if scale != 1.0:
            im = im.resize((int(w * scale), int(h * scale)), Image.LANCZOS)
        seg_h, ov = 1400, 90
        y = 0
        W, H = im.size
        while y < H:
            crop = im.crop((0, y, W, min(y + seg_h, H)))
            fn = path + ".seg.png"
            crop.save(fn)
            result, _ = _ocr(fn)
            if result:
                lines.extend(r[1].strip() for r in result)
            try:
                os.unlink(fn)
            except Exception:
                pass
            if y + seg_h >= H:
                break
            y += seg_h - ov
    except Exception as e:
        log("      OCR失败 %s: %s" % (url.split("/")[-1], e))
        lines = []
    finally:
        try:
            os.unlink(path)
        except Exception:
            pass
    cache[url] = lines
    save_ocr_cache(cache)
    return lines


# ------------------------------------------------------------------ 湖南人表格解析

NON_THEME = {"市场连板股", "涨停炸板", "炸板", "跌停", "跌停股", "其他",
             "其他热点", "其他个股", "其他概念", "其他板块", "次新股",
             "ST", "ST板块", "昨日涨停表现", "业绩", "业绩增长",
             "三季报增长", "半年报增长", "一季报增长", "年报增长",
             "业绩扭亏", "业绩预增", "扭亏", "增持", "回购", "重组"}


def _edit_dist(a, b):
    if abs(len(a) - len(b)) > 1:
        return 9
    # 简化编辑距离（长度差<=1 时计算）
    m, n = len(a), len(b)
    dp = list(range(n + 1))
    for i in range(1, m + 1):
        pre, dp[0] = dp[0], i
        for j in range(1, n + 1):
            cur = dp[j]
            dp[j] = min(dp[j] + 1, dp[j - 1] + 1,
                        pre + (a[i - 1] != b[j - 1]))
            pre = cur
    return dp[n]


def merge_theme_names(matrix):
    """OCR 题材名容错归并：编辑距离<=1 的名字合并到出现天数更多的写法"""
    names = list(matrix.keys())
    parent = {n: n for n in names}

    def find(x):
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    for i, a in enumerate(names):
        for b in names[i + 1:]:
            # 编辑距离<=1（OCR 单字错），或短名包含于长名（分类口径差异）
            if _edit_dist(a, b) <= 1 or (len(a) >= 3 and a in b) \
                    or (len(b) >= 3 and b in a):
                ra, rb = find(a), find(b)
                if ra != rb:
                    wa = len(matrix[ra])
                    wb = len(matrix[rb])
                    keep, drop = (ra, rb) if wa >= wb else (rb, ra)
                    parent[find(drop)] = keep
    merged = {}
    for n in names:
        r = find(n)
        tgt = merged.setdefault(r, {})
        for d, v in matrix[n].items():
            tgt.setdefault(d, v)
    return {r: v for r, v in merged.items()}


def hunan_boards_of(cell):
    """湖南人连板数列解析：'2'、'2/5天3板'（连板/累计）、'1/3天2板AI短剧'（黏连原因）。
    纯'N天M板'（无前导连板数）仅当天数==板数时可靠（N连板）。返回 int 或 None"""
    if not cell:
        return None
    m = re.match(r"^(\d{1,2})连板", cell)            # '2连板'
    if m:
        return int(m.group(1))
    m = re.match(r"^(\d{1,2})板?(?:/|$)", cell)      # 前导数字 = 递推连板数
    if m:
        return int(m.group(1))
    m = re.match(r"^(\d{1,2})天(\d{1,2})板", cell)   # 波段格式：N==M 才是连板
    if m:
        n, b = int(m.group(1)), int(m.group(2))
        return b if n == b else None
    return None


_NAME_OK = re.compile(r"^[A-Za-z\*]{0,3}[\u4e00-\u9fa5]{2,6}[ABab]?$")


def clean_stock_name(raw):
    """OCR 名称清洗：把被黏连的"描述文本 + 名称"还原为纯股票名。
    - 正常名（2~6 汉字，可带 XD/*ST/N 前缀）原样返回
    - 含分号的取最后一段（'…；亚太药业' -> '亚太药业'）
    - 含句读且无分号的视为描述残句，返回 ""（该行丢弃，宁缺勿错）"""
    n = (raw or "").strip().replace(" ", "")
    if not n:
        return ""
    if _NAME_OK.match(n):
        return n
    if "；" in n or ";" in n:
        n = re.split(r"[；;]", n)[-1].strip()
        m = re.match(r"^([A-Za-z\*]{0,3}[\u4e00-\u9fa5]{2,6})", n)
        return m.group(1) if m else ""
    # 名称后紧跟时间/连板（'广安爱众14:51'、'通达电气13:351/5天4板'）：取开头名称
    m = re.match(r"^([A-Za-z\*]{0,3}[\u4e00-\u9fa5]{2,6}?)(?=\d)", n)
    if m:
        return m.group(1)
    if re.search(r"[。，、：:（）()]", n):
        return ""
    m = re.match(r"^([A-Za-z\*]{0,3}[\u4e00-\u9fa5]{2,6}[ABab]?)$", n)
    return m.group(1) if m else ""


def _parse_rows_by_time(lines, pool_map):
    """时间锚点解析（代码列在图右侧的版式：题材标签 / 名称 / 时间 / 连板 / 金额 / 代码 / 描述）。
    原"代码行首"解析在此版式下会把代码错配给下一只股，故按时间行定位名称与代码。"""
    out, seen = [], set()
    for i, ln in enumerate(lines):
        if not re.fullmatch(r"\d{1,2}:\d{2}", ln):
            continue
        name = ""
        for j in range(i - 1, max(-1, i - 4), -1):
            c = clean_stock_name(lines[j])
            if c and not re.search(r"[;；]$", lines[j]):
                name = c
                break
        if not name:
            continue
        nb = lines[i + 1] if i + 1 < len(lines) else ""
        hb = hunan_boards_of(nb)
        if hb is None and nb.strip().startswith("首"):
            hb = 1
        code = ""
        for j in range(i + 2, min(len(lines), i + 9)):
            if re.fullmatch(r"\d{6}", lines[j]):
                code = lines[j]
                break
        if not code:
            continue
        st = {"code": code, "name": name, "boards": hb or 1, "reason": "",
              "time": ln, "hunan": bool(hb)}
        p = pool_map.get(code)
        if p:
            st["name"] = p["name"]
            if not hb:
                st["boards"] = p["boards"]
        else:
            hit = next((v for k, v in pool_map.items() if v["name"] == name), None)
            if hit:
                st["code"] = hit["code"]
                if not hb:
                    st["boards"] = hit["boards"]
        if code in seen:
            continue
        seen.add(code)
        out.append(st)
    return out


def _layout_is_time_anchored(lines):
    """判断块内版式：代码列在右侧（名称上一行是"题材标签;"、代码行在时间之后）→ 需走时间锚点解析。
    返回 True 表示"代码行首"假设不成立。"""
    times = [i for i, l in enumerate(lines) if re.fullmatch(r"\d{1,2}:\d{2}", l)]
    if len(times) < 3:
        return False
    tagged = 0
    for t in times:
        # 版式：题材标签 / 名称 / 时间 …  → 时间行上两行是"题材;"
        if t - 2 >= 0 and re.search(r"[;；]$", lines[t - 2].strip()):
            tagged += 1
    return tagged >= len(times) * 0.5


def _parse_stock_rows(lines, pool_map, by_time=False):
    """块内个股行: [代码,名称,时间,连板数,原因]（OCR 有漏项，做容错）。
    连板数以湖南人表格标注为准（hunan=True），涨停池递推仅补缺失。
    by_time=True 时改用时间锚点解析（适用于"代码列在图右侧"的版式）"""
    if by_time:
        return _parse_rows_by_time(lines, pool_map)
    stocks, buf = [], []

    def flush():
        nonlocal buf
        if not buf:
            return
        if not re.fullmatch(r"\d{6}", buf[0]):
            buf = []
            return
        code = buf[0]
        rest = buf[1:]
        if not rest:
            buf = []
            return
        # OCR 可能黏连"时间+连板数"（如 14:542/19天11板）：先拆开
        glued = re.match(r"^(\d{1,2}:\d{2})(\d{1,2}(?:/\S*)?)$", rest[0])
        if glued:
            rest = [glued.group(1), glued.group(2)] + rest[1:]
        ti = next((i for i, x in enumerate(rest)
                   if re.fullmatch(r"\d{1,2}:\d{2}(:\d{2})?", x)), None)
        name = clean_stock_name(rest[0] if ti is None else "".join(rest[:ti]))
        if not name:                 # 名称被 OCR 拆坏（描述残句）：丢弃该行，避免脏名入池
            buf = []
            return
        boards, hb = 1, None
        if ti is not None and ti + 1 < len(rest):
            nx = rest[ti + 1]
            hb = hunan_boards_of(nx)
            if hb and hb > 30:
                hb = None
            if hb:
                boards = hb
        reason = ""
        if ti is not None and ti + 2 < len(rest):
            reason = re.split(r"[|｜]", rest[ti + 2])[0].strip()
        st = {"code": code, "name": name, "boards": boards, "reason": reason,
              "time": rest[ti] if ti is not None and ti < len(rest) else "",
              "hunan": bool(hb)}
        p = pool_map.get(code)
        if p:                      # 涨停池校准（仅补湖南人缺失项）
            st["name"] = p["name"]
            if not hb:
                st["boards"] = p["boards"]
        else:                      # 按名称回查
            hit = next((v for k, v in pool_map.items()
                        if v["name"] == name), None)
            if hit:
                st["code"] = hit["code"]
                st["boards"] = hit["boards"]
        stocks.append(st)
        buf = []

    for ln in lines:
        if re.fullmatch(r"\d{6}", ln):
            flush()
            buf.append(ln)
        elif ln.startswith("【"):
            flush()
        elif buf:
            buf.append(ln)
    flush()
    return stocks


def parse_article(lines, pool_map, by_time=False):
    """解析一篇文章的 OCR 行 -> {overview, blocks:[{name,count,amount,stocks}]}
    by_time=True：个股行改用时间锚点解析（代码列在图右侧的版式）"""
    overview, blocks, cur = {}, [], None

    def close_block():
        nonlocal cur
        if cur:
            cur["stocks"] = _parse_stock_rows(cur.pop("_rows"), pool_map, by_time)
            blocks.append(cur)
            cur = None

    for ln in lines:
        m = re.match(r"^【(.+?)】\s*(.*)", ln)
        if m:
            close_block()
            tail = m.group(2)
            cnt = re.search(r"(\d+)\s*只", tail)
            amt = re.search(r"([\d.]+)\s*亿", tail)
            cur = {"name": m.group(1).strip(),
                   "count": int(cnt.group(1)) if cnt else 0,
                   "amount": float(amt.group(1)) if amt else 0,
                   "_rows": []}
            continue
        if cur is not None:
            cur["_rows"].append(ln)
        for key, pat in (("zt", r"涨停(\d+)家"), ("zb", r"炸板(\d+)家"),
                         ("dt", r"跌停(\d+)家"), ("first", r"首板(\d+)家"),
                         ("lianban", r"连板(\d+)家"),
                         ("zt_amt", r"涨停金额[：:](\d+)亿"),
                         ("zb_amt", r"炸板金额[：:](\d+)亿")):
            mm = re.search(pat, ln)
            if mm and key not in overview:
                overview[key] = int(mm.group(1))
    close_block()

    # 切片重叠会产生相邻同名块：合并个股（按 code 去重）
    merged = []
    for b in blocks:
        if merged and merged[-1]["name"] == b["name"]:
            seen = {s["code"] for s in merged[-1]["stocks"]}
            merged[-1]["stocks"].extend(
                s for s in b["stocks"] if s["code"] not in seen)
            if b["count"] > merged[-1]["count"]:
                merged[-1]["count"] = b["count"]
            if b["amount"] > merged[-1]["amount"]:
                merged[-1]["amount"] = b["amount"]
        else:
            merged.append(b)

    # 市场连板股块用于交叉校验
    maxb = 0
    for b in merged:
        if b["name"] == "市场连板股":
            maxb = max([s["boards"] for s in b["stocks"]] or [0])
    overview["max_board"] = maxb
    return overview, merged


# ------------------------------------------------------------------ 空间情绪四阶段模型
# 【全站唯一口径】以下三个概念在数据层 / 前端 / 文档中只有这一种叫法，不得混用：
#   空间龙头   = 当日「换手连板」最高的个股（排除当前连板段全程一字板、排除次新股），可并列
#   空间高度   = 空间龙头的连板数（spaceTop）
#   空间压制   = 上一个空间龙头断板时的高度（suppress）；只有「上一日空间龙头整批断板」
#                才退位改写（允许随退潮逐级下移），被突破后清空
# 注：maxBoard 是全市场最高连板（含一字板、含次新），只作参考，不参与阶段判定。

def build_stages(pools):
    """逐日判定四阶段（节点/补涨/突破/主升）。返回 days 列表。
    池内 s["new"]=True 的次新股（上市不足 60 个交易日）连板不纳入空间统计。"""
    dates = sorted(pools)
    daily = {d: {s["code"]: s for s in pools[d]} for d in dates}
    maxb = {d: max((s["boards"] for s in pools[d]), default=0) for d in dates}
    yz = yizi_map(pools)

    def space_of(i, d):
        """空间龙头 = 排除"当前连板段全程一字板"（无换手、买不进）后的
        当日连板最高的个股（可并列）。最高板若为一字股，空间取次高换手板"""
        elig = [s for s in pools[d]
                if not s.get("new")
                and not all_yizi_run(s["code"], s["boards"], i, dates, yz)]
        mb = max((s["boards"] for s in elig), default=0)
        return [s for s in elig if s["boards"] == mb]

    space = {d: space_of(i, d) for i, d in enumerate(dates)}
    stop = {d: max((s["boards"] for s in space[d]), default=0) for d in dates}

    def yz_note(i, d):
        """被排除的全程一字连板提示（仅列高于空间高度的，避免刷屏）"""
        sm = stop[d]
        ex = [s for s in pools[d]
              if s["boards"] >= 2 and s["boards"] > sm and not s.get("new")
              and all_yizi_run(s["code"], s["boards"], i, dates, yz)]
        if not ex:
            return ""
        return "一字连板不计空间:" + "/".join(
            "%s%d板" % (s["name"], s["boards"]) for s in ex)

    days, H, state = [], None, None
    node_date, node_subs = None, []
    sup_stocks = []      # 形成当前空间压制的断板龙头（含题材待回填）

    for i, d in enumerate(dates):
        if i == 0:
            yn = yz_note(i, d)
            days.append({"date": d, "stage": None, "suppress": None,
                         "spaceTop": stop[d], "maxBoard": maxb[d],
                         "spaceStocks": [s["name"] for s in space[d]],
                         "spaceFull": [{"code": s["code"], "name": s["name"],
                                        "boards": s["boards"]} for s in space[d]],
                         "suppressStocks": [],
                         "note": "序列起点" + ("；" + yn if yn else "")})
            continue
        pd_ = dates[i - 1]
        prev_space = space[pd_]
        survived, broken = [], []
        for s in prev_space:
            cur = daily[d].get(s["code"])
            if cur and cur["boards"] == s["boards"] + 1:
                survived.append(cur)
            else:
                broken.append(s)

        note_parts = []
        if broken:
            note_parts.append("断板:" + "/".join(
                "%s%d板" % (s["name"], s["boards"]) for s in broken))
        if survived:
            note_parts.append("晋级:" + "/".join(
                "%s%d板" % (s["name"], s["boards"]) for s in survived))

        # ---------- 事件判定（优先级） ----------
        broke_all = bool(broken) and not survived
        top_d = stop[d]
        if H is not None and top_d > H:          # 空间高度超过压制（一字/次新不参与）
            state = "主升" if state in ("突破", "主升") else "突破"
            note_parts.append("突破压制%d板" % H)
            H = None
            sup_stocks = []
        elif broke_all and state != "节点":
            state = "节点"
            note_parts.append("空间龙头断板")
        elif state == "节点" and node_date == pd_:
            # 节点次日：次高空间龙头（可并列）晋级判定
            promoted = None
            for sub in node_subs:
                cur = daily[d].get(sub["code"])
                if cur and cur["boards"] == sub["boards"] + 1:
                    promoted = sub
                    break
            if promoted:
                state = "补涨"
                note_parts.append("次高空间龙头%s晋级" % promoted["name"])
            else:
                note_parts.append("次高未晋级" if node_subs else "无次高空间龙头")
        elif state == "突破" and survived:
            state = "主升"

        # 空间压制 = 上一个空间龙头的空间高度：只有「上一日空间龙头整批断板」
        # 才算退位，此时压制改写为该批龙头的高度（允许随退潮逐级下移）；
        # 若仍有龙头晋级（空间在延续），低位过渡票断板不改写压制。
        if broke_all and state not in ("突破", "主升"):
            bt = max(s["boards"] for s in broken)
            if bt >= 2:
                H = bt
                sup_stocks = [{"code": s["code"], "name": s["name"],
                               "boards": s["boards"], "breakDate": d}
                              for s in broken if s["boards"] == H]
        if broke_all:
            node_date = d
            # 次高空间龙头候选：与空间龙头同口径（排除一字连板、排除次新）
            others = [s for s in pools[d]
                      if s["code"] not in [x["code"] for x in broken]
                      and not s.get("new")
                      and not all_yizi_run(s["code"], s["boards"], i, dates, yz)]
            node_subs = [s for s in others if s["boards"] >= 2]
            if node_subs:
                top = max(s["boards"] for s in node_subs)
                node_subs = [s for s in node_subs if s["boards"] == top]
            else:
                node_subs = []

        yn = yz_note(i, d)
        if yn:
            note_parts.append(yn)

        days.append({"date": d, "stage": state, "suppress": H,
                     "spaceTop": top_d, "maxBoard": maxb[d],
                     "ztCount": len(pools[d]),
                     "spaceStocks": [s["name"] for s in space[d]],
                     "spaceFull": [{"code": s["code"], "name": s["name"],
                                    "boards": s["boards"]} for s in space[d]],
                     "suppressStocks": list(sup_stocks),
                     "note": "；".join(note_parts)})
    return days


# ------------------------------------------------------------------ 同花顺题材补充

def ths_fallback_themes(pool, topn=6):
    """湖南人缺失日：用同花顺涨停原因(多标签拆分)聚合题材。
    口径与湖南人一致：题材内涨停>=5 且 有连板(按连板数口径)
    topn：每天只保留最强的 topn 个题材，避免全年数据下题材碎片化"""
    from collections import Counter
    if isinstance(pool, dict):        # 兼容 code->stock 映射
        pool = list(pool.values())
    cnt, maxb = Counter(), {}
    for s in pool:
        tags = [t.strip() for t in re.split(r"[+＋]", s.get("reason") or "")
                if t.strip()]
        for t in set(tags):
            cnt[t] += 1
            maxb[t] = max(maxb.get(t, 0), s["boards"])
    out = []
    for name, c in cnt.items():
        if name in NON_THEME:
            continue
        if c >= 5 and maxb[name] >= 2:
            stocks = []
            for s in pool:
                tags = [t.strip() for t in re.split(r"[+＋]", s.get("reason") or "")]
                if name in tags:
                    stocks.append({"code": s["code"], "name": s["name"],
                                   "boards": s["boards"], "reason": s["reason"]})
            stocks.sort(key=lambda x: -x["boards"])
            out.append({"name": name, "count": c, "amount": 0,
                        "maxB": maxb[name], "stocks": stocks})
    out.sort(key=lambda b: (-b["count"], -b["maxB"]))
    return out[:topn] if topn else out


# ------------------------------------------------------------------ 赚钱效应

def money_winners(pools, theme_detail, klines):
    """赚钱效应：
    items   昨日连板(>=2板)股中，今日连板或红盘(>0%)的个股
    板块统计 avgAll   题材昨日全部涨停股（含首板）今日平均涨跌幅
             leader   板块连板龙头（昨日题材内最高板）今日涨跌
             mids     中位股 = 空间身位低于板块连板龙头的连板股，今日涨跌
    klines  悬停日K（近60根，不复权）"""
    dates = sorted(pools)
    pool_map = {d: {s["code"]: s for s in pools[d]} for d in dates}
    tx_cache = {}

    def pct(code, d):
        if code not in tx_cache:
            tx_cache[code] = tx_daily(code, 90)
        rec = tx_cache[code].get(d)
        if not rec or rec[2] is None:
            return None
        return round((rec[1] / rec[2] - 1) * 100, 2)

    def today_state(code, d):
        """今日表现：(kind, boards, chg) kind: 连板/红盘/绿盘/平"""
        cur = pool_map[d].get(code)
        if cur:
            return ("连板", cur["boards"], cur["chg"])
        c = pct(code, d)
        if c is None:
            return ("?", 0, None)
        return ("红盘" if c > 0 else ("平" if c == 0 else "绿盘"), 0, c)

    out = {}
    for i in range(1, len(dates)):
        d, pd_ = dates[i], dates[i - 1]
        # 题材归属（湖南人，缺失日回退当日）
        code_theme = {}
        for td in (pd_, d):
            for t in theme_detail.get(td, []):
                for s in t["stocks"]:
                    code_theme.setdefault(s["code"], t["name"])

        groups = {}
        for s in pools[pd_]:
            if s["boards"] < 2:
                continue
            kind, b2, c = today_state(s["code"], d)
            if c is None or (kind not in ("连板", "红盘")):
                continue          # 只展示赚钱的
            th = code_theme.get(s["code"], "其他")
            groups.setdefault(th, []).append(
                {"code": s["code"], "name": s["name"], "prev": s["boards"],
                 "boards": b2, "chg": c, "kind": kind})

        if not groups:
            continue
        # ---- 板块统计 ----
        rows = []
        for th, items in sorted(groups.items(),
                                key=lambda kv: -len(kv[1])):
            members = [s for s in pools[pd_]
                       if code_theme.get(s["code"]) == th]
            # 龙头 = 题材昨日空间高度
            leader = max(members, key=lambda s: s["boards"]) if members else None
            lkind, lb2, lchg = (today_state(leader["code"], d)
                                if leader else ("?", 0, None))
            # 昨日全部涨停股平均涨跌幅
            chgs = []
            for s in members:
                kind2, b2v, c2 = today_state(s["code"], d)
                if c2 is not None:
                    chgs.append(c2)
            avg_all = round(sum(chgs) / len(chgs), 2) if chgs else None
            # 中位股 = 空间身位低于板块连板龙头的连板股
            mids = []
            if leader:
                for s in members:
                    if s["boards"] < leader["boards"]:
                        kind2, b2v, c2 = today_state(s["code"], d)
                        if c2 is None:
                            continue
                        mids.append({"code": s["code"], "name": s["name"],
                                     "prev": s["boards"],
                                     "boards": b2v, "chg": c2, "kind": kind2})
            avg_mid = (round(sum(m["chg"] for m in mids) / len(mids), 2)
                       if mids else None)
            rows.append({
                "theme": th,
                "items": sorted(items, key=lambda x: (-x["boards"], -x["chg"])),
                "avgAll": avg_all,
                "nAll": len(chgs),
                "leader": ({"code": leader["code"], "name": leader["name"],
                            "prev": leader["boards"],
                            "kind": lkind, "boards": lb2, "chg": lchg}
                           if leader else None),
                "mids": sorted(mids, key=lambda m: -m["prev"]),
                "avgMid": avg_mid,
            })
        out[d] = rows

    # ---- 悬停日K：赚钱效应涉及的个股（昨日连板集合），带磁盘缓存 ----
    kl_cache_file = os.path.join(OUT_DIR, ".kline_cache.json")
    kl_cache = {}
    if os.path.exists(kl_cache_file):
        try:
            kl_cache = json.load(open(kl_cache_file, encoding="utf-8"))
        except Exception:
            kl_cache = {}
    codes = set()
    kl_from = max(1, len(dates) - KLINE_DAYS)   # 只嵌最近 N 天的日K，控制 data.js 体积
    for i in range(kl_from, len(dates)):
        d, pd_ = dates[i], dates[i - 1]
        for s in pools[pd_]:
            if s["boards"] >= 2:
                codes.add(s["code"])
    changed = False
    for c in sorted(codes):
        if c in klines:
            continue
        if c in kl_cache:
            klines[c] = kl_cache[c]
            continue
        k = sina_kline(c, 60)
        if k:
            klines[c] = k
            kl_cache[c] = k
            changed = True
        time.sleep(0.35)
    if changed:
        with open(kl_cache_file, "w", encoding="utf-8") as f:
            json.dump(kl_cache, f, ensure_ascii=False)
    return out


# ------------------------------------------------------------------ main

def table_date(lines, post_date):
    """从 OCR 行提取表格自身日期（如 【11.2星期一】湖南人涨停复盘），
    湖南人有时隔日贴旧表，须按表内日期归属"""
    for ln in lines[:30]:
        m = re.search(r"【(\d{1,2})\.(\d{1,2})\s*星期?", ln)
        if m:
            y = int(post_date[:4])
            try:
                dd = _date(y, int(m.group(1)), int(m.group(2)))
            except ValueError:
                return post_date
            pd_ = datetime.strptime(post_date, "%Y%m%d").date()
            if (dd - pd_).days > 10:        # 跨年
                dd = dd.replace(year=y - 1)
            return dd.strftime("%Y%m%d")
    return post_date


def main(n_days=45, start=None):
    if start:
        log("[1/4] 抓取涨停池（%s 至今）..." % start)
        pools = collect_range(start)
    else:
        log("[1/4] 抓取涨停池（%d 交易日）..." % n_days)
        pools = collect_history(n_days)
    pools = {d: v for d, v in pools.items() if v}     # 剔除空数据日
    if not pools:
        raise RuntimeError("未取到涨停池数据")
    dates = sorted(pools)
    pool_map_by_date = {d: {s["code"]: s for s in pools[d]} for d in dates}

    # 连板数口径：湖南人锚点优先（如存在），其余由涨停池逐日连续性递推
    _hb_path = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                            ".hunan_boards.json")
    _hb = {}
    if os.path.exists(_hb_path):
        try:
            _hb = json.load(open(_hb_path, encoding="utf-8"))
        except Exception:
            _hb = {}
    streaks = compute_streaks(pools,
                              {d: v for d, v in _hb.items() if d in pool_map_by_date})
    for d in dates:
        for s in pools[d]:
            s["boards"] = streaks[d].get(s["code"], 1)
    log("      共 %d 个交易日（连板数已按连续涨停口径重算）" % len(dates))

    log("[2/5] 空间压制四阶段模型 ...")
    days = build_stages(pools)

    log("[3/5] 淘股吧湖南人题材（OCR，首次较慢）...")
    min_date = dates[0]
    arts = [a for a in tgb_articles() if a["date"] >= min_date]
    arts.sort(key=lambda a: a["date"])
    log("      待处理文章 %d 篇" % len(arts))
    theme_dates, theme_detail, overview_by_date = [], {}, {}
    for k, a in enumerate(arts, 1):
        if a["date"] not in pool_map_by_date:
            continue
        img_urls = article_images(a["url"])
        lines = []
        for u in img_urls:
            lines.extend(ocr_image(u))
        if not lines:
            log("      [%d/%d] %s 无表格内容" % (k, len(arts), a["date"]))
            continue
        d = table_date(lines, a["date"])
        if d not in pool_map_by_date:
            prev = [x for x in dates if x <= d]
            if not prev:
                log("      [%d/%d] 表日期 %s 超出范围" % (k, len(arts), d))
                continue
            d = prev[-1]
        ov, blocks = parse_article(lines, pool_map_by_date[d])
        themes_today = []
        for b in blocks:
            if b["name"] in NON_THEME:
                continue
            max_bs = max([s["boards"] for s in b["stocks"]] or [0])
            if b["count"] >= 5 and max_bs >= 2:
                b["maxB"] = max_bs
                themes_today.append(b)
        if d in theme_detail:               # 同日多篇（隔日贴旧表）合并
            old = {t["name"]: t for t in theme_detail[d]}
            for t in themes_today:
                o = old.get(t["name"])
                if o:
                    seen = {s["code"] for s in o["stocks"]}
                    o["stocks"].extend(
                        s for s in t["stocks"] if s["code"] not in seen)
                    o["count"] = max(o["count"], t["count"])
                else:
                    theme_detail[d].append(t)
        else:
            theme_detail[d] = themes_today
        overview_by_date[d] = ov
        if themes_today and d not in theme_dates:
            theme_dates.append(d)
        names = [(t["name"], t["count"]) for t in theme_detail[d]]
        log("      [%d/%d] %s(帖%s) 题材 %s" % (k, len(arts), d, a["date"][4:],
                                               names or "无"))
        time.sleep(1.0)
    theme_dates.sort()

    # 湖南人缺失日：同花顺涨停原因补充（口径一致：涨停>=5 且有连板）
    theme_source = {}
    for d in dates:
        if d in theme_detail and theme_detail[d]:
            theme_source[d] = "tgb"
        else:
            fb = ths_fallback_themes(pool_map_by_date[d])
            if fb:
                theme_detail[d] = fb
                theme_dates.append(d)
                theme_source[d] = "ths"
                log("      %s 湖南人缺失 -> 同花顺补充 %d 个题材"
                    % (d, len(fb)))

    # 题材名口径统一：同一题材不同日期的叫法归到一个名字，前端色块才能一致
    THEME_ALIAS = {"短剧传媒": "短剧", "机器人+物理AI": "机器人",
                   "“华”字辈": "华字辈", '"华"字辈': "华字辈"}
    for d in list(theme_detail.keys()):
        ts = theme_detail[d]
        for t in ts:
            t["name"] = THEME_ALIAS.get(t["name"], t["name"])
        agg = {}
        for t in ts:
            o = agg.get(t["name"])
            if not o:
                agg[t["name"]] = t
                continue
            seen = {s["code"] for s in o["stocks"]}
            o["stocks"].extend(s for s in t["stocks"] if s["code"] not in seen)
            o["count"] = max(o["count"], t["count"])
            o["maxB"] = max(o["maxB"], t["maxB"])
            o["amount"] = max(o["amount"], t["amount"])
        if len(agg) != len(ts):
            theme_detail[d] = list(agg.values())

    # 题材矩阵：题材 x 日期（OCR 错字归并）
    matrix = {}
    for d, ts in theme_detail.items():
        for t in ts:
            e = matrix.setdefault(t["name"], {})
            e[d] = {"count": t["count"], "maxB": t["maxB"], "amount": t["amount"]}
    matrix = merge_theme_names(matrix)
    # 重写 themeDetail 里的题材名与归并一致
    renames = {}
    for r, series in matrix.items():
        renames[r] = r
    for d, ts in theme_detail.items():
        for t in ts:
            for r, series in matrix.items():
                if d in series and t["name"] != r and _edit_dist(t["name"], r) <= 1:
                    t["name"] = r
                    break

    log("[4/5] 赚钱效应（昨日连板 -> 今日红盘/连板 + 板块统计 + 悬停日K）...")
    klines = {}
    money = money_winners(pools, theme_detail, klines)
    log("      %d 个交易日有赚钱效应记录，日K %d 只" % (len(money), len(klines)))

    # 空间龙头 / 压制龙头 回填题材
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
    log("      %d 个交易日有赚钱效应记录" % len(money))

    log("[5/5] 输出 data.js ...")
    today = dates[-1]
    data = {
        "updated": datetime.now().strftime("%Y-%m-%d %H:%M"),
        "today": today,
        "dates": dates,
        "stages": days,
        "themeDates": sorted(set(theme_dates)),
        "themeDetail": theme_detail,
        "themeSource": theme_source,
        "money": money,
        "klines": klines,
        "tgb": TGB_BLOG,
    }
    def _slim(stocks):
        by = {}
        for s in stocks:
            by.setdefault(s["boards"], []).append(s)
        out = []
        for b in sorted(by, reverse=True)[:6]:
            out.extend(by[b][:2])
            for s in by[b][:2]:
                s.pop("reason", None)
        return out

    for _d, _ts in theme_detail.items():
        for _t in _ts:
            _t["stocks"] = _slim(_t.get("stocks", []))

    # 空间龙头连板周期线：曾为当日空间高度股（spaceFull）的个股，
    # 输出其完整连板历程（首板→最高板→断板），前端每股一条彩色折线区分周期
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
        if obj["pts"] and max(p[1] for p in obj["pts"]) >= 3:   # 至少3板才算一个周期
            obj["maxB"] = max(p[1] for p in obj["pts"])
            space_series.append(obj)
    space_series.sort(key=lambda o: (-o["maxB"], o["pts"][0][0]))
    data["spaceSeries"] = space_series

    # 最近3日涨停池精简（供前端"刷新今日"按连板口径递推；ay=当前连板段是否全程一字）
    yz_all = yizi_map(pools)
    data["recentPools"] = {
        dt: [{"code": s["code"], "name": s["name"], "boards": s["boards"],
              "ay": 1 if all_yizi_run(s["code"], s["boards"],
                                      dates.index(dt), dates, yz_all) else 0}
             for s in pools[dt]]
        for dt in dates[-3:]
    }

    with open(os.path.join(OUT_DIR, "data.json"), "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False)
    with open(os.path.join(OUT_DIR, "data.js"), "w", encoding="utf-8") as f:
        f.write("window.REVIEW=" +
                json.dumps(data, ensure_ascii=False) + ";")
    log("完成 -> data.js / data.json")


if __name__ == "__main__":
    argv = sys.argv[1:]
    if "--from" in argv:
        main(start=argv[argv.index("--from") + 1])
    else:
        main(int(argv[0]) if argv else 45)
