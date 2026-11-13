// Integration checks: filters, freshness, price mismatch, source expiry, failure recovery.
const assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path'),http=require('node:http');
const {chromium}=require(process.env.PLAYWRIGHT_MODULE||'playwright-core');
const web=path.resolve(__dirname,'../web');
const server=http.createServer((req,res)=>{
 const name=new URL(req.url,'http://localhost').pathname,file=path.join(web,name==='/'?'index.html':name);
 if(!file.startsWith(web+path.sep)||!fs.existsSync(file)||!fs.statSync(file).isFile()){res.writeHead(404);return res.end();}
 res.setHeader('Content-Type',({'.html':'text/html','.js':'text/javascript','.css':'text/css','.json':'application/json','.jpg':'image/jpeg'})[path.extname(file)]||'text/plain');res.end(fs.readFileSync(file));
});
(async()=>{
 await new Promise(resolve=>server.listen(0,'127.0.0.1',resolve));
 const browser=await chromium.launch({headless:true,executablePath:process.env.CHROMIUM_PATH});
 try{
  const context=await browser.newContext({serviceWorkers:'block'}),page=await context.newPage(),errors=[];
  const base=`http://127.0.0.1:${server.address().port}`,seed=JSON.parse(fs.readFileSync(path.join(web,'black-friday-offers.json'),'utf8'));
  const now=new Date().toISOString(),old=new Date(Date.now()-72*3600000).toISOString();
  let failStore=false,allFail=false,expire=false;
  const products=Array.from({length:12},(_,i)=>({retailer:'kmart',sku:`test${i}`,title:`Test kitchen item ${i}`,category:'kitchen',price:20+i,reference_price:100,pct_off:80-i,reference_source:'Retailer RRP',is_30d_low:i===0,url:`https://www.kmart.com.au/product/test-${i}`}));
  const extra=Array.from({length:12},(_,i)=>({retailer:'target',sku:`extra${i}`,title:`Toy pick ${i}`,category:'toys',price:40+i,reference_price:100,pct_off:60-i,reference_source:'Retailer RRP',url:`https://www.target.com.au/product/test-${i}`}));
  products.push({retailer:'kmart',sku:'changed',title:'Changed price MUST NOT SHOW',category:'tech',price:10,reference_price:100,pct_off:90,url:'https://www.kmart.com.au/'});
  products.push({retailer:'kmart',sku:'stale',title:'Stale listing MUST NOT SHOW',category:'tech',price:10,reference_price:100,pct_off:90,url:'https://www.kmart.com.au/'});
  page.on('pageerror',e=>errors.push(e.message));
  await context.route('**/*',async route=>{
   const u=new URL(route.request().url());
   if(u.origin!==base)return route.fulfill({status:204});
   if(u.pathname==='/black-friday-offers.json'){
    const data=structuredClone(seed);data.reviewed_at=expire?old:now;
    data.products.push({...data.products[0],id:'expired',title:'Expired offer MUST NOT SHOW',expires_at:old});
    return route.fulfill({json:data});
   }
   if(u.pathname.startsWith('/rest/v1/')){
    assert.equal(route.request().method(),'GET');
    const retailer=u.searchParams.get('retailer')?.slice(3);
    if(allFail||(failStore&&retailer==='kmart'))return route.fulfill({status:503,json:{message:'Unavailable'}});
    if(u.pathname.endsWith('/discount_feed')){
     assert.equal(u.searchParams.get('is_marketplace'),'is.false');assert.equal(u.searchParams.get('limit'),'12');
     assert.deepEqual(u.searchParams.getAll('pct_off'),['gte.20','lte.85']);
     return route.fulfill({json:retailer==='kmart'?products:retailer==='target'?extra:[]});
    }
    if(u.pathname.endsWith('/product_search')){
     assert.match(u.searchParams.get('last_seen'),/^gte\./);
     return route.fulfill({json:(retailer==='target'?extra:products).map(p=>({retailer:p.retailer,sku:p.sku,current_price:p.sku==='changed'?99:p.price,last_seen:p.sku==='stale'?old:now,is_marketplace:false}))});
    }
   }
   return route.continue();
  });
  await page.goto(base+'/black-friday.html');
  await page.waitForFunction(()=>document.querySelector('#bf-refresh').disabled===false&&document.querySelector('#bf-status').textContent.includes('13 supported'));
  assert.equal(await page.locator('.bf-card').count(),24);
  await page.locator('#bf-more').click();assert.equal(await page.locator('.bf-card').count(),33);
  assert.equal(await page.locator('#bf-more').isVisible(),false);
  assert.equal(await page.locator('#bf-stores a').count(),24);
  assert.doesNotMatch(await page.locator('#bf-list').innerText(),/MUST NOT SHOW/);
  await page.locator('#bf-categories [data-category="clothing"]').click();assert.equal(await page.locator('.bf-card').count(),3);
  await page.locator('#bf-budget').check();assert.equal(await page.locator('.bf-card').count(),1);
  await page.locator('#bf-query').fill('no-such-product');assert.match(await page.locator('#bf-list').innerText(),/No matches/);
  await page.locator('#bf-reset').click();assert.equal(await page.locator('.bf-card').count(),24);
  await page.locator('#bf-store').selectOption('dyson');assert.equal(await page.locator('.bf-card').count(),2);
  await page.locator('#bf-discount').selectOption('50');assert.equal(await page.locator('.bf-card').count(),0);
  await page.locator('#bf-reset').click();await page.locator('#bf-sort').selectOption('price');assert.match(await page.locator('.bf-card').first().innerText(),/\$20/);
  await page.locator('#bf-query').fill('<img src=x onerror=alert(1)>');assert.equal(await page.locator('#bf-list img').count(),0);await page.locator('#bf-reset').click();
  failStore=true;await page.locator('#bf-refresh').click();await page.waitForFunction(()=>document.querySelector('#bf-status').textContent.includes('1 could not'));
  assert.equal(await page.locator('.bf-card').count(),21);assert.equal(await page.locator('#bf-status').getAttribute('data-error'),'true');
  failStore=false;await page.locator('#bf-refresh').click();await page.waitForFunction(()=>document.querySelector('#bf-status').textContent.includes('13 supported'));assert.equal(await page.locator('.bf-card').count(),24);
  for(const width of [360,390,768,1440]){
   await page.setViewportSize({width,height:1000});assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth),true,`Overflow at ${width}`);
   if(process.env.QA_SCREENSHOTS&&[390,1440].includes(width))await page.screenshot({path:path.join(process.env.QA_SCREENSHOTS,`black-friday-${width}.png`),fullPage:true});
  }
  expire=true;await page.reload();await page.waitForFunction(()=>document.querySelector('#bf-status').textContent.includes('13 supported'));assert.equal(await page.locator('.bf-card').count(),24);
  allFail=true;await page.locator('#bf-refresh').click();await page.waitForFunction(()=>document.querySelector('#bf-status').textContent.includes('13 could not'));assert.equal(await page.locator('.bf-card').count(),0);
  assert.deepEqual(errors,[]);
  console.log('PASS: tracked price/freshness verification, source expiry, filters/search/reset/sort, partial/total API failure and recovery, 24 store links, 360/390/768/1440px overflow and no script errors');
 }finally{await browser.close();server.close();}
})().catch(e=>{console.error(e);process.exitCode=1;server.close();});
