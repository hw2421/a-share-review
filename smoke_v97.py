import asyncio
from playwright.async_api import async_playwright

URL = "http://localhost:8765/index.html"

async def main():
    errors = []
    async with async_playwright() as p:
        b = await p.chromium.launch(headless=True)
        ctx = await b.new_context(accept_downloads=True)
        pg = await ctx.new_page()
        pg.on("pageerror", lambda e: errors.append(str(e)))
        await pg.goto(URL, wait_until="networkidle", timeout=30000)
        await pg.wait_for_timeout(1200)
        await pg.evaluate("switchTab('decision')")
        await pg.wait_for_timeout(800)

        # 子模块存在性
        exist = await pg.evaluate("""() => ({
            box: !!document.getElementById('elrn-box'),
            form: !!document.getElementById('elrn-form'),
            week: !!document.getElementById('elrn-week-body')
        })""")
        print("=== 子模块存在 ===", exist)

        # 1) 检索个股
        await pg.fill("#elrn-q", "盛剑科技")
        await pg.click("#elrn-search")
        await pg.wait_for_timeout(600)
        sugg = await pg.evaluate("""() => [...document.querySelectorAll('#elrn-sugg button')].map(b=>({n:b.dataset.n,c:b.dataset.c}))""")
        print("=== 检索结果 ===", sugg[:5])
        assert any(x['c'] == '603324' for x in sugg), "未检索到盛剑科技"

        # 2) 导入（带出日K与题材梯队）
        await pg.click("#elrn-sugg button")
        await pg.wait_for_timeout(900)
        detail = await pg.evaluate("""() => ({
            on: document.getElementById('elrn-detail').classList.contains('on'),
            formOn: document.getElementById('elrn-form').classList.contains('on'),
            text: document.getElementById('elrn-detail').innerText.slice(0,300),
            canvas: !!document.getElementById('elrn-kc'),
            lad: document.querySelectorAll('.elrn-lt').length
        })""")
        print("=== 导入结果 ===", detail)

        # 3) 选标签 + 写心得 + 保存
        await pg.click("#elrn-tags button[data-t='板块助攻≥2']")
        await pg.click("#elrn-tags button[data-t='换手板']")
        await pg.fill("#elrn-note", "盛剑科技半导体4板，板块有助攻，换手回封可参与；学到板块变强+情绪变好时缩量回封是买点。")
        await pg.click("#elrn-save")
        await pg.wait_for_timeout(600)
        saved = await pg.evaluate("""() => {
            const db=JSON.parse(localStorage.getItem('earn_learn_diary')||'{}');
            return {count:Object.keys(db).length, sample:Object.values(db)[0]};
        }""")
        print("=== 保存结果 ===", saved)

        # 4) 周总结表
        week = await pg.evaluate("""() => ({
            rows: document.querySelectorAll('.elrn-wkr').length,
            sub: document.getElementById('elrn-week-sub').textContent,
            summary: (document.querySelector('.elrn-wsum')||{}).innerText||''
        })""")
        print("=== 周总结 ===", week)
        assert week['rows'] >= 1, "周总结无记录行"

        # 5) 第二条：另一天另一只股（验证周聚合/导出）
        await pg.fill("#elrn-q", "中京电子")
        await pg.click("#elrn-search")
        await pg.wait_for_timeout(500)
        await pg.click("#elrn-sugg button")
        await pg.wait_for_timeout(700)
        await pg.click("#elrn-tags button[data-t='题材入链']")
        await pg.fill("#elrn-note", "PCB题材入链，跟风首板不追，只做龙头。")
        await pg.click("#elrn-save")
        await pg.wait_for_timeout(500)
        db2 = await pg.evaluate("()=>Object.keys(JSON.parse(localStorage.getItem('earn_learn_diary')||'{}')).length")
        print("=== 第二条保存后条数 ===", db2)

        # 6) 导出 CSV
        async with pg.expect_download(timeout=15000) as di:
            await pg.click("#elrn-export")
        dl = await di.value
        path = await dl.path()
        with open(path, encoding='utf-8-sig') as f:
            csv = f.read()
        print("=== 导出CSV ===", "文件名:", dl.suggested_filename, "行数:", csv.count('\n'))
        assert '盛剑科技' in csv and '中京电子' in csv, "CSV缺个股"

        # 7) 同会话 reload 持久化
        await pg.reload(wait_until="networkidle")
        await pg.wait_for_timeout(1000)
        await pg.evaluate("switchTab('decision')")
        await pg.wait_for_timeout(700)
        after = await pg.evaluate("""() => ({
            db:Object.keys(JSON.parse(localStorage.getItem('earn_learn_diary')||'{}')).length,
            rows:document.querySelectorAll('.elrn-wkr').length
        })""")
        print("=== reload后持久化 ===", after)
        assert after['db'] == 2, "reload后记录丢失"

        # 8) 周切换 / 页签遍历
        await pg.click("#elrn-wprev")
        await pg.wait_for_timeout(300)
        await pg.click("#elrn-wlatest")
        await pg.wait_for_timeout(300)
        for t in ['market', 'theme', 'ladder', 'hist', 'decision']:
            await pg.evaluate("t=>switchTab(t)", t)
            await pg.wait_for_timeout(250)
        print("=== 周切换+页签遍历通过 ===")

        print("=== JS报错 ===", errors if errors else "0报错")
        await pg.screenshot(path="/workspace/a-share-review/v97_赚钱效应学习.png", full_page=False)
        await b.close()

asyncio.run(main())
