# -*- coding: utf-8 -*-
"""v29 冒烟：三战法下拉（空间突破/试错龙头/补涨）、全新买入文案、板块赚钱效应验证字段、
对比表、机会池、参与模拟同股不加仓、零控制台错误。"""
import os, subprocess, time, re
from playwright.sync_api import sync_playwright

OUT = os.path.dirname(os.path.abspath(__file__))
PORT = 8903
srv = subprocess.Popen(["python3", "-m", "http.server", str(PORT)],
                       cwd=OUT, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
time.sleep(1.5)
fails, total = [], 0


def ck(c, m):
    global total
    total += 1
    if not c:
        fails.append(m)


try:
    with sync_playwright() as p:
        b = p.chromium.launch(headless=True)
        pg = b.new_page(viewport={"width": 1400, "height": 900})
        errs = []
        pg.on("console", lambda m: errs.append(m.text) if m.type == "error" else None)
        pg.on("pageerror", lambda e: errs.append(str(e)))
        pg.goto("http://127.0.0.1:%d/index.html?tab=review" % PORT,
                wait_until="networkidle", timeout=60000)
        pg.wait_for_timeout(1800)

        # 1 三战法下拉
        opts = pg.locator("#rv-method option").all_text_contents()
        ck(len(opts) == 3, "战法下拉应3项：%s" % opts)
        ck(any("补涨" in o for o in opts), "缺补涨")
        ck(not any("示范" in o for o in opts), "仍残留示范龙头")
        print("战法下拉：", opts)

        # 2 reviewMeta 全新逻辑关键字
        uni = pg.evaluate("((window.REVIEW.reviewMeta||{}).spaceDragon||{}).uni||''")
        for kw in ("v29", "高开但不是涨停开盘", "板块当日有赚钱效应", "低吸/半路/打板",
                   "N+1 板", "昨日节点", "改买板块 2 板+ 换手助攻股",
                   "盘中 −7% 无条件止损", "已清空此前全部战法口径"):
            ck(kw in uni, "uni 缺关键字「%s」" % kw)
        for bad in ("示范龙头", "v27 门槛", "单笔盈利≥5%", "首次符合条件才买入"):
            ck(bad not in uni, "uni 残留已删除口径「%s」" % bad)
        for m, kw in (("spaceDragon", "断板日没有大长阴杀跌"),
                      ("trialLead", "博弈成为新的空间龙头"),
                      ("fillUp", "改买板块助攻换手连板股")):
            buy = pg.evaluate("((window.REVIEW.reviewMeta||{})['%s']||{}).buy||''" % m)
            ck(kw in buy, "%s 买入文案缺「%s」" % (m, kw))
        print("reviewMeta v29 关键字校验完成")

        # 3 三战法逐笔笔数（与 data.json 一致）
        import json
        D = json.load(open(os.path.join(OUT, "data.json"), encoding="utf-8"))
        for m in ("spaceDragon", "trialLead", "fillUp"):
            n = len(D["review"][m])
            c = pg.evaluate("(window.REVIEW.review['%s']||[]).length" % m)
            ck(c == n, "%s 笔数应 %d，实际 %d" % (m, n, c))
        print("三战法笔数：%s ✓" % " / ".join(str(len(D["review"][m])) for m in
                                          ("spaceDragon", "trialLead", "fillUp")))

        # 4 板块赚钱效应验证字段
        be = pg.evaluate("Object.keys(window.REVIEW.boardEarn||{}).length")
        ck(be > 1000, "boardEarn 日数异常 %s" % be)
        nok = pg.evaluate("Object.values(window.REVIEW.boardEarn||{}).reduce((s,v)=>s+Object.values(v).filter(x=>x.ok).length,0)")
        ck(nok > 1500, "boardEarn 通过题材日异常 %s" % nok)
        print("boardEarn：%d 日 / %d 个通过题材日 ✓" % (be, nok))

        # 5 对比表三战法
        cmp = pg.locator("#rv-cmp-box").inner_text()
        for name in ("空间突破", "试错龙头", "补涨"):
            ck(name in cmp, "对比表缺 %s" % name)
        ck("示范龙头" not in cmp, "对比表仍残留示范龙头")

        # 6 参与模拟：同股不加仓生效
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
        print("参与模拟：全部 %d 笔 → 模拟 %d 笔（同股不加仓生效）✓" % (all_n, sim_n))

        # 7 战法三补涨明细有数据
        pg.select_option("#rv-method", "fillUp")
        pg.uncheck("#rv-sim")
        pg.click("#rv-go")
        pg.wait_for_timeout(500)
        tbl = pg.locator("#rv-table").inner_text()
        ck(bool(re.search(r"20\d\d-\d\d-\d\d", tbl)), "战法三明细无数据")
        ck("补涨" in tbl, "补涨明细 why 未显示补涨逻辑")

        # 8 当日机会页：新口径 + 一字触发补涨记录
        pg.evaluate("focusModule('chance')")
        pg.wait_for_timeout(1000)
        sub = pg.evaluate("document.querySelector('#page-chance h3 .sub')?.textContent||''")
        ck("板块须有赚钱效应" in sub and "补涨" in sub, "机会页头部口径未更新")
        chs = pg.evaluate("Object.values(window.REVIEW.chances||{}).flat()")
        ck(any(x.get("result", "").find("触发补涨") >= 0 for x in chs), "机会池缺一字触发补涨记录")
        print("当日机会页口径与补涨触发记录 ✓")

        ck(not errs, "控制台错误：%s" % errs[:3])
        print("控制台错误数：", len(errs))
        b.close()
finally:
    srv.terminate()

print("\n结果：%d/%d 通过" % (total - len(fails), total))
for f in fails:
    print("  ✗", f)
raise SystemExit(1 if fails else 0)
