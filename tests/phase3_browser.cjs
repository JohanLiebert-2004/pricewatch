// PLAYWRIGHT_MODULE / CHROMIUM_PATH match website_browser.cjs.
const assert=require('node:assert/strict');
const fs=require('node:fs'),path=require('node:path'),http=require('node:http');
const {chromium}=require(process.env.PLAYWRIGHT_MODULE||'playwright-core');
const {chart}=require('../web/sparklines.js');
const now=Date.now(), stamp=days=>new Date(now-days*86400000).toISOString();
assert.equal(chart([[stamp(1),12]]).state,'sparse');
assert.equal(chart([[stamp(2),0],[stamp(1),'NaN']]).state,'sparse');
assert.match(chart([[stamp(10),12],[stamp(1),12]]).html,/No change/);
assert.match(chart([[stamp(10),20],[stamp(1),12]]).html,/ H[\d.]+ V/); // observed steps, no invented smoothing
assert.match(chart([[stamp(10),12],[stamp(1),20]]).html,/Range \$12.00 to \$20.00/);
const web=path.resolve(__dirname,'../web');
const server=http.createServer((req,res)=>{
  const pathname=new URL(req.url,'http://localhost').pathname;
  const file=path.join(web,pathname==='/'?'index.html':pathname);
  if(!file.startsWith(web+path.sep)||!fs.existsSync(file)||!fs.statSync(file).isFile()){res.writeHead(404);res.end();return;}
  res.setHeader('Content-Type',({'.html':'text/html','.js':'text/javascript','.css':'text/css'})[path.extname(file)]||'text/plain');
  res.end(fs.readFileSync(file));
});
(async()=>{
  await new Promise(resolve=>server.listen(0,'127.0.0.1',resolve));
  const browser=await chromium.launch({headless:true,...(process.env.CHROMIUM_PATH?{executablePath:process.env.CHROMIUM_PATH}:{})});
  try{
    const context=await browser.newContext({serviceWorkers:'block'});
    let historyCalls=[],mode='normal',held;
    await context.route('**/_vercel/**',r=>r.fulfill({status:204}));
    await context.route('**/rest/v1/**',async r=>{
      const url=new URL(r.request().url());
      if(url.pathname.endsWith('discount_feed')){
        const offset=Number(url.searchParams.get('offset')||0);
        const rows=Array.from({length:24},(_,i)=>({retailer:'kmart',sku:`${mode}-${offset+i}`,title:`Product ${offset+i}`,price:12,reference_price:20,pct_off:40,is_30d_low:true,category:'home',price_updated_at:stamp(0)}));
        return r.fulfill({json:rows,headers:{'Content-Range':`${offset}-${offset+23}/48`}});
      }
      if(url.pathname.endsWith('feed_price_history')){
        const items=r.request().postDataJSON().p_items;
        assert(items.length<=24);historyCalls.push(items);
        if(mode==='held'){held=r;return;}
        if(mode==='error')return r.fulfill({status:503,json:{message:'Unavailable'}});
        return r.fulfill({json:items.map((item,i)=>({...item,points:i===1?[[stamp(1),12]]:[[stamp(25),20],[stamp(2),12]]}))});
      }
      return r.fulfill({json:[]});
    });
    const page=await context.newPage(),errors=[];page.on('pageerror',e=>errors.push(e.message));
    await page.goto(`http://127.0.0.1:${server.address().port}/`);
    await page.waitForSelector('#list [data-state="ready"]');
    assert.equal(historyCalls.length,1);
    assert.equal(await page.locator('#list [data-state="sparse"]').count(),1);
    assert.equal(await page.locator('#dealFilters').evaluate(n=>n.open),false);
    await page.locator('#loadMore').click();
    await page.waitForFunction(()=>document.querySelectorAll('#list [data-state="ready"]').length===46);
    assert.equal(historyCalls.length,2);assert.equal(historyCalls[1].length,24);
    assert(historyCalls[1].every(i=>Number(i.sku.split('-')[1])>=24));
    for(const width of [320,390,768,1440]){
      await page.setViewportSize({width,height:900});
      assert(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth));
      if(width===390)assert((await page.locator('#list').boundingBox()).y + await page.evaluate(()=>scrollY) < 1100);
      const badge=await page.locator('#list .badge').first().boundingBox();
      const low=await page.locator('#list .badge.lowest').first().boundingBox();
      assert(badge.y+badge.height<=low.y);
    }
    await page.locator('#dealFilters summary').click();
    await page.locator('#includeMarketplace').check();
    await page.waitForFunction(()=>document.querySelector('#filterSummary').textContent.includes('marketplace included'));
    mode='held';await page.locator('#qclear').click();
    await page.waitForFunction(()=>document.querySelector('#list .card-history').dataset.historySku==='held-0');
    for(let i=0;i<50&&!held;i++)await new Promise(resolve=>setTimeout(resolve,10));
    assert(held);
    mode='error';await page.locator('#includeMarketplace').check();
    await page.waitForSelector('#list [data-state="error"]');
    await held.fulfill({json:[{retailer:'kmart',sku:'held-0',points:[[stamp(25),999],[stamp(1),1]]}]});
    assert.equal(await page.locator('#list [data-state="ready"]').count(),0);
    assert.equal(await page.locator('#list .card').count(),24);
    assert.match(await page.locator('#list .cnow').first().innerText(),/12/);
    await page.emulateMedia({reducedMotion:'reduce'});
    assert.equal(await page.locator('.live').evaluate(n=>getComputedStyle(n).animationName),'none');
    assert.deepEqual(errors,[]);
    console.log('PASS Phase 3: bounded batching, pagination cache, sparse/flat/rising/falling history, stale response isolation, API failure, usable cards, filter disclosure, badges, reduced motion and 320–1440px layout');
  }finally{await browser.close()}
})().catch(e=>{console.error(e);process.exitCode=1}).finally(()=>server.close());
