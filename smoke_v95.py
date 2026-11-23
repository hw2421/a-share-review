import asyncio
from playwright.async_api import async_playwright

URL = "http://localhost:8765/index.html"

async def main():
    errors = []
    async with async_playwright() as p:
        b = await p.chromium.launch(headless=True)
        pg = await b.new_page(viewport={"width": 1440, "height": 1000})
        pg.on("pageerror", lambda e: errors.append(str(e)))
        await pg.goto(URL, wait_until="networkidle", timeout=30000)
        await pg.wait_for_timeout(1500)
        # 切到今日复盘
        await pg.evaluate("switchTab('decision')")
        await pg.wait_for_timeout(1500)

        res = await pg.evaluate("""() => {
          const root = document.getElementById('ch-root');
          const items = root ? root.querySelectorAll('.ch-item').length : -1;
          const sum = (document.getElementById('ch-summary')||{}).textContent||'';
          const sel = document.getElementById('ch-sel');
          const rules = (document.getElementById('ch-rules')||{}).textContent||'';
          const why = root && root.querySelector('.ch-why');
          return {
            items, sum,
            selVal: sel ? sel.value : null,
            selCount: sel ? sel.options.length : 0,
            rulesLen: rules.length,
            rulesHas5: rules.indexOf('五要素')>=0,
            whySample: why ? why.textContent.slice(0,80) : null,
            nameSample: root && root.querySelector('.ch-nm') ? root.querySelector('.ch-nm').textContent : null,
            hasNote: root ? root.querySelectorAll('.ch-note input').length : 0
          };
        }""")
        print("=== 初始渲染 ===", res)

        # 测试保存自省
        note_id = await pg.evaluate("""() => {
          const inp = document.querySelector('.ch-note input');
          if(!inp) return null; return inp.id;
        }""")
        if note_id:
            code = note_id.replace('ch-note-','')
            await pg.fill('#'+note_id, '测试：恐高没敢打板，下次按五要素机械执行')
            await pg.click('.ch-note button')
            await pg.wait_for_timeout(300)
            saved = await pg.evaluate("""(code) => {
              const db = JSON.parse(localStorage.getItem('sd_diary')||'{}');
              const hits = Object.entries(db).filter(([k,v])=>k.indexOf('|'+code+'|')>=0);
              return {count:hits.length, sample:hits[0]};
            }""", code)
            print("=== 自省保存(sd_diary) ===", saved)

        # 测试兼容历史 sd_diary 结构（模拟用户以前填的记录）
        compat = await pg.evaluate("""() => {
          const db = JSON.parse(localStorage.getItem('sd_diary')||'{}');
          db['20261102|002172|summit'] = {tag:'犹豫错过买点', txt:'历史遗留：当时犹豫没打', name:'澳洋健康', d:'20261102', m:'summit', ts:1234567890};
          localStorage.setItem('sd_diary', JSON.stringify(db));
          // 重新渲染，检查回填
          if(typeof paintChances==='function') paintChances('20261102');
          const inp = document.getElementById('ch-note-002172');
          const sel = document.getElementById('ch-tag-002172');
          return {inputVal: inp?inp.value:null, tagVal: sel?sel.value:null};
        }""")
        print("=== 历史sd_diary回填兼容 ===", compat)

        # 测试显示自省汇总
        await pg.click('#ch-diary-show')
        await pg.wait_for_timeout(300)
        dlg = await pg.evaluate("() => (document.getElementById('ch-diary-panel')||{}).textContent.slice(0,200)")
        print("=== 自省汇总 ===", dlg)

        # 测试日期切换
        before = await pg.evaluate("() => document.getElementById('ch-sel').value")
        await pg.click('#ch-prev')
        await pg.wait_for_timeout(500)
        after = await pg.evaluate("() => document.getElementById('ch-sel').value")
        print("=== 日期切换 ===", before, "->", after)

        # 截图（滚到机会卡片）
        await pg.evaluate("""() => { const c=document.getElementById('ch-card'); if(c) c.scrollIntoView(); }""")
        await pg.wait_for_timeout(400)
        await pg.screenshot(path="/workspace/a-share-review/v95_当日机会子模块.png", full_page=False)
        print("JS报错:", errors if errors else "0报错")
        await b.close()

asyncio.run(main())
