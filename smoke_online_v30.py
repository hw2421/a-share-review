# -*- coding: utf-8 -*-
"""v30 线上真实环境回归：新包一致性 / 题材门槛 / 机会页精简 / 刷新链路 / 零控制台错误。"""
import hashlib
import json
import os
from playwright.sync_api import sync_playwright

OUT = "/workspace/a-share-review"
URL = "https://aebe03640c921d2fe.app.workbuddy.host/?sharecode=LERGP4G4UT&tab=chance"
LOCAL_VER = open(os.path.join(OUT, "version.js"), encoding="utf-8").read()
LOCAL_SHA = {
    f: hashlib.sha256(open(os.path.join(OUT, f), "rb").read()).hexdigest()
    for f in ("data.js", "index.html", "version.js")
}
LOCAL_D = json.load(open(os.path.join(OUT, "data.json"), encoding="utf-8"))
LOCAL_REVIEW = {m: len(v) for m, v in LOCAL_D["review"].items()}
LOCAL_N_CH = sum(len(v) for v in LOCAL_D["chances"].values())
LOCAL_N_DAYS = len(LOCAL_D["themeDates"])
fails, total = [], 0


def ck(c, m):
    global total
    total += 1
    if not c:
        fails.append(m)


SHA_JS = """async (f) => {
  const r = await fetch(f + '?ts=' + Date.now(), {cache:'no-store'});
  if(!r.ok) return 'HTTP ' + r.status;
  const t = await r.text();
  const b = await crypto.subtle.digest('SHA-256', new TextEncoder().encode(t));
  return Array.from(new Uint8Array(b)).map(x=>x.toString(16).padStart(2,'0')).join('');
}"""

with sync_playwright() as p:
    b = p.chromium.launch(headless=True)
    pg = b.new_page(viewport={"width": 1400, "height": 900})
    errs = []
    pg.on("console", lambda m: errs.append(m.text) if m.type == "error" else None)
    pg.on("pageerror", lambda e: errs.append(str(e)))
    pg.goto(URL, wait_until="networkidle", timeout=90000)
    pg.wait_for_timeout(1800)

    ck(not errs, "线上控制台错误：%s" % errs[:4])
    ck(pg.evaluate("typeof window.checkNewData") == "function",
       "线上 index.html 不是 v30 新版（缺 checkNewData）")

    # 三个静态文件与本地逐字节一致
    for f in ("data.js", "index.html", "version.js"):
        online = pg.evaluate(SHA_JS, f)
        ck(online == LOCAL_SHA[f], "线上 %s 与本地不一致：%s" % (f, online[:16]))
    ver = pg.evaluate("window.DATA_VER||''")
    ck(('"%s"' % ver) in LOCAL_VER, "线上数据指纹异常：%r" % ver)
    print("线上指纹 DATA_VER =", ver)

    # 战法口径：线上与本地数据基线完全一致
    for m, n in LOCAL_REVIEW.items():
        c = pg.evaluate("(window.REVIEW.review['%s']||[]).length" % m)
        ck(c == n, "线上 %s 逐笔应为 %d，实际 %d" % (m, n, c))
    n_days = pg.evaluate("window.REVIEW.themeDates.length")
    n_ch = pg.evaluate("Object.values(window.REVIEW.chances).reduce((a,v)=>a+v.length,0)")
    ck(n_days == LOCAL_N_DAYS, "线上题材日应 %d，实际 %d" % (LOCAL_N_DAYS, n_days))
    ck(n_ch == LOCAL_N_CH, "线上机会池应 %d 条，实际 %d" % (LOCAL_N_CH, n_ch))

    # 题材炒作图新门槛（线上数据全量）
    bad = pg.evaluate("""() => {
      const R=window.REVIEW, L=R.ladders||{}; let bad=0, n=0;
      Object.keys(R.themeDetail).forEach(d=>R.themeDetail[d].forEach(t=>{
        n++; const e=(L[d]||{})[t.name];
        if(!e || e.maxB<2 || e.count<3) bad++;
      }));
      return {bad, n};
    }""")
    ck(bad["bad"] == 0, "线上题材图有 %d 个块未达新门槛" % bad["bad"])
    print("线上题材日 %d 天 / 题材块 %d 个（全部达标）" % (n_days, bad["n"]))

    # 当日机会页精简
    ck(pg.locator("#ch-diary").count() == 0, "线上自省按钮未删除")
    ck(pg.locator("#ch-diary-box").count() == 0, "线上自省弹窗未删除")
    ck(pg.locator(".cdiary").count() == 0, "线上自省输入未删除")
    ck(pg.locator(".cwhy").count() == 0, "线上 why 长文未删除")
    ck(pg.locator(".clogic").count() == 0, "线上战法长文未删除")
    rows = pg.locator("#ch-list .ch-row")
    ck(rows.count() > 0, "线上机会页无标的")
    ck(pg.locator("#ch-list .cl").count() == rows.count(), "线上战法列缺失")
    ck(pg.locator("#ch-list .cstk").count() == rows.count(), "线上标的股缺失")
    ck(pg.locator("#ch-list .cth").count() == rows.count(), "线上题材情况缺失")
    methods = set(pg.locator("#ch-list .cl").all_text_contents())
    ck(methods <= {"空间突破", "试错龙头", "补涨"}, "线上战法名不一致：%s" % methods)
    body = pg.locator("#ch-list").inner_text()
    for kw in ("买入 ", "战法完整逻辑", "已参与模拟", "保存自省"):
        ck(kw not in body, "线上机会页残留「%s」" % kw)
    print("线上机会页 %d 行 · 战法 %s" % (rows.count(), methods))

    # 题材炒作图渲染 + 梯队浮层
    lad_el = pg.locator("#ch-list .clad").first
    if lad_el.count():
        lad_el.click(); pg.wait_for_timeout(300)
        ck(pg.locator("#ladpop").is_visible(), "线上点梯队未弹出浮层")
    pg.click('nav button[data-t="theme"]'); pg.wait_for_timeout(500)
    ck(pg.locator("#theme-list .tcard").count() > 0, "线上题材炒作图无卡片")

    # 刷新按钮：版本相同 → 不重载；点击后能结束并恢复可用
    ck(pg.evaluate("window.checkNewData()") is False,
       "线上版本一致时误判为新数据")
    navs = []
    pg.on("framenavigated", lambda f: navs.append(f.url))
    pg.click("#refresh-btn")
    pg.wait_for_function("!document.getElementById('refresh-btn').disabled",
                         timeout=20000)
    ck(len(navs) == 0, "线上版本未变却触发了重载")
    ck(pg.locator("#refresh-btn").is_enabled(), "线上刷新按钮未恢复可用")
    print("线上刷新：按钮已恢复，无异常重载")

    b.close()

print("\nv30 线上回归：%d/%d 通过" % (total - len(fails), total))
for f in fails:
    print("  ✗", f)
raise SystemExit(1 if fails else 0)
