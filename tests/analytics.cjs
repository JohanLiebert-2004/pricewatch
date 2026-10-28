const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const web = path.resolve(__dirname, '../web');
const script = fs.readFileSync(path.join(web, 'analytics.js'), 'utf8');
function fixture(url) {
  const scripts = [];
  const context = vm.createContext({URL, Date,
    window: {location: {href: url}},
    document: {referrer: 'https://example.org/search?email=private@example.org#token',
      createElement: () => ({}), head: {appendChild: element => scripts.push(element)}},
  });
  return {context, scripts};
}
const {context, scripts} = fixture('https://dealwatch.com.au/catalogue.html?q=phone#private');
vm.runInContext(script, context);
vm.runInContext(script, context);
assert.equal(scripts.length, 1, 'Repeated initialization must not load another tag');
assert.equal(scripts[0].src, 'https://www.googletagmanager.com/gtag/js?id=G-3V1XFPB2L7');
const commands = context.window.dataLayer.map(args => Array.from(args));
assert.equal(commands.filter(args => args[0] === 'config').length, 1);
assert.equal(commands.filter(args => args[0] === 'event').length, 0);
const config = commands.find(args => args[0] === 'config')[2];
assert.equal(config.page_location, 'https://dealwatch.com.au/catalogue.html');
assert.equal(config.page_referrer, 'https://example.org/search');
assert.equal(config.allow_google_signals, false);
assert.equal(config.allow_ad_personalization_signals, false);
for (const suffix of ['?token=private', '?EMAIL=private%40example.org', '?q=private%40example.org', '?auth=private']) {
  const sample = fixture('https://dealwatch.com.au/alerts.html' + suffix);
  vm.runInContext(script, sample.context);
  assert.equal(sample.scripts.length, 0, suffix);
  assert.equal(sample.context.window.dataLayer, undefined, suffix);
}
let pages = 0;
for (const file of fs.readdirSync(web).filter(file => file.endsWith('.html'))) {
  const text = fs.readFileSync(path.join(web, file), 'utf8');
  assert.equal((text.match(/src="\/analytics.js"/g) || []).length, 1, file);
  assert.equal((text.match(/GTM-MF4PFW6M/g) || []).length, 2, file);
  pages++;
}
console.log(`PASS: ${pages} templates, one initialization, clean URLs/referrers and sensitive-link exclusion`);
