# -*- coding: utf-8 -*-
"""盘中日K刷新（每 30 分钟）：只重抓「近期活跃个股」的日K，保持页面日K最新。
覆盖对象：最近 N 个交易日的空间龙头 / 空间压制股 / 题材龙头。
流程：
  1) 新浪日K（520 根，不复权）逐只刷新 -> .stock_kline_cache.json / .sup_kline_cache.json
  2) sup_klines.refresh 重建压制股断板段；klines 分片按「只增不减」就地打补丁
  3) data.json / data.js 里的 klines（近 60 根）与 supKlines 同步更新
  4) --publish 时发布站点
用法：python3 refresh_klines.py [--recent 12] [--publish] [--force]
"""
import argparse
import datetime
import json
import os
import subprocess
import sys
import time

OUT = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, OUT)
import fetch_data as fd
import sup_klines
import stock_klines

STOCK_F = os.path.join(OUT, ".stock_kline_cache.json")
SUP_F = os.path.join(OUT, ".sup_kline_cache.json")
DATA_F = os.path.join(OUT, "data.json")
DATAJS_F = os.path.join(OUT, "data.js")

log = lambda m: print("[kline %s] %s" % (datetime.datetime.now().strftime("%H:%M:%S"), m),
                      flush=True)


def trading_window():
    """09:25 ~ 15:10（工作日）才需要盘中刷新"""
    now = datetime.datetime.now()
    if now.weekday() >= 5:
        return False
    hm = now.hour * 60 + now.minute
    return 9 * 60 + 25 <= hm <= 15 * 60 + 10


def patch_shards(codes, cache):
    """把刷新后的日K就地写回 klines/klines_*.json：逐桶读写，覆盖只增不减。"""
    import glob
    import stock_klines as SK
    hit = miss = 0
    for fp in sorted(glob.glob(os.path.join(SK.KDIR, "klines_*.json"))):
        b_idx = int(os.path.basename(fp).split("_")[1].split(".")[0])
        try:
            obj = json.load(open(fp, encoding="utf-8"))
        except Exception:
            continue
        touched = False
        for c in codes:
            bars = cache.get(c) or []
            if not bars:
                continue
            if c in obj:
                # 同一日期用新抓的，旧窗口里缓存没有的日期保留原值，再并入最新几日
                cmap = {b[0]: b for b in bars}
                old_bars = obj[c]
                seen = {b[0] for b in old_bars}
                merged = [cmap.get(b[0], b) for b in old_bars]
                merged += [b for b in bars[-15:] if b[0] not in seen]
                merged.sort(key=lambda x: x[0])
                if merged != old_bars:
                    obj[c] = merged
                    touched = True
                hit += 1
            elif SK.bucket_of(c) == b_idx:
                obj[c] = bars[-60:]      # 新进分片的个股：先放近 60 根
                touched = True
                miss += 1
        if touched:
            with open(fp, "w", encoding="utf-8") as f:
                json.dump(obj, f, ensure_ascii=False, separators=(",", ":"))
    print("[kline] 分片补丁：更新 %d 只，新增 %d 只" % (hit, miss), flush=True)


def load_json(p, default):
    try:
        return json.load(open(p, encoding="utf-8"))
    except Exception:
        return default


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--recent", type=int, default=12)
    ap.add_argument("--publish", action="store_true")
    ap.add_argument("--force", action="store_true")
    a = ap.parse_args()

    if not a.force and not trading_window():
        log("非交易时段（09:25-15:10 工作日外），跳过")
        return 0

    data = load_json(DATA_F, None)
    if not data:
        log("缺少 data.json")
        return 1
    dates = data["dates"]
    stages = data["stages"]
    td = data.get("themeDetail", {})
    recent = set(dates[-a.recent:])

    # ---- 需求个股：近期空间龙头 / 压制股 / 题材龙头 ----
    need = set()
    for day in stages:
        if day["date"] not in recent:
            continue
        for s in day.get("spaceFull") or []:
            need.add(s["code"])
        for s in day.get("suppressStocks") or []:
            need.add(s["code"])
    for d, ts in td.items():
        if d not in recent:
            continue
        for t in ts:
            st = t.get("stocks") or []
            if st:
                need.add(max(st, key=lambda x: x["boards"])["code"])
    need.discard("")
    log("近期活跃个股 %d 只（近 %d 个交易日）" % (len(need), a.recent))

    stock_c = load_json(STOCK_F, {})
    sup_c = load_json(SUP_F, {})
    sup_codes = {s["code"] for day in stages for s in (day.get("suppressStocks") or [])}

    # ---- 1) 逐只刷新日K（同花顺实时优先，取不到回落新浪；与本地历史按日期合并）----
    ok = fail = ths_n = 0
    for i, code in enumerate(sorted(need), 1):
        live = fd.ths_kline(code)
        if len(live) >= 2:
            ths_n += 1
            bars = fd.merge_klines(stock_c.get(code) or [], live, 520)
        else:
            bars = fd.sina_kline(code, 520)
        if bars:
            stock_c[code] = bars
            if code in sup_c or code in sup_codes:
                sup_c[code] = bars
            ok += 1
        else:
            fail += 1
        if i % 50 == 0:
            log("  进度 %d/%d（成功 %d / 同花顺 %d）" % (i, len(need), ok, ths_n))
        time.sleep(0.22)
    with open(STOCK_F, "w", encoding="utf-8") as f:
        json.dump(stock_c, f, ensure_ascii=False)
    with open(SUP_F, "w", encoding="utf-8") as f:
        json.dump(sup_c, f, ensure_ascii=False)
    log("日K刷新完成：成功 %d / 失败 %d（其中同花顺 %d，新浪兜底 %d）"
        % (ok, fail, ths_n, ok - ths_n))
    if not ok:
        log("全部失败（接口不可用），保留旧数据")
        return 1

    # ---- 2) 重建压制股断板段 + 就地更新个股日K分片 ----
    # 不再调用 stock_klines.refresh 重切分片：它需要按全期需求集重算窗口，
    # 而这里只有近 N 个交易日的需求集，重切会裁掉历史窗口（且要整份重载
    # 380MB 缓存，8GB 上限下容易 OOM）。改为按 code 就地打补丁：旧窗口日期
    # 全部保留，只把最新 15 根的日期并进来，覆盖只增不减。
    sup = sup_klines.refresh(dates, stages)
    patch_shards(need, stock_c)

    # ---- 3) 同步 data.json / data.js ----
    kl = data.get("klines") or {}
    for code in need:
        bars = stock_c.get(code) or []
        if bars:
            kl[code] = bars[-60:]
    data["klines"] = kl
    data["supKlines"] = sup
    data["klinesAt"] = datetime.datetime.now().strftime("%Y-%m-%d %H:%M")
    with open(DATA_F, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False)
    import datajs
    datajs.write_obj(data, OUT)
    log("data.json / data.js 已更新（klinesAt=%s）" % data["klinesAt"])

    # ---- 4) 发布 ----
    if a.publish:
        r = subprocess.run(["node", "/root/.codebuddy/skills/sites/scripts/publish.js",
                            "--dir", OUT, "--language", "static"],
                           capture_output=True, text=True, timeout=240)
        good = '"verified":true' in (r.stdout or "")
        log("发布：%s" % ("成功" if good else ("失败 " + (r.stdout or r.stderr or "")[-200:])))
        if not good:
            return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
