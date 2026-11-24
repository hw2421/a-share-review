# -*- coding: utf-8 -*-
"""v116b：自动确保最新交易日的日K齐全（盘后 build_merged 前置步骤）。

背景：refresh_klines.py 用同花顺/新浪接口抓日K，对「当日」滞后（收盘后一段时间才落库），
导致当日 gapMap/涨停形态/赚钱效应判定全空，页面呈现「今日没更新」。
fill_today_kline.py 用腾讯实时接口（收盘即有完整 OHLC）能可靠补当日 bar，
但此前从未被自动流程调用。本脚本把它接入自动链路：

  1) 取涨停池最新交易日；若非今日或未到收盘后(15:05)则跳过；
  2) 检查当日涨停池/连板梯队个股在 .stock_kline_cache.json 的当日 bar 覆盖率；
  3) 覆盖率 < 98% 则调用 fill_today_kline.py <today> 用腾讯接口补齐。

只在收盘后执行（避免盘中实时价污染当日 bar）。幂等：已齐全则跳过，不重复抓。
用法：python3 ensure_today_kline.py [--force]
"""
import datetime, json, os, subprocess, sys

BASE = os.path.dirname(os.path.abspath(__file__))


def jload(p, default):
    try:
        return json.load(open(p, encoding="utf-8"))
    except Exception:
        return default


def main():
    force = "--force" in sys.argv
    now = datetime.datetime.now()
    # 盘中实时价未定，禁止补当日 bar；留 5 分钟缓冲
    if not force and (now.hour < 15 or (now.hour == 15 and now.minute < 5)):
        print("ensure_today_kline: 未到收盘后(%02d:%02d)，跳过" % (now.hour, now.minute))
        return 0

    pc = jload(os.path.join(BASE, ".pool_cache.json"), {})
    today = max(pc) if pc else ""
    if not today:
        print("ensure_today_kline: 无涨停池数据，跳过")
        return 0
    # 最新交易日必须是今天（周末/节假日无当日实时数据可补）
    if not force and today != now.strftime("%Y%m%d"):
        print("ensure_today_kline: 最新交易日 %s 非今日，跳过" % today)
        return 0

    data = jload(os.path.join(BASE, "data.json"), {})
    need = set()
    for s in (pc.get(today) or []):
        need.add(str(s.get("code") or ""))
    for c in (data.get("dayZT", {}).get(today) or {}):
        need.add(str(c))
    for row in (data.get("lb", {}).get(today) or []):
        need.add(str(row[3] or ""))
    need.discard("")
    if not need:
        print("ensure_today_kline: %s 无需求个股，跳过" % today)
        return 0

    stock_c = jload(os.path.join(BASE, ".stock_kline_cache.json"), {})
    have = 0
    for c in need:
        bars = stock_c.get(c) or []
        if any(b and len(b) >= 1 and b[0] == today for b in bars):
            have += 1
    rate = have / len(need)
    print("ensure_today_kline: %s 需求 %d 只，已有当日bar %d 只（%.1f%%）"
          % (today, len(need), have, rate * 100))
    if rate >= 0.98 and not force:
        print("ensure_today_kline: 当日日K已齐全，跳过补抓")
        return 0

    print("ensure_today_kline: 当日日K缺失，调用腾讯实时接口补齐…")
    try:
        r = subprocess.run([sys.executable, os.path.join(BASE, "fill_today_kline.py"), today],
                           capture_output=True, text=True, timeout=600)
        out = (r.stdout or r.stderr or "").strip().splitlines()
        print("fill_today: " + (out[-1] if out else "无输出"))
        if r.returncode != 0:
            print("ensure_today_kline: 补抓失败（rc=%d），保留旧数据" % r.returncode)
            return r.returncode
    except Exception as e:
        print("ensure_today_kline: 补抓异常 %s，保留旧数据" % e)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
