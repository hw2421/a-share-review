# -*- coding: utf-8 -*-
"""补齐"当日"日K bar（腾讯实时接口 qt.gtimg.cn）。

背景：同花顺 v6 日K / 新浪日K 对"当日"存在滞后（收盘后一段时间才落库），
导致当日连板梯队形态显示「?」、换手标「—」、赚钱效应达标筛选为空。
腾讯实时行情接口在收盘后即有当日完整 OHLC，作为当日 bar 的可靠来源。

用法：python3 fill_today_kline.py [YYYYMMDD]
  · 抓当日涨停池（.pool_cache.json）全部个股 + 连板梯队个股的当日 OHLC；
  · 写入 .stock_kline_cache.json、klines/klines_*.json 分片（只增不减，同日覆盖）；
  · 同步 data.json / data.js 的 klines（近 60 根）与 klinesAt；
  · 不重建全站（重建单独用 refresh_job / build_merged）。
"""
import datetime
import glob
import json
import os
import sys
import time
import urllib.request

BASE = os.path.dirname(os.path.abspath(__file__))
DATA_F = os.path.join(BASE, "data.json")
STOCK_F = os.path.join(BASE, ".stock_kline_cache.json")
POOL_F = os.path.join(BASE, ".pool_cache.json")
KDIR = os.path.join(BASE, "klines")
BATCH = 60


def log(m):
    print("[fill_today] %s" % m, flush=True)


def jload(p, default):
    try:
        return json.load(open(p, encoding="utf-8"))
    except Exception:
        return default


def tx_code(code):
    code = str(code).zfill(6)
    if code.startswith("6"):
        return "sh" + code
    if code.startswith(("0", "3")):
        return "sz" + code
    if code.startswith(("4", "8", "9")):
        return "bj" + code
    return "sh" + code


def fetch_today_bars(codes, day):
    """批量抓腾讯实时，返回 {code: [date,open,close,low,high,vol]}。
    仅保留时间戳属于 day 的快照，避免抓到非目标日。"""
    out = {}
    codes = sorted(set(codes))
    for i in range(0, len(codes), BATCH):
        chunk = codes[i:i + BATCH]
        q = ",".join(tx_code(c) for c in chunk)
        url = "https://qt.gtimg.cn/q=" + q
        try:
            req = urllib.request.Request(url, headers={"Referer": "https://gu.qq.com/"})
            txt = urllib.request.urlopen(req, timeout=25).read().decode("gbk", "ignore")
        except Exception as e:
            log("批次 %d-%d 抓取失败：%s" % (i, i + len(chunk), e))
            continue
        for line in txt.strip().split("\n"):
            if "=" not in line:
                continue
            try:
                f = line.split("=", 1)[1].strip().strip('";').split("~")
                if len(f) < 35:
                    continue
                code = f[2]
                ts = f[30]  # YYYYMMDDHHMMSS
                if not ts.startswith(day):
                    continue
                close = float(f[3]); prev = float(f[4]); op = float(f[5])
                high = float(f[33]); low = float(f[34])
                vol = float(f[36]) if len(f) > 36 and f[36] else 0  # 手
                if not (op > 0 and close > 0 and high >= low > 0):
                    continue
                out[code] = [day, round(op, 2), round(close, 2),
                             round(low, 2), round(high, 2), int(vol * 100)]
            except Exception:
                continue
        time.sleep(0.15)
    return out


def merge_bar(bars, bar):
    """按日期合并：同日覆盖，其余保留，升序。"""
    rec = {b[0]: b for b in bars if b and len(b) >= 2}
    rec[bar[0]] = bar
    return [rec[d] for d in sorted(rec)]


def patch_shards(daybars, day):
    n = 0
    for fp in sorted(glob.glob(os.path.join(KDIR, "klines_*.json"))):
        try:
            obj = json.load(open(fp, encoding="utf-8"))
        except Exception:
            continue
        touched = False
        for c, bar in daybars.items():
            if c in obj:
                old = obj[c]
                if any(b[0] == day for b in old):
                    continue
                obj[c] = merge_bar(old, bar)
                touched = True
                n += 1
        if touched:
            with open(fp, "w", encoding="utf-8") as f:
                json.dump(obj, f, ensure_ascii=False, separators=(",", ":"))
    log("分片补入当日 bar：%d 只" % n)


def main():
    day = sys.argv[1] if len(sys.argv) > 1 else datetime.datetime.now().strftime("%Y%m%d")
    data = jload(DATA_F, None)
    if not data:
        log("数据文件缺失"); return 1

    # 需求个股：当日涨停池全部 + 连板梯队个股
    pool = jload(POOL_F, {})
    need = set()
    for s in (pool.get(day) or []):
        need.add(str(s.get("code") or ""))
    for row in (data.get("lb", {}).get(day) or []):
        need.add(str(row[3] or ""))
    need.discard("")
    log("目标日 %s，需求个股 %d 只（涨停池+连板梯队）" % (day, len(need)))
    if not need:
        log("无需求个股"); return 1

    daybars = fetch_today_bars(need, day)
    log("拿到当日 bar %d 只" % len(daybars))
    if not daybars:
        log("接口未返回当日数据，退出（不改任何文件）"); return 2

    # 1) 缓存
    stock_c = jload(STOCK_F, {})
    for c, bar in daybars.items():
        stock_c[c] = merge_bar(stock_c.get(c) or [], bar)[-600:]
    with open(STOCK_F, "w", encoding="utf-8") as f:
        json.dump(stock_c, f, ensure_ascii=False)
    log("缓存已更新")

    # 2) 分片
    patch_shards(daybars, day)

    # 3) data.json / data.js 的 klines（近 60 根）
    kl = data.get("klines") or {}
    for c, bar in daybars.items():
        kl[c] = merge_bar(kl.get(c) or [], bar)[-60:]
    data["klines"] = kl
    data["klinesAt"] = datetime.datetime.now().strftime("%Y-%m-%d %H:%M")
    with open(DATA_F, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False)
    import datajs
    datajs.write_obj(data, BASE)
    log("data.json / data.js 已同步（klinesAt=%s）" % data["klinesAt"])
    return 0


if __name__ == "__main__":
    sys.exit(main())
