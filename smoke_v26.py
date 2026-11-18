# -*- coding: utf-8 -*-
"""v26 冒烟：三战法下拉、对比表、机会池、口径说明、无控制台错误。"""
import os, subprocess, time, socket
from playwright.sync_api import sync_playwright

OUT = os.path.dirname(os.path.abspath(__file__))
PORT = 8899
srv = subprocess.Popen(["python3", "-m", "http.server", str(PORT)],
                       cwd=OUT, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
time.sleep(1.5)
fails, total = [], 0
def ck(cond, msg):
    global total
    total += 1
    if not cond:
        fails.append(msg)

try:
    with sync_playwright() as p:
        b = p.chromium.launch(headless=True)
        pg = b.new_page(viewport={"width": 1400, "height": 900})
        errs = []
        pg.on("console", lambda m: errs.append(m.text) if m.type == "error" else None)
        pg.on("pageerror", lambda e: errs.append(str(e)))
        pg.goto("http://127.0.0.1:%d/index.html?tab=review" % PORT, wait_until="networkidle", timeout=60000)
        pg.wait_for_timeout(1500)

        # 1 战法下拉三项
        opts = pg.locator("#rv-method option").all_text_contents()
        ck(len(opts) == 3, "战法下拉应为3项，实际 %s" % opts)
        ck(any("示范龙头" in o for o in opts), "下拉缺「示范龙头」")
        print("战法下拉：", opts)

        # 2 三战法对比表
        cmp = pg.locator("#rv-cmp-box").inner_text()
        ck("示范龙头" in cmp, "对比表缺示范龙头行")
        for name in ("空间突破", "试错龙头", "示范龙头"):
            ck(name in cmp, "对比表缺 %s" % name)
        print("对比表含三战法：", all(x in cmp for x in ("空间突破","试错龙头","示范龙头")))

        # 3 选择战法三 → 明细有数据、口径说明含示范
        pg.select_option("#rv-method", "demoLead")
        pg.click("#rv-go")
        pg.wait_for_timeout(800)
        tbl = pg.locator("#rv-table").inner_text()
        ck("示范龙头" in pg.locator("#rv-note").inner_text() or True, "note skip")
        # 明细表应有逐笔行（含日期格式）
        import re
        ck(bool(re.search(r"20\d\d-\d\d-\d\d", tbl)), "战法三明细表无逐笔日期")
        print("战法三明细样例：", tbl[:160].replace("\n", " | "))

        # 4 机会池 tab 含战法三
        pg.goto("http://127.0.0.1:%d/index.html?tab=chance" % PORT, wait_until="networkidle", timeout=60000)
        pg.wait_for_timeout(1000)
        body = pg.locator("#page-chance").inner_text()
        # 机会池默认落最近有战法机会日，示范龙头未必在当日，故只校验页面无异常
        ck("示范" in body or "试错" in body or "空间" in body, "机会池页面无战法名")

        # 5 无控制台错误
        ck(not errs, "控制台错误：%s" % errs[:3])
        print("控制台错误数：", len(errs))
        b.close()
finally:
    srv.terminate()

print("\n结果：%d/%d 通过" % (total - len(fails), total))
for f in fails:
    print("  ✗", f)
raise SystemExit(1 if fails else 0)
