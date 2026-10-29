const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const html = fs.readFileSync(require('node:path').join(__dirname, '../web/product.html'), 'utf8');
const source = html.slice(html.indexOf('function priceContext('), html.indexOf('function rememberViewed('));
const sandbox = {money:n=>'$'+Number(n).toFixed(2)};
vm.createContext(sandbox);
vm.runInContext(source, sandbox);
const row = (price, date) => ({price, scraped_at:date+'T00:00:00Z'});
const rows = [row(100,'2026-05-01'), row(60,'2026-06-01'), row(90,'2026-10-01')];
assert.equal(sandbox.priceContext(rows, 90).difference, 30);
assert.match(sandbox.priceContext(rows, 60).title, /^At the lowest/);
assert.match(sandbox.priceContext(rows, 50).title, /^At the lowest/);
assert.equal(sandbox.priceContext(rows, 50).low, 50);
assert.match(sandbox.priceContext([row(50,'2026-10-01')],50).title, /Still building/);
assert.equal(sandbox.priceContext([row(50,'2026-10-01'),row(40,'2026-10-01')],40).sufficient, false);
assert.equal(sandbox.priceContext([row(null,'2026-10-01'),row('bad','2026-10-02')],40), null);
assert.equal(sandbox.priceContext(rows, null), null);
assert.equal(sandbox.priceContext([row(0,'2026-10-01'),row(1,'2026-10-02')],1).low,0);
assert.equal(sandbox.priceContext([row(19.99,'2026-10-01'),row(20,'2026-10-02')],20).difference,0.01);
const cutoff = Date.parse('2026-08-01T00:00:00Z');
assert.equal(sandbox.windowLow(rows,90,'2026-10-29T00:00:00Z',cutoff),60, 'carry June price into August window');
assert.equal(sandbox.windowLow([row(30,'2026-01-01')],30,'2026-02-01T00:00:00Z',cutoff),null,'no observations in window');
assert.equal(sandbox.windowLow(rows,40,'2026-10-29T00:00:00Z',cutoff),40,'include current price');
assert.equal(sandbox.windowLow([row(30,'2026-01-01'),row(70,'2026-07-01'),row(90,'2026-10-01')],90,'2026-10-29T00:00:00Z',cutoff),70,'exclude obsolete low before boundary');
assert.equal(sandbox.windowLow([row(null,'2026-10-01'),row(50,'2026-10-02')],50,'2026-10-29T00:00:00Z',cutoff),50);
// Compile every inline browser script as well as testing the history calculations.
for(const match of html.matchAll(/<script\b([^>]*)>([\s\S]*?)<\/script>/g)) {
  if(!/application\/ld\+json/.test(match[1])) new vm.Script(match[2]);
}
console.log('PASS: history assessment, limited observations, invalid prices, cents, 90-day carry-forward, expired history and inline script syntax');
