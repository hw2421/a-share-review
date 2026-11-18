# -*- coding: utf-8 -*-
"""v27 冒烟：三战法下拉、对比表、口径说明含 v27 关键字、参与模拟「同股不加仓」生效、无控制台错误。"""
import os, subprocess, time, re
from playwright.sync_api import sync_playwright

OUT = os.path.dirname(os.path.abspath(__file__))
PORT = 8901
srv = subprocess.Popen(["python3", "-m", "http.server", str(PORT)],
                       cwd=OUT, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
time.sleep(1.5)
fails, total = [], 0
def ck(c, m):
    global total
    total += 1
    if not c: fails.append(m)

try:
    with sync_playwright() as p:
        b = p.chromium.launch(headless=True)
        pg = b.new_page(viewport={"width": 1400, "height": 900})
        errs = []
        pg.on("console", lambda m: errs.append(m.text) if m.type == "error" else None)
        pg.on("pageerror", lambda e: errs.append(str(e)))
        pg.goto("http://127.0.0.1:%d/index.html?tab=review" % PORT, wait_until="networkidle", timeout=60000)
        pg.wait_for_timeout(1500)

        # 1 三战法下拉
        opts = pg.locator("#rv-method option").all_text_contents()
        ck(len(opts) == 3, "战法下拉应3项：%s" % opts)
        ck(any("示范龙头" in o for o in opts), "缺示范龙头")
        print("战法下拉：", opts)

        # 2 reviewMeta 含 v27 关键字
        uni = pg.evaluate("((window.REVIEW.reviewMeta||{}).spaceDragon||{}).uni||''")
        for kw in ("首次符合条件才买入", "不加仓", "单笔盈利≥5%", "单笔亏损≤−10%",
                   "板位 ≤5 板", "涨停家数 ≥80", "领涨竞争", "空间高度骤降 ≥4 板",
                   "T 日空间已突破压制", "板块最高板豁免"):
            ck(kw in uni, "uni 缺关键字「%s」" % kw)
        for m in ("spaceDragon", "trialLead", "demoLead"):
            b_ = pg.evaluate("((window.REVIEW.reviewMeta||{})['%s']||{}).buy||''" % m)
            ck("v27" in b_, "%s 买入文案缺 v27" % m)
        print("reviewMeta v27 关键字校验完成")

        # 3 三战法逐笔笔数
        for m, n in (("spaceDragon", 63), ("trialLead", 438), ("demoLead", 21)):
            c = pg.evaluate("(window.REVIEW.review['%s']||[]).length" % m)
            ck(c == n, "%s 笔数应 %d，实际 %d" % (m, n, c))
        print("三战法笔数：63 / 438 / 21 ✓")

        # 4 对比表三战法
        cmp = pg.locator("#rv-cmp-box").inner_text()
        for name in ("空间突破", "试错龙头", "示范龙头"):
            ck(name in cmp, "对比表缺 %s" % name)

        # 5 参与模拟：同股不加仓 —— 试错龙头逐笔中同股多次出现，开启模拟后笔数应减少
        pg.select_option("#rv-method", "trialLead")
        pg.click("#rv-all")
        pg.wait_for_timeout(400)
        pg.uncheck("#rv-sim")
        pg.wait_for_timeout(400)
        all_n = int(re.search(r"本次\s*(\d+)\s*笔", pg.locator("#rv-scope").inner_text()).group(1))
        pg.check("#rv-sim")
        pg.wait_for_timeout(500)
        scope = pg.locator("#rv-scope").inner_text()
        sim_n = int(re.search(r"→\s*(\d+)\s*笔", scope).group(1))
        ck(sim_n < all_n, "参与模拟后笔数未减少（%d vs %d）" % (sim_n, all_n))
        ck("同股不加仓" in scope, "范围文案缺「同股不加仓」")
        print("参与模拟：全部 %d 笔 → 模拟 %d 笔（同股不加仓生效）✓" % (all_n, sim_n))

        # 6 战法三明细有数据
        pg.select_option("#rv-method", "demoLead")
        pg.uncheck("#rv-sim")
        pg.click("#rv-go")
        pg.wait_for_timeout(500)
        tbl = pg.locator("#rv-table").inner_text()
        ck(bool(re.search(r"20\d\d-\d\d-\d\d", tbl)), "战法三明细无数据")

        ck(not errs, "控制台错误：%s" % errs[:3])
        print("控制台错误数：", len(errs))
        b.close()
finally:
    srv.terminate()

print("\n结果：%d/%d 通过" % (total-len(fails), total))
for f in fails: print("  ✗", f)
raise SystemExit(1 if fails else 0)
