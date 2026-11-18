# -*- coding: utf-8 -*-
"""线上真实环境回归：三战法渲染、示范龙头逐笔、无控制台错误。"""
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
    # 数据已解压
    n = pg.evaluate("Object.keys((window.REVIEW&&window.REVIEW.review)||{}).length")
    ck(n == 3, "线上 review 战法数应为3，实际 %s" % n)
    ck(pg.evaluate("!!(window.REVIEW&&window.REVIEW.review&&window.REVIEW.review.demoLead)"), "线上缺 demoLead")
    # 下拉三项
    opts = pg.locator("#rv-method option").all_text_contents()
    ck(len(opts) == 3 and any("示范龙头" in o for o in opts), "线上战法下拉异常 %s" % opts)
    # 对比表三战法
    cmp = pg.locator("#rv-cmp-box").inner_text()
    ck(all(x in cmp for x in ("空间突破","试错龙头","示范龙头")), "线上对比表缺战法")
    # 选战法三回测看明细
    pg.select_option("#rv-method", "demoLead")
    pg.click("#rv-go")
    pg.wait_for_timeout(600)
    tbl = pg.locator("#rv-table").inner_text()
    ck(bool(re.search(r"20\d\d-\d\d-\d\d", tbl)), "线上战法三明细无数据")
    # reviewMeta 三战法 + 示范口径
    uni = pg.evaluate("((window.REVIEW.reviewMeta||{}).demoLead||{}).uni||''")
    ck("示范龙头" in uni and "连续 ≥3 日登赚钱效应榜" in uni, "线上 demoLead 口径缺关键字")
    ck(not errs, "线上控制台错误：%s" % errs[:3])
    print("线上 review 战法数:", n, "| 下拉:", opts)
    print("明细样例:", tbl[:120].replace("\n", " | "))
    print("控制台错误数:", len(errs))
    b.close()

print("\n线上回归结果：%d/%d 通过" % (total-len(fails), total))
for f in fails: print("  ✗", f)
raise SystemExit(1 if fails else 0)
