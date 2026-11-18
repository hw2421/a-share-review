# -*- coding: utf-8 -*-
"""v27 线上真实环境回归：三战法渲染、v27 口径关键字、参与模拟同股不加仓、无控制台错误。"""
import re
from playwright.sync_api import sync_playwright

URL = "https://aebe03640c921d2fe.app.workbuddy.host/?sharecode=LERGP4G4UT&tab=review"
fails, total = [], 0
def ck(c, m):
    global total
    total += 1
    if not c: fails.append(m)

with sync_playwright() as p:
    b = p.chromium.launch(headless=True)
    pg = b.new_page(viewport={"width": 1400, "height": 900})
    errs = []
    pg.on("console", lambda m: errs.append(m.text) if m.type == "error" else None)
    pg.on("pageerror", lambda e: errs.append(str(e)))
    pg.goto(URL, wait_until="networkidle", timeout=90000)
    pg.wait_for_timeout(2000)

    n = pg.evaluate("Object.keys((window.REVIEW&&window.REVIEW.review)||{}).length")
    ck(n == 3, "线上战法数应3：%s" % n)
    for m, exp in (("spaceDragon", 63), ("trialLead", 438), ("demoLead", 21)):
        c = pg.evaluate("(window.REVIEW.review['%s']||[]).length" % m)
        ck(c == exp, "线上 %s 笔数应 %d，实际 %d" % (m, exp, c))
    opts = pg.locator("#rv-method option").all_text_contents()
    ck(len(opts) == 3 and any("示范龙头" in o for o in opts), "线上下拉异常 %s" % opts)

    uni = pg.evaluate("((window.REVIEW.reviewMeta||{}).spaceDragon||{}).uni||''")
    for kw in ("首次符合条件才买入", "不加仓", "单笔盈利≥5%", "单笔亏损≤−10%", "板位 ≤5 板",
               "涨停家数 ≥80", "领涨竞争", "空间高度骤降 ≥4 板", "T 日空间已突破压制"):
        ck(kw in uni, "线上 uni 缺「%s」" % kw)

    cmp = pg.locator("#rv-cmp-box").inner_text()
    for name in ("空间突破", "试错龙头", "示范龙头"):
        ck(name in cmp, "线上对比表缺 %s" % name)

    # 参与模拟：同股不加仓
    pg.select_option("#rv-method", "trialLead")
    pg.click("#rv-all"); pg.wait_for_timeout(400)
    pg.uncheck("#rv-sim"); pg.wait_for_timeout(400)
    all_n = int(re.search(r"本次\s*(\d+)\s*笔", pg.locator("#rv-scope").inner_text()).group(1))
    pg.check("#rv-sim"); pg.wait_for_timeout(500)
    scope = pg.locator("#rv-scope").inner_text()
    sim_n = int(re.search(r"→\s*(\d+)\s*笔", scope).group(1))
    ck(sim_n < all_n, "线上参与模拟未去重 %d/%d" % (sim_n, all_n))
    ck("同股不加仓" in scope, "线上范围文案缺「同股不加仓」")

    ck(not errs, "线上控制台错误：%s" % errs[:3])
    print("线上三战法笔数: 63 / 438 / 21 | 下拉:", opts)
    print("参与模拟: 全部 %d → 模拟 %d（同股不加仓）✓" % (all_n, sim_n))
    print("控制台错误数:", len(errs))
    b.close()

print("\n线上回归：%d/%d 通过" % (total-len(fails), total))
for f in fails: print("  ✗", f)
raise SystemExit(1 if fails else 0)
