import asyncio
from playwright.async_api import async_playwright

URL = "http://localhost:8765/index.html"

async def main():
    errors = []
    async with async_playwright() as p:
        b = await p.chromium.launch(headless=True)
        ctx = await b.new_context(viewport={"width": 1440, "height": 1000})
        pg = await ctx.new_page()
        pg.on("pageerror", lambda e: errors.append(str(e)))
        await pg.goto(URL, wait_until="networkidle", timeout=30000)
        await pg.wait_for_timeout(1500)

        print("=== 需求1：日期两行布局 ===")
        for tab, bpick in [("market", "dp-market"), ("theme", "dp-theme")]:
            await pg.evaluate("t=>switchTab(t)", tab)
            await pg.wait_for_timeout(500)
            r = await pg.evaluate("""(id)=>{
                const h=document.getElementById(id);
                if(!h) return {err:'no host'};
                const rows=h.querySelectorAll(':scope > .dbar-r');
                const r1=h.querySelector(':scope > .dbar-r:not(.dbar-r2)');
                const r2=h.querySelector(':scope > .dbar-r2');
                return {
                  flexWrap:getComputedStyle(h).flexWrap,
                  rows:rows.length,
                  row1:r1?r1.innerText.trim().slice(0,60):null,
                  row2:r2?r2.innerText.trim():null,
                  hasPrev:!!h.querySelector('.dp-prev'),
                  hasToday:!!h.querySelector('.dp-today'),
                  hasNext:!!h.querySelector('.dp-next')
                };
            }""", bpick)
            print(f"  [{tab}] {r}")

        for tab, bar in [("ladder", None), ("decision", None), ("hist", None)]:
            await pg.evaluate("t=>switchTab(t)", tab)
            await pg.wait_for_timeout(400)
            r = await pg.evaluate("""(t)=>{
                const page=document.getElementById('page-'+t);
                const bar=page.querySelector('.dbar');
                if(!bar) return {err:'no dbar'};
                const r1=bar.querySelector(':scope > .dbar-r:not(.dbar-r2)');
                const r2=bar.querySelector(':scope > .dbar-r2');
                return {
                  rows:bar.querySelectorAll(':scope > .dbar-r').length,
                  row1:r1?r1.innerText.trim().slice(0,70):null,
                  row2:r2?r2.innerText.trim():null,
                  todayLabel:(r2&&r2.querySelector('.today'))?r2.querySelector('.today').innerText:null
                };
            }""", tab)
            print(f"  [{tab}] {r}")

        # 测试 dpick 上一/今日/下一按钮可点击并改变日期
        await pg.evaluate("switchTab('market')")
        await pg.wait_for_timeout(400)
        before = await pg.evaluate("()=>document.querySelector('#dp-market .dp-now').innerText")
        await pg.click("#dp-market .dp-prev")
        await pg.wait_for_timeout(500)
        after_prev = await pg.evaluate("()=>document.querySelector('#dp-market .dp-now').innerText")
        await pg.click("#dp-market .dp-today")
        await pg.wait_for_timeout(500)
        after_today = await pg.evaluate("()=>document.querySelector('#dp-market .dp-now').innerText")
        print(f"  dp-market 日期切换: {before} -> prev {after_prev} -> today {after_today}")

        print("\n=== 需求2：介绍 i 折叠 ===")
        await pg.evaluate("switchTab('theme')")
        await pg.wait_for_timeout(500)
        r = await pg.evaluate("""()=>{
            const h=document.querySelector('#page-theme h3');
            const sub=h.querySelector(':scope > .sub');
            const btn=h.querySelector(':scope > .info-i');
            return {
              hasIntro:h.classList.contains('has-intro'),
              hasBtn:!!btn,
              btnText:btn?btn.innerText:null,
              subDisplay:sub?getComputedStyle(sub).display:null,
              subText:sub?sub.innerText.slice(0,30):null
            };
        }""")
        print(f"  初始(收起): {r}")
        await pg.click("#page-theme h3 .info-i")
        await pg.wait_for_timeout(300)
        r2 = await pg.evaluate("""()=>{
            const h=document.querySelector('#page-theme h3');
            const sub=h.querySelector(':scope > .sub');
            return {open:h.classList.contains('open'), subDisplay:getComputedStyle(sub).display};
        }""")
        print(f"  点击后(展开): {r2}")
        # 今日复盘子模块 i（重绘后补回）
        await pg.evaluate("switchTab('decision')")
        await pg.wait_for_timeout(600)
        r3 = await pg.evaluate("""()=>{
            const hs=document.querySelectorAll('#page-decision h4.ep-h');
            let withSub=0, withBtn=0;
            hs.forEach(h=>{ if(h.querySelector(':scope > .sub')) withSub++;
                            if(h.querySelector(':scope > .info-i')) withBtn++; });
            return {h4Count:hs.length, withSub, withBtn};
        }""")
        print(f"  今日复盘 h4: {r3}")

        print("\n=== 需求3：子模块底色+字体色 ===")
        r4 = await pg.evaluate("""()=>{
            const out={};
            for(let i=1;i<=8;i++){
              const el=document.querySelector('.smod-'+i);
              if(!el){out['smod-'+i]='MISSING';continue;}
              const bg=getComputedStyle(el).backgroundColor;
              const border=getComputedStyle(el).borderTopColor;
              const h4=el.querySelector('.ep-h');
              const color=h4?getComputedStyle(h4).color:null;
              out['smod-'+i]={bg,border,titleColor:color};
            }
            return out;
        }""")
        for k, v in r4.items():
            print(f"  {k}: {v}")

        print("\n=== 回归：切换各页签 ===")
        for t in ["market", "theme", "ladder", "decision", "hist"]:
            await pg.evaluate("t=>switchTab(t)", t)
            await pg.wait_for_timeout(250)
        print("  5 页签切换完成")

        print("\nJS报错:", errors if errors else "0报错")
        await pg.screenshot(path="/workspace/a-share-review/v99_今日复盘配色.png", full_page=False)
        await b.close()

asyncio.run(main())
