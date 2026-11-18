# -*- coding: utf-8 -*-
"""v31 本地回归：两战法（空间突破 / 试错龙头）、补涨已删除、后端一键刷新。

前置：python3 app.py 8900（单端口应用，含 /api/*）
用法：python3 smoke_v31.py [http://127.0.0.1:8900/]
"""
import json
import sys
import time

from playwright.sync_api import sync_playwright

BASE = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8900/"
OUT = "/workspace/a-share-review"
D = json.load(open(OUT + "/data.json", encoding="utf-8"))

ok_n = 0
bad = []


def ck(cond, msg):
    global ok_n
    if cond:
        ok_n += 1
    else:
        bad.append(msg)
        print("  ✗", msg)


with sync_playwright() as p:
    b = p.chromium.launch(headless=True)
    pg = b.new_page()
    errs, nav = [], []
    pg.on("console", lambda m: errs.append(m.text) if m.type == "error" else None)
    pg.on("pageerror", lambda e: errs.append(str(e)))
    pg.on("framenavigated", lambda f: nav.append(f.url) if f == pg.main_frame else None)

    pg.goto(BASE, wait_until="domcontentloaded", timeout=90000)
    pg.wait_for_function("window.REVIEW && window.REVIEW.dates", timeout=90000)
    time.sleep(1.0)

    # ---- 数据口径 ----
    methods = pg.evaluate("Object.keys(window.REVIEW.review)")
    ck(set(methods) == {"spaceDragon", "trialLead"}, "战法集合应为两战法，实际 %s" % methods)
    ck("fillUp" not in (pg.evaluate("window.REVIEW.reviewMeta") or {}), "reviewMeta 仍含 fillUp")
    for m, n in (("spaceDragon", len(D["review"]["spaceDragon"])),
                 ("trialLead", len(D["review"]["trialLead"]))):
        c = pg.evaluate("(window.REVIEW.review['%s']||[]).length" % m)
        ck(c == n, "%s 逐笔应为 %d，实际 %d" % (m, n, c))
    n_ch = pg.evaluate("Object.values(window.REVIEW.chances).reduce((a,v)=>a+v.length,0)")
    ck(n_ch == sum(len(v) for v in D["chances"].values()),
       "机会池条数与数据不一致：%d" % n_ch)
    mbuy = pg.evaluate("(window.REVIEW.reviewMeta.spaceDragon||{}).buy||''")
    ck("昨日（T）板块已有助攻股" in mbuy, "战法一买入文案未体现「昨日板块有助攻」")
    ck("高开" not in mbuy, "战法一买入文案仍含已删除的「高开」条件")
    sd = D["review"]["spaceDragon"]
    ck(bool(sd) and all("昨日助攻" in str(t.get("why") or "") for t in sd),
       "战法一逐笔 why 缺「昨日助攻」留痕")
    ck(all(("赚钱效应" not in str(t.get("why") or "")) and
           ("断板" not in str(t.get("why") or "")) for t in sd),
       "战法一逐笔 why 仍残留已删除条件")
    ck(all(not str(o.get("result", "")).find("触发补涨") >= 0
           for v in D["chances"].values() for o in v), "机会池仍有「触发补涨」记录")

    # ---- 交易复盘页：下拉只有两项 ----
    pg.evaluate("switchTab('review')")
    time.sleep(0.6)
    opts = pg.eval_on_selector_all("#rv-method option", "els=>els.map(e=>e.textContent)")
    ck(len(opts) == 2 and not any("补涨" in o for o in opts), "战法下拉应只有两项：%s" % opts)
    ck(all("空间突破" in o or "试错龙头" in o for o in opts), "下拉名称不符：%s" % opts)
    body = pg.inner_text("#page-review")
    ck("补涨" not in body, "交易复盘页仍出现「补涨」")

    # ---- 当日机会页：两战法、无补涨、含题材梯队 ----
    pg.evaluate("switchTab('chance')")
    time.sleep(0.6)
    ch = pg.inner_text("#page-chance")
    ck("补涨" not in ch, "当日机会页仍出现「补涨」")
    ck("空间突破" in ch or "试错龙头" in ch or "无符合" in ch, "机会页未渲染战法名")
    ck(pg.eval_on_selector_all("#ch-list .ch-row", "e=>e.length") >= 0, "机会列表异常")

    # ---- 刷新按钮：走 /api/refresh 并自动重载 ----
    st = pg.evaluate("""async()=>{const r=await fetch('api/status');return await r.json();}""")
    ck(bool(st.get("server")), "后端 /api/status 不可用")
    url_before = pg.url
    reloaded = False
    try:
        with pg.expect_navigation(wait_until="domcontentloaded", timeout=180000):
            pg.click("#refresh-btn")
        reloaded = True
    except Exception:
        reloaded = pg.url != url_before
    ck(reloaded, "刷新后未自动重载（无导航）")
    ck("v=" in pg.url, "重载 URL 未带数据版本参数：%s" % pg.url)
    pg.wait_for_function("window.REVIEW && window.REVIEW.dates", timeout=90000)
    time.sleep(1.2)
    ck(pg.evaluate("Object.keys(window.REVIEW.review).length") == 2, "重载后战法集合异常")
    ck(pg.evaluate("!!document.querySelector('#page-chance')"), "重载后页面结构缺失")

    ck(not errs, "控制台错误：%s" % errs[:3])
    print("控制台错误数：%d；导航次数：%d" % (len(errs), len(nav)))
    b.close()

print("\nv31 本地回归：%d 通过 / %d 失败" % (ok_n, len(bad)))
if bad:
    for m in bad:
        print(" -", m)
    sys.exit(1)
