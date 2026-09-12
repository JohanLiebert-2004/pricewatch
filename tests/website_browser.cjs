// Run with Node and Playwright installed, or set PLAYWRIGHT_MODULE to its path.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const http = require('node:http');
const { chromium } = require(process.env.PLAYWRIGHT_MODULE || 'playwright-core');
const web = path.resolve(__dirname, '../web');
const config = JSON.parse(fs.readFileSync(path.join(web, 'vercel.json')));
const policy = config.headers.find(h => h.source === '/(.*)').headers
  .find(h => h.key === 'Permissions-Policy').value;
const server = http.createServer((req, res) => {
  const pathname = new URL(req.url, 'http://localhost').pathname;
  const file = path.join(web, pathname === '/' ? 'index.html' : pathname);
  if (!file.startsWith(web + path.sep) || !fs.existsSync(file) || !fs.statSync(file).isFile()) {
    res.writeHead(404); res.end(); return;
  }
  res.setHeader('Permissions-Policy', policy);
  res.setHeader('Content-Type', ({'.html':'text/html', '.css':'text/css', '.js':'text/javascript'})[path.extname(file)] || 'application/octet-stream');
  res.end(fs.readFileSync(file));
});

(async () => {
  await new Promise(resolve => server.listen(0, '127.0.0.1', resolve));
  const browser = await chromium.launch({headless:true,
    ...(process.env.CHROMIUM_PATH ? {executablePath:process.env.CHROMIUM_PATH} : {}),
    args:['--use-fake-device-for-media-stream', '--use-fake-ui-for-media-stream']});
  try {
    const context = await browser.newContext({serviceWorkers:'block'});
    await context.route('**/rest/v1/**', route => route.fulfill({json:[]}));
    await context.route('**/_vercel/**', route => route.fulfill({status:204}));
    const page = await context.newPage();
    const errors = [];
    page.on('pageerror', error => errors.push(error.message));
    const url = `http://127.0.0.1:${server.address().port}/search.html`;
    await page.goto(url);
    assert.equal(await page.evaluate(() => document.featurePolicy.allowsFeature('camera')), true);
    assert.equal(await page.locator('#scanner').isVisible(), false);

    // A late permission grant must release its stream without reopening the camera.
    await page.evaluate(() => {
      window.BarcodeDetector = class { async detect() { return []; } };
      navigator.mediaDevices.getUserMedia = () => new Promise(resolve => { window.grantCamera = resolve; });
    });
    await page.locator('#scan').click();
    await page.waitForFunction(() => window.grantCamera);
    await page.locator('#stopScan').click();
    await page.evaluate(() => grantCamera({getTracks:() => [{stop:() => {window.stopped = true;}}]}));
    await page.waitForFunction(() => window.stopped);
    assert.equal(await page.locator('#scanner').isVisible(), false);
    assert.equal(await page.locator('#scanVideo').evaluate(video => video.srcObject), null);

    // A previous search finishing last must not replace the new search.
    await page.evaluate(async () => {
      const originalFetch = window.fetch;
      let pending = [];
      window.fetch = url => new Promise(resolve => pending.push({url, resolve}));
      const old = search('old');
      const latest = search('latest');
      const row = title => ({title, retailer:'kmart', sku:title, url:'https://example.com', current_price:10});
      for (const request of pending.filter(r => r.url.includes('latest'))) {
        request.resolve({ok:true, json:async () => [row('Latest result')]});
      }
      await latest;
      for (const request of pending.filter(r => r.url.includes('old'))) {
        request.resolve({ok:true, json:async () => [row('Old result')]});
      }
      await old;
      window.fetch = originalFetch;
    });
    assert.match(await page.locator('#list').innerText(), /Latest result/);
    assert.doesNotMatch(await page.locator('#list').innerText(), /Old result/);

    // Real bundled ZXing startup against Chromium's fake camera, then cleanup.
    await page.reload();
    await page.evaluate(() => { delete window.BarcodeDetector; });
    await page.locator('#scan').click();
    await page.waitForFunction(() => document.querySelector('#scanner').dataset.state === 'scanning', {timeout:15000});
    await page.evaluate(() => { window.cameraTracks = document.querySelector('#scanVideo').srcObject.getTracks(); });
    await page.locator('#stopScan').click();
    assert.equal(await page.locator('#scanner').isVisible(), false);
    assert.equal(await page.evaluate(() => cameraTracks.every(track => track.readyState === 'ended')), true);

    for (const width of [390, 1440]) {
      await page.setViewportSize({width, height:900});
      assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), true, `Overflow at ${width}px`);
      if (process.env.QA_SCREENSHOTS) await page.screenshot({path:path.join(process.env.QA_SCREENSHOTS, `search-${width}.png`), fullPage:true});
    }
    assert.deepEqual(errors, []);
    console.log('PASS: camera policy, hidden panel, late permission cleanup, search race, real ZXing startup/stop, desktop/mobile overflow, no page errors');
    await context.close();
  } finally {
    await browser.close();
  }
})().catch(error => { console.error(error); process.exitCode = 1; })
  .finally(() => server.close());
