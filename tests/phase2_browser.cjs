// Run with the same PLAYWRIGHT_MODULE / CHROMIUM_PATH as website_browser.cjs.
const assert=require('node:assert/strict');
const fs=require('node:fs'),path=require('node:path'),http=require('node:http');
const {chromium}=require(process.env.PLAYWRIGHT_MODULE||'playwright-core');
const comparison=require('../web/comparisons.js');
assert.equal(comparison.gtin('4006381333931'),'04006381333931');
assert.equal(comparison.gtin('04006381333931'),'04006381333931');
for(const code of ['4006381333932','0000000000000','70330905764','abc','']) assert.equal(comparison.gtin(code),null);
assert(comparison.aliases('4006381333931').includes('04006381333931'));
assert(!comparison.fresh({current_price:0,last_seen:new Date().toISOString()}));
assert(!comparison.fresh({current_price:10,last_seen:'2020-01-01'}));

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
    const now=new Date().toISOString();
    const product={product_id:1,retailer:'kmart',sku:'TEST',gtin:'4006381333931',title:'Acme 1234 Pencil Set',brand:'Acme',category:'home',url:'https://example.com',current_price:40,price:40,price_updated_at:now,scraped_at:now};
    const other={...product,retailer:'bigw',sku:'EXACT',gtin:'04006381333931',current_price:30,last_seen:now};
    const stale={...other,retailer:'target',sku:'OLD',current_price:1,last_seen:'2020-01-01'};
    const lookalike={...other,retailer:'myer',sku:'LOOK',gtin:'5901234123457',current_price:2};
    const market={...other,retailer:'myer',sku:'MARKET',current_price:35,is_marketplace:true};
    let mode='normal',brandRoute=null;
    await context.route('**/_vercel/**',r=>r.fulfill({status:204}));
    await context.route('**/rest/v1/**',async route=>{
      const url=new URL(route.request().url());
      if(url.pathname.endsWith('/product_history')) return route.fulfill({json:[{...product,gtin:mode==='missing'?null:product.gtin}]});
      if(url.pathname.endsWith('/product_search')){
        if(url.searchParams.has('gtin')){
          assert.match(url.searchParams.get('gtin'),/04006381333931/);
          if(mode==='error') return route.fulfill({status:503,json:{message:'Unavailable'}});
          return route.fulfill({json:mode==='empty'?[]:[other,stale,lookalike,market,{...other,current_price:0,sku:'ZERO'}]});
        }
        if(mode==='normal'){brandRoute=route;return;}
        return route.fulfill({json:[lookalike,other]});
      }
      return route.fulfill({json:[]});
    });
    const page=await context.newPage(),errors=[];page.on('pageerror',e=>errors.push(e.message));
    const url=`http://127.0.0.1:${server.address().port}/product.html?retailer=kmart&sku=TEST`;
    await page.goto(url);
    await page.waitForFunction(()=>document.querySelector('#compare-prices')?.dataset.state==='ready');
    assert.match(await page.locator('#compare-prices .comparison-saving').innerText(),/\$10\.00 lower/);
    assert.equal(await page.locator('#related').isVisible(),false); // Exact results did not wait for brand request.
    assert.equal(await page.locator('#compare-prices > .comparison-table-wrap tbody tr').count(),3);
    assert.equal(await page.locator('#compare-prices a[href*="LOOK"]').count(),0);
    assert.match(await page.locator('.comparison-older summary').innerText(),/1 older/);
    assert.equal(await page.locator('.comparison-older .pill.deal').count(),0);
    assert.match(await page.locator('#compare-prices .pill.mkt').innerText(),/Marketplace seller/);
    await page.waitForFunction(()=>document.querySelector('#compare-prices').compareDocumentPosition(document.querySelector('#watchpanel')) & Node.DOCUMENT_POSITION_FOLLOWING);
    await brandRoute.fulfill({json:[lookalike,other]});
    await page.waitForSelector('#related:not([hidden])');
    assert.match(await page.locator('#related').innerText(),/not confirmed identical/);
    assert.doesNotMatch(await page.locator('#related').innerText(),/cheaper|lower at|save \$/i);
    assert.equal(await page.locator('#related a[href*="EXACT"]').count(),0);
    for(const width of [390,1440]){
      await page.setViewportSize({width,height:900});
      assert(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth));
      if(process.env.QA_SCREENSHOTS) await page.locator('#compare-prices').screenshot({path:path.join(process.env.QA_SCREENSHOTS,`phase2-compare-${width}.png`)});
    }
    mode='error';await page.reload();await page.waitForSelector('.comparison-retry');
    assert.doesNotMatch(await page.locator('#compare-prices').innerText(),/No recently checked/);
    mode='empty';await page.locator('.comparison-retry').click();
    await page.waitForFunction(()=>document.querySelector('#compare-prices').dataset.state==='ready');
    assert.match(await page.locator('#compare-prices').innerText(),/No recently checked/);
    mode='missing';await page.reload();
    await page.waitForFunction(()=>document.querySelector('#compare-prices')?.dataset.state==='unavailable');
    assert.equal(await page.locator('#compare-prices').getAttribute('aria-busy'),'false');
    await page.goto(`http://127.0.0.1:${server.address().port}/about.html`);
    assert.match(await page.locator('main').innerText(),/does not use affiliate links/);
    for(const width of [390,1440]){
      await page.setViewportSize({width,height:900});
      assert(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth));
      if(process.env.QA_SCREENSHOTS) await page.screenshot({path:path.join(process.env.QA_SCREENSHOTS,`phase2-about-${width}.png`),fullPage:true});
    }
    for(const name of fs.readdirSync(web).filter(f=>f.endsWith('.html'))){
      assert(fs.readFileSync(path.join(web,name),'utf8').includes('href="/about.html"'),`${name} missing About link`);
    }
    assert.deepEqual(errors,[]);
    console.log('PASS Phase 2: GTIN validity/padding, exact/similar separation, independent requests, stale-price exclusion, seller labels, errors/retry, missing barcode, About links/copy and mobile/desktop');
  }finally{await browser.close();}
})().catch(e=>{console.error(e);process.exitCode=1}).finally(()=>server.close());
