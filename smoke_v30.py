# -*- coding: utf-8 -*-
"""v30 本地回归：题材炒作图新门槛 / 当日机会精简 / 刷新自动取数 / 战法口径零改动。

用法：先在 /workspace/a-share-review 起 `python3 -m http.server 8899`，再跑本脚本。
"""
import json
import os
from playwright.sync_api import sync_playwright

OUT = "/workspace/a-share-review"
BASE = "http://127.0.0.1:8899"
D = json.load(open(os.path.join(OUT, "data.json"), encoding="utf-8"))
VER = open(os.path.join(OUT, "version.js"), encoding="utf-8").read()
fails, total = [], 0


def ck(c, m):
    global total
    total += 1
    if not c:
        fails.append(m)


with sync_playwright() as p:
    b = p.chromium.launch(headless=True)
    pg = b.new_page(viewport={"width": 1400, "height": 900})
    errs = []
    pg.on("console", lambda m: errs.append(m.text) if m.type == "error" else None)
    pg.on("pageerror", lambda e: errs.append(str(e)))
    pg.goto(BASE + "/?tab=chance", wait_until="networkidle", timeout=90000)
    pg.wait_for_timeout(1200)

    ck(not errs, "控制台错误：%s" % errs[:4])

    # ---- 数据版本指纹 ----
    ver = pg.evaluate("window.DATA_VER||''")
    ck(len(ver) == 12, "version.js 未加载：%r" % ver)
    ck(('"%s"' % ver) in VER, "页面指纹与 version.js 不一致")

    # ---- 战法口径：页面与本地数据基线完全一致（不硬编码历史快照数字） ----
    exp_review = {m: len(v) for m, v in D["review"].items()}
    for m, n in exp_review.items():
        c = pg.evaluate("(window.REVIEW.review['%s']||[]).length" % m)
        ck(c == n, "%s 逐笔应为 %d，实际 %d" % (m, n, c))
    n_ch = sum(len(v) for v in D["chances"].values())
    pg_n = pg.evaluate("Object.values(window.REVIEW.chances).reduce((a,v)=>a+v.length,0)")
    ck(pg_n == n_ch, "页面机会池 %d 与本地 %d 不一致" % (pg_n, n_ch))
    ck(len(D["boardEarn"]) > 0, "boardEarn 缺失")
    print("数据基线：逐笔 %s / 机会池 %d 条 / 题材日 %d 天" %
          (exp_review, n_ch, len(D["themeDates"])))

    # ---- 题材炒作图新门槛：有连板(>=2) 且涨停>=3只 ----
    bad, nblk = 0, 0
    for d, ts in D["themeDetail"].items():
        lad = (D["ladders"] or {}).get(d, {})
        for t in ts:
            nblk += 1
            e = lad.get(t["name"])
            if not e or e["maxB"] < 2 or e["count"] < 3:
                bad += 1
    ck(bad == 0, "题材炒作图有 %d 个块未达新门槛" % bad)
    print("题材日 %d 天 / 题材块 %d 个（全部达标）" % (len(D["themeDates"]), nblk))

    # ---- 当日机会页精简 ----
    ck(pg.locator("#ch-diary").count() == 0, "自省按钮未删除")
    ck(pg.locator("#ch-diary-box").count() == 0, "自省弹窗未删除")
    ck(pg.locator(".cdiary").count() == 0, "自省输入未删除")
    ck(pg.locator(".cwhy").count() == 0, "逐笔理由长文未删除")
    ck(pg.locator(".clogic").count() == 0, "战法完整逻辑长文未删除")
    rows = pg.locator("#ch-list .ch-row")
    ck(rows.count() > 0, "当日机会无标的")
    ck(pg.locator("#ch-list .cl").count() == rows.count(), "战法列数量不符")
    ck(pg.locator("#ch-list .cstk").count() == rows.count(), "标的股数量不符")
    ck(pg.locator("#ch-list .cth").count() == rows.count(), "题材情况数量不符")
    methods = set(pg.locator("#ch-list .cl").all_text_contents())
    ck(methods <= {"空间突破", "试错龙头", "补涨"},
       "战法名与交易复盘不一致：%s" % methods)
    body = pg.locator("#ch-list").inner_text()
    for kw in ("买入 ", "战法完整逻辑", "已参与模拟", "保存自省", "模拟首笔"):
        ck(kw not in body, "机会页残留「%s」" % kw)
    ck("梯队" in body, "未展示题材梯队")
    ck("标的 /" in pg.locator("#ch-sum").inner_text(), "汇总未简化")
    print("机会页 %d 行 · 战法 %s" % (rows.count(), methods))

    # 点梯队 → 复用题材页完整梯队浮层
    lad_el = pg.locator("#ch-list .clad").first
    if lad_el.count():
        lad_el.click()
        pg.wait_for_timeout(300)
        ck(pg.locator("#ladpop").is_visible(), "点梯队未弹出浮层")

    # ---- 题材炒作图渲染 ----
    pg.click('nav button[data-t="theme"]')
    pg.wait_for_timeout(500)
    ck(pg.locator("#theme-list .tcard").count() > 0, "题材炒作图无卡片")

    # ---- 刷新：版本相同不重载；版本变化自动重载 ----
    ck(pg.evaluate("checkNewData()") is False, "版本相同时不应判定为新数据")
    state = {"n": 0}
    def fake_ver(route):
        state["n"] += 1
        body = 'window.DATA_VER="deadbeef0000";' if state["n"] == 1 else VER
        route.fulfill(status=200, content_type="application/javascript", body=body)
    pg.route("**/version.js*", fake_ver)
    navs = []
    pg.on("framenavigated", lambda f: navs.append(f.url))
    pg.click("#refresh-btn")
    pg.wait_for_timeout(2500)
    ck(len(navs) > 0, "检测到新数据但未自动重载")
    ck(state["n"] >= 2, "重载后未重新取 version.js")
    print("刷新自动重载：导航 %d 次 / version.js 请求 %d 次" % (len(navs), state["n"]))
    pg.unroute("**/version.js*")

    b.close()

print("\nv30 本地回归：%d/%d 通过" % (total - len(fails), total))
for f in fails:
    print("  ✗", f)
raise SystemExit(1 if fails else 0)
