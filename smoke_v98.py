import asyncio
from playwright.async_api import async_playwright

URL = "http://localhost:8765/index.html"

async def main():
    errors = []
    async with async_playwright() as p:
        b = await p.chromium.launch(headless=True)
        ctx = await b.new_context(accept_downloads=True, viewport={"width": 1440, "height": 1000})
        pg = await ctx.new_page()
        pg.on("pageerror", lambda e: errors.append(str(e)))
        await pg.goto(URL, wait_until="networkidle", timeout=30000)
        await pg.wait_for_timeout(1200)

        # ===== 需求1：各模块日期选项单行 =====
        # 检查各 bar 的 flex-wrap 与实际是否单行（子元素 top 基本一致）
        wraps = await pg.evaluate("""() => {
            const sels = ['.hsel','.ch-bar','.ep-bar','.dpick','.trange','.rv-bar'];
            const out = {};
            sels.forEach(s=>{
                const el=document.querySelector(s);
                if(el) out[s]=getComputedStyle(el).flexWrap;
            });
            return out;
        }""")
        print("=== 需求1 日期条 flex-wrap（应为 nowrap）===", wraps)
        assert all(v == 'nowrap' for v in wraps.values()), "有日期条仍换行"

        # 切各页签确认单行且不溢出容器
        for t in ['market', 'theme', 'ladder', 'decision', 'hist']:
            await pg.evaluate("t=>switchTab(t)", t)
            await pg.wait_for_timeout(300)
        await pg.evaluate("switchTab('decision')")
        await pg.wait_for_timeout(600)
        # ep-bar 单行验证：所有直接子元素 top 接近
        bar = await pg.evaluate("""() => {
            const el=document.querySelector('#page-decision .ep-bar');
            const tops=[...el.children].map(c=>Math.round(c.getBoundingClientRect().top));
            return {tops, unique:[...new Set(tops)].length};
        }""")
        print("=== 需求1 今日复盘 ep-bar 子元素top（同居中±5px=单行）===", bar)
        assert max(bar['tops']) - min(bar['tops']) <= 6, "ep-bar 疑似换行"

        # ===== 需求4：跨题材归类折叠 =====
        cross = await pg.evaluate("""() => {
            const d=document.getElementById('ep-cross-wrap');
            return {exists:!!d, open:d?d.open:null, summary:(document.querySelector('.ep-cross-sum')||{}).innerText||'', sub:(document.getElementById('ep-cross-sub')||{}).textContent||''};
        }""")
        print("=== 需求4 跨题材折叠 ===", cross)
        assert cross['exists'] and cross['open'] is False, "跨题材归类未默认折叠"
        # 点击展开
        await pg.click(".ep-cross-sum")
        await pg.wait_for_timeout(300)
        opened = await pg.evaluate("()=>document.getElementById('ep-cross-wrap').open")
        print("=== 需求4 点击后展开 ===", opened)
        assert opened is True

        # ===== 需求2+3：客观特征带出 + 基于填写内容的周总结 =====
        # 清空填报，注入两条不同主题的心得
        await pg.evaluate("()=>localStorage.removeItem('earn_learn_diary')")
        await pg.fill("#elrn-date", "2026-11-20")
        await pg.fill("#elrn-q", "盛剑科技")
        await pg.click("#elrn-search")
        await pg.wait_for_timeout(400)
        await pg.click("#elrn-sugg button")
        await pg.wait_for_timeout(900)
        # 客观标签
        obj = await pg.evaluate("""() => [...document.querySelectorAll('.elrn-obj .ot')].map(x=>x.textContent)""")
        print("=== 需求2 客观特征标签 ===", obj)
        assert any('市场阶段' in x for x in obj), "缺市场阶段标签"
        assert any('助攻' in x or '题材' in x for x in obj), "缺题材情况标签"
        assert any('换手' in x or '形态' in x or '板' in x for x in obj), "缺个股情况标签"
        # 填写心得（情绪+题材主题）
        await pg.click("#elrn-tags button[data-t='板块助攻≥2']")
        await pg.fill("#elrn-note", "半导体主升期情绪变好，板块有助攻，龙头换手回封是买点，题材入链才参与。")
        await pg.click("#elrn-save")
        await pg.wait_for_timeout(400)
        # 第二条：纪律/风险主题
        await pg.fill("#elrn-q", "中京电子")
        await pg.click("#elrn-search")
        await pg.wait_for_timeout(400)
        await pg.click("#elrn-sugg button")
        await pg.wait_for_timeout(700)
        await pg.fill("#elrn-note", "这次犹豫没下手，棉花朵没执行计划，后悔；下次要守纪律、控制仓位，不追高。")
        await pg.click("#elrn-save")
        await pg.wait_for_timeout(400)

        # 周总结归类
        week = await pg.evaluate("""() => ({
            rows: document.querySelectorAll('.elrn-wkr').length,
            summary: (document.querySelector('.elrn-wsum')||{}).innerText||''
        })""")
        print("=== 需求3 周总结（基于填写内容归类）===\n", week['summary'])
        assert '基于你填写的内容' in week['summary'], "周总结未标明基于填写内容"
        assert '情绪周期' in week['summary'] or '题材板块' in week['summary'], "未归类出情绪/题材主题"
        assert '纪律执行' in week['summary'] or '风险控制' in week['summary'], "未归类出纪律/风险主题"

        # 保存的 obj 字段
        recs = await pg.evaluate("()=>Object.values(JSON.parse(localStorage.getItem('earn_learn_diary')||'{}'))")
        print("=== 需求2 保存记录含obj客观标签 ===", [(r['name'], len(r.get('obj', []))) for r in recs])

        print("=== JS报错 ===", errors if errors else "0报错")
        await pg.screenshot(path="/workspace/a-share-review/v98_客观标签与周总结.png", full_page=False)
        await b.close()

asyncio.run(main())
