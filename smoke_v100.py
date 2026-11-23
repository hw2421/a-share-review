import asyncio
from playwright.async_api import async_playwright
URL="http://localhost:8765/index.html"

async def main():
  errors=[]
  async with async_playwright() as p:
    b=await p.chromium.launch(headless=True)
    ctx=await b.new_context(viewport={"width":1440,"height":1000})
    pg=await ctx.new_page()
    pg.on("pageerror",lambda e:errors.append(str(e)))
    await pg.goto(URL,wait_until="networkidle",timeout=60000)
    await pg.wait_for_timeout(1200)

    print("=== 需求：题材卡固定尺寸，参照11-12 ===")
    await pg.evaluate("switchTab('theme')")
    await pg.evaluate("()=>{THEME_RANGE=9999;renderTheme();}")
    await pg.wait_for_timeout(600)

    # 1. 全部卡片宽度统一66px
    r=await pg.evaluate("""()=>{
      const cs=[...document.querySelectorAll('#theme-list .tcard')];
      const ws={}; cs.forEach(c=>{const w=Math.round(c.getBoundingClientRect().width);ws[w]=(ws[w]||0)+1;});
      let clipped=0; cs.forEach(c=>{if(c.scrollHeight>c.clientHeight+1)clipped++;});
      return {total:cs.length,widths:ws,clipped};
    }""")
    print("  卡片宽度分布:",r)
    assert r['widths'].get('66')==r['total'], f"宽度未全统一66: {r['widths']}"
    assert r['clipped']==0, f"存在被裁切卡片 {r['clipped']}"

    # 2. 11-12基准仍为66x48
    r2=await pg.evaluate("""()=>{
      const row=document.querySelector('#theme-list .trow[data-d="20261112"]');
      return [...row.querySelectorAll('.tcard')].map(c=>({
        th:c.dataset.th,w:Math.round(c.getBoundingClientRect().width),h:Math.round(c.getBoundingClientRect().height)}));
    }""")
    print("  11-12卡片:",r2)
    assert all(x['w']==66 and x['h']==48 for x in r2), "11-12基准异常"

    # 3. 长题材名完整显示
    r3=await pg.evaluate("""()=>{
      const c=[...document.querySelectorAll('#theme-list .tcard')].find(x=>x.dataset.th==='特斯拉/磷酸铁锂/新能源车');
      if(!c)return {err:'none'};
      const nm=c.querySelector('.nm');
      return {txt:nm.innerText,needWrap:nm.scrollHeight>16,fullyShown:nm.scrollWidth<=nm.clientWidth+1};
    }""")
    print("  长题材名:",r3)
    assert r3.get('fullyShown'), "长题材名被裁切"

    # 4. 点题材卡→线路图浮层，点梯队→梯队浮层（交互回归）
    await pg.evaluate("()=>{const c=document.querySelector('#theme-list .tcard[data-d=\"20261112\"]');c&&c.click();}")
    await pg.wait_for_timeout(500)
    pop1=await pg.evaluate("()=>{const p=document.querySelector('.thpop,#thpop');return !!p&&getComputedStyle(p).display!=='none';}")
    print("  线路图浮层:",pop1)
    await pg.keyboard.press("Escape")
    await pg.evaluate("()=>{const l=document.querySelector('#theme-list .tcard[data-d=\"20261112\"] .lad');l&&l.click();}")
    await pg.wait_for_timeout(500)
    pop2=await pg.evaluate("()=>{const p=document.querySelector('.ladpop,#ladpop');return !!p&&getComputedStyle(p).display!=='none';}")
    print("  梯队浮层:",pop2)
    await pg.keyboard.press("Escape")

    # 5. 五页签回归
    print("=== 五页签回归 ===")
    for t in ["market","theme","ladder","hist","decision"]:
      await pg.evaluate("t=>switchTab(t)",t)
      await pg.wait_for_timeout(300)
      vis=await pg.evaluate("t=>{const p=document.getElementById('page-'+t);return !!p&&getComputedStyle(p).display!=='none';}",t)
      print(f"  [{t}] 可见={vis}")
      assert vis, f"{t} 页签异常"

    print("=== JS错误 ===", errors)
    assert not errors, f"JS错误: {errors}"
    print("ALL PASS")
    await b.close()

asyncio.run(main())
