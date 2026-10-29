// Isolated application tests: no real retailer, analytics or email requests.
const assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path'),http=require('node:http');
const {chromium}=require(process.env.PLAYWRIGHT_MODULE||'playwright-core');
const web=path.resolve(__dirname,'../web');
const server=http.createServer((req,res)=>{
  const name=new URL(req.url,'http://localhost').pathname;
  const file=path.join(web,name==='/'?'index.html':name);
  if(!file.startsWith(web+path.sep)||!fs.existsSync(file)||!fs.statSync(file).isFile()){res.writeHead(404);return res.end();}
  res.setHeader('Content-Type',({'.html':'text/html','.js':'text/javascript','.css':'text/css'})[path.extname(file)]||'text/plain');res.end(fs.readFileSync(file));
});
(async()=>{
 await new Promise(resolve=>server.listen(0,'127.0.0.1',resolve));
 const browser=await chromium.launch({headless:true,executablePath:process.env.CHROMIUM_PATH});
 try{
  const context=await browser.newContext({serviceWorkers:'block'});
  const base=`http://127.0.0.1:${server.address().port}`,errors=[],requests=[];
  const product={product_id:7,retailer:'kmart',sku:'LEGO1',title:'LEGO Star Wars <set>',price:40,current_price:40,current_rrp:100,price_updated_at:new Date().toISOString(),scraped_at:new Date().toISOString()};
  let failWishlist=false;
  await context.route('**/*',async route=>{
   const url=new URL(route.request().url());if(url.origin!==base)return route.fulfill({status:204});
   if(url.pathname.startsWith('/rest/v1/')){
    if(url.pathname.includes('/rpc/')){
     const body=route.request().postDataJSON();requests.push({path:url.pathname,body});
     return route.fulfill({json:url.pathname.endsWith('create_keyword_alert')?'manage-test':url.pathname.endsWith('keyword_alert_status')?'pending':true});
    }
    if(url.pathname.endsWith('/product_search')){
     if(failWishlist&&url.searchParams.get('or')?.startsWith('(and('))return route.fulfill({status:503,json:{message:'Unavailable'}});
     const ret=url.searchParams.get('retailer');return route.fulfill({json:!ret||ret.includes('kmart')?[product]:[]});
    }
    if(url.pathname.endsWith('/product_history'))return route.fulfill({json:[{...product,price:30,scraped_at:'2026-09-01T00:00:00Z'},product]});
    return route.fulfill({json:[]});
   }
   if(url.pathname.startsWith('/_vercel/'))return route.fulfill({status:204});
   return route.continue();
  });
  const page=await context.newPage();page.on('pageerror',e=>errors.push(e.message));
  await page.goto(base+'/search.html?q=lego');
  await page.locator('.wishlist-button').first().click();
  assert.equal(await page.locator('.wishlist-button').first().getAttribute('aria-pressed'),'true');
  await page.locator('#save-search').click();
  await page.goto(base+'/alerts.html');
  await page.locator('#wishlist-items .shopping-row').waitFor();
  assert.match(await page.locator('#wishlist-items').innerText(),/LEGO Star Wars <set>.*\$40\.00/s);
  assert.equal(await page.locator('#saved-searches a').innerText(),'lego');
  await page.locator('#saved-searches a').click();
  assert.equal(await page.locator('#q').inputValue(),'lego');
  await page.goto(base+'/catalogue.html?q=lego&retailers=kmart,bigw&cats=toys&pmin=10&pmax=50&sort=discount_desc');
  await page.locator('#list .row').first().waitFor();
  assert.equal(await page.locator('#sort').inputValue(),'discount_desc');
  assert.equal(await page.locator('#pmax').inputValue(),'50');
  await page.locator('#save-search').click();
  await page.goto(base+'/alerts.html');
  assert.equal(await page.locator('#saved-searches .shopping-row').count(),2);
  await page.locator('#saved-searches a').first().click();
  assert.equal(await page.locator('#sort').inputValue(),'discount_desc');
  await page.goto(base+'/alerts.html?q=lego#keyword-tools');
  await page.locator('[name=excluded]').fill('keyring');await page.locator('[name=max]').fill('50');
  await page.locator('[name=email]').fill('test@example.invalid');await page.locator('[name=stores][value=kmart]').check();
  await page.locator('#keyword-form [type=submit]').click();
  await page.waitForFunction(()=>document.querySelector('#keyword-result').textContent.includes('Check your email'));
  const created=requests.find(r=>r.path.endsWith('create_keyword_alert'));
  assert.deepEqual(created.body,{p_email:'test@example.invalid',p_keywords:'lego',p_excluded_words:'keyring',p_retailers:['kmart'],p_min_price:0,p_max_price:50,p_min_discount:0});
  assert.doesNotMatch(await page.evaluate(()=>localStorage.getItem('dealwatch_keyword_alerts')),/test@example.invalid/);
  await page.locator('[data-cancel-keyword]').click();
  await page.waitForFunction(()=>document.querySelector('#keyword-list').textContent.includes('cancelled'));
  await page.goto(base+'/alerts.html?keyword_confirm=email-secret');
  await page.waitForFunction(()=>document.querySelector('.shopping-notice')?.textContent.includes('active'));
  assert.equal(new URL(page.url()).search,'');
  await page.goto(base+'/alerts.html?keyword_cancel=manage-secret');
  assert.equal(requests.filter(r=>r.path.endsWith('cancel_keyword_alert')).length,1,'GET cancellation link must not cancel');
  await page.locator('.shopping-notice button').click();
  await page.waitForFunction(()=>document.querySelector('.shopping-notice')?.textContent.includes('cancelled'));
  await page.goto(base+'/product.html?retailer=kmart&sku=LEGO1');
  await page.locator('.target-shortcuts button').first().click();assert.equal(await page.locator('#wtarget').inputValue(),'30.00');
  await page.locator('.target-shortcuts button').last().click();assert.equal(await page.locator('#wtarget').inputValue(),'36.00');
  await page.goto(base+'/product.html?retailer=kmart&sku=LEGO1&confirm=product-secret');
  await page.waitForFunction(()=>document.querySelector('#confirmation-result')?.textContent.includes('confirmed'));
  failWishlist=true;await page.goto(base+'/alerts.html');
  await page.waitForFunction(()=>document.querySelector('#wishlist-items')?.textContent.includes('saved prices'));
  await page.locator('#wishlist-items .wishlist-button').click();
  assert.match(await page.locator('#wishlist-items').innerText(),/Choose.*Save to wishlist/);
  for(const width of [390,1440]){
   await page.setViewportSize({width,height:900});
   assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth),true,`Overflow at ${width}`);
   if(process.env.QA_SCREENSHOTS)await page.screenshot({path:path.join(process.env.QA_SCREENSHOTS,`shopping-${width}.png`),fullPage:true});
  }
  assert.deepEqual(errors,[]);
  console.log('PASS: wishlist save/persist/remove/current-price failure, saved search/filter restore, keyword create/cancel/confirm, product confirmation and targets, desktop/mobile overflow, no script errors');
 }finally{await browser.close();server.close();}
})().catch(e=>{console.error(e);process.exitCode=1;server.close();});
