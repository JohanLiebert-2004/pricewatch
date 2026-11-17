const fs=require('fs'),vm=require('vm'),assert=require('assert');
const elements=new Map(),listeners={};
function element(id){if(!elements.has(id))elements.set(id,{value:'',textContent:'',innerHTML:'',events:{},addEventListener(name,fn){this.events[name]=fn},classList:{toggle(){}},setAttribute(){}});return elements.get(id)}
const location={href:'https://dealwatch.com.au/amazon.html',search:'',protocol:'https:'};
function navigate(_,__,path){const url=new URL(path,location.href);location.href=url.href;location.search=url.search}
const context=vm.createContext({document:{getElementById:element,querySelectorAll:()=>[]},location,history:{pushState:navigate,replaceState:navigate},URL,URLSearchParams,Intl,Date,Number,Math,addEventListener:(name,fn)=>listeners[name]=fn});
const script=fs.readFileSync('web/amazon.js','utf8').replace('refresh();setInterval(refresh,15000);','');
vm.runInContext(script,context);
const now=new Date().toISOString(),old=new Date(Date.now()-7200000).toISOString();
const catalogue=Array.from({length:123},(_,i)=>({asin:'B'+String(i).padStart(9,'0'),title:'Product '+i,cents:i<100?(i+1)*1000:null,observed:i<20?now:i<100?old:null,status:i<100?'priced':'awaiting_check',signals:[]}));
context.sample={generated_at:now,catalogue,products:catalogue.filter(p=>p.cents),coverage:{tracked_products:123,checked_products:100,priced_products:100}};
vm.runInContext('state=sample;render()',context);
const rows=()=>[...element('deals').innerHTML.matchAll(/\/dp\/([A-Z0-9]{10})/g)].map(x=>x[1]);
const first=rows();assert.equal(first.length,50);assert(element('pagination').innerHTML.includes('Page 1 of 3'));
vm.runInContext('goToPage(2)',context);const second=rows();assert.equal(second.length,50);assert(second.every(x=>!first.includes(x)));assert(location.search.includes('page=2'));
vm.runInContext('goToPage(3)',context);const third=rows();assert.equal(third.length,23);assert.equal(new Set([...first,...second,...third]).size,123);assert(element('deals').innerHTML.includes('Awaiting check'));
vm.runInContext('state=sample;render()',context);assert.equal(vm.runInContext('page',context),3,'refresh retains the selected page');
vm.runInContext("filter='recent';page=1;render()",context);assert.equal(rows().length,20);
vm.runInContext("filter='priced';render()",context);assert.equal(vm.runInContext('selectedRows().length',context),100);assert(element('deals').innerHTML.includes('Last checked 2h ago'));
vm.runInContext("filter='tracked';goToPage(3)",context);element('max-price').value='50';element('max-price').events.input();assert.equal(vm.runInContext('page',context),1);assert.equal(rows().length,5,'unknown prices do not pass a numeric price filter');
element('max-price').value='';element('search').value='';element('page-size').value='25';element('page-size').events.change();assert.equal(rows().length,25);assert(element('pagination').innerHTML.includes('Page 1 of 5'));
location.search='?view=tracked&page=4&size=25&sort=price-desc';listeners.popstate();assert.equal(vm.runInContext('page',context),4);assert.equal(rows().length,25);
vm.runInContext('goToPage(999)',context);assert.equal(vm.runInContext('page',context),5);
console.log('All 123 products browsed across distinct pages; filters, age labels, URL state, back navigation and live-refresh page retention verified.');
if(process.env.AMAZON_LIVE_FIXTURE){
 context.live=JSON.parse(fs.readFileSync(process.env.AMAZON_LIVE_FIXTURE,'utf8'));
 for(const id of ['search','min-price','max-price'])element(id).value='';
 vm.runInContext("state=live;filter='tracked';pageSize=50;page=1;render()",context);
 const seen=new Set(),pages=Math.ceil(context.live.catalogue.length/50);
 for(let n=1;n<=pages;n++){vm.runInContext('goToPage('+n+')',context);for(const asin of rows()){assert(!seen.has(asin),'duplicate across live catalogue pages');seen.add(asin)}}
 assert.equal(seen.size,context.live.coverage.tracked_products);
 console.log('Live deployment data verified: '+seen.size+' unique products reachable across '+pages+' pages.');
}
