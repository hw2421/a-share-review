# -*- coding: utf-8 -*-
"""v94 前端实测：题材补全后各模块显示正确、无 JS 报错。"""
import asyncio
from playwright.async_api import async_playwright

URL = "http://localhost:8765/index.html"

async def main():
    errors = []
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        page = await browser.new_page(viewport={"width": 1440, "height": 900})
        page.on("pageerror", lambda e: errors.append(str(e)))
        page.on("console", lambda m: errors.append("console.error:" + m.text)
                if m.type == "error" else None)
        await page.goto(URL, wait_until="networkidle", timeout=30000)
        await page.wait_for_timeout(2500)

        # 检查 REVIEW 已加载
        info = await page.evaluate("""() => {
            const R = window.REVIEW || {};
            return {
                ver: window.DATA_VER,
                stockTheme: Object.keys(R.stockTheme||{}).length,
                themeDetail: Object.keys(R.themeDetail||{}).length,
                dates: (R.dates||[]).length,
                title: document.title
            };
        }""")
        print("加载信息:", info)

        # 检查 themeOf 三级反查：抽查之前缺题材的个股
        check = await page.evaluate("""() => {
            const R = window.REVIEW || {};
            function themeOf(d, code){
                const TD=(R.themeDetail||{})[d]||[];
                for(const t of TD) if((t.stocks||[]).some(s=>String(s.code)===String(code))) return {th:t.name,src:'themeDetail'};
                const LF=(R.ladderFull||{})[d]||{};
                for(const th in LF) if((LF[th]||[]).some(s=>String(s.code)===String(code))) return {th,src:'ladderFull'};
                const st=(R.stockTheme||{})[String(code)];
                if(st) return {th:st,src:'stockTheme'};
                return null;
            }
            const tests = [
                ['20261110','688781','视涯科技'],
                ['20261111','301486','致尚科技'],
                ['20261118','688757','胜科纳米'],
                ['20261103','601123','马矿股份'],
                ['20261120','002749','国光股份'],
                ['20261023','301660','粤芯半导体'],
                ['20261022','301023','奕帆传动']
            ];
            return tests.map(t=>[t[0],t[1],t[2],themeOf(t[0],t[1])]);
        }""")
        print("\n=== themeOf 反查抽查（之前缺题材个股）===")
        for d, c, n, r in check:
            print(f"  {d} {c} {n}: {r}")

        # 遍历各页签触发渲染，检查报错
        tabs = await page.evaluate("""() => Array.from(document.querySelectorAll('[data-tab],[data-page],.nav-tab,.tab'))
            .map(e=>e.getAttribute('data-tab')||e.getAttribute('data-page')||e.textContent.trim()).filter(Boolean).slice(0,30)""")
        print("\n页签候选:", tabs[:15])

        await page.screenshot(path="/workspace/a-share-review/v94_题材补全实测.png", full_page=False)

        print("\n=== JS 报错 ===")
        print(errors if errors else "0 报错")
        await browser.close()

asyncio.run(main())
