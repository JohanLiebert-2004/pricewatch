
let state={products:[],community_leads:[]},filter='all';
const e=s=>String(s??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const aud=n=>Number.isFinite(n)?new Intl.NumberFormat('en-AU',{style:'currency',currency:'AUD'}).format(n/100):'—';
const fresh=p=>p.observed&&Date.now()-Date.parse(p.observed)<=15*60000;
const anomaly=p=>fresh(p)&&p.historical_drop_percent>=60&&p.baseline_cents-p.cents>=3000;
const priceBounds=()=>{const min=document.getElementById('min-price').value,max=document.getElementById('max-price').value;return {min:min===''?0:Math.round(Number(min)*100),max:max===''?Infinity:Math.round(Number(max)*100)}};
const inPriceRange=(cents,bounds)=>Number.isFinite(cents)&&cents>=bounds.min&&cents<=bounds.max;
function compareDeals(a,b,sort){return sort==='discount'?(b.advertised_discount_percent||0)-(a.advertised_discount_percent||0):sort==='saving'?Math.max(0,(b.reference_cents||b.cents)-b.cents)-Math.max(0,(a.reference_cents||a.cents)-a.cents):sort==='price'?a.cents-b.cents:sort==='price-desc'?b.cents-a.cents:0}
function shortName(p){const title=(p.title||p.asin).split(/,|\s[|–]\s/)[0];return title.length<=100?title:title.slice(0,97).trimEnd()+'…'}
function render(){
 const bounds=priceBounds(),query=document.getElementById('search').value.toLowerCase();let rows;
 if(filter==='community')rows=(state.community_leads||[]).filter(l=>!l.expired&&l.quoted_cents&&l.published&&Date.now()-Date.parse(l.published)<48*3600000).map(l=>({...l,cents:l.quoted_cents,quote:true,advertised_discount_percent:l.reference_cents?Math.round(1000*(1-l.quoted_cents/l.reference_cents))/10:null}));
 else rows=(state.products||[]).filter(p=>p.cents&&fresh(p));
 const total=rows.length;
 if(filter==='errors')rows=rows.filter(p=>anomaly(p)||(p.signals||[]).some(s=>s.includes('reports a price error')));
 if(filter==='double')rows=rows.filter(p=>p.double_checked);
 rows=rows.filter(p=>inPriceRange(p.cents,bounds)&&[p.title,p.asin].join(' ').toLowerCase().includes(query));rows.sort((a,b)=>compareDeals(a,b,document.getElementById('sort').value));
 document.getElementById('count').textContent=(filter==='community'?'Total community quotes: ':'Total deals: ')+total.toLocaleString('en-AU')+' · Showing: '+rows.length.toLocaleString('en-AU');
 document.getElementById('deals').innerHTML=rows.map(p=>'<tr><td><a title="'+e(p.title)+'" target="_blank" rel="noopener" href="https://www.amazon.com.au/dp/'+e(p.asin)+'">'+e(shortName(p))+'</a>'+(p.quote?'<span class="tag">Unverified</span>':'')+'</td><td>'+aud(p.cents)+'</td><td>'+(p.advertised_discount_percent>0?e(p.advertised_discount_percent)+'% · '+aud(Math.max(0,p.reference_cents-p.cents)):'—')+'</td></tr>').join('')||'<tr><td class="empty" colspan="3">'+(filter==='errors'?'No possible pricing errors verified yet.':'No matching current offers.')+'</td></tr>';
 document.getElementById('updated').textContent=state.generated_at?'Updated '+new Date(state.generated_at).toLocaleTimeString('en-AU',{timeZone:'Australia/Perth',hour12:false})+' AWST':'';
}
async function refresh(){try{if(globalThis.location?.protocol==='file:'){state=JSON.parse(document.getElementById('snapshot').textContent);render();if(!globalThis.fileReload)globalThis.fileReload=setTimeout(()=>location.reload(),60000);return}const r=await fetch('/amazon-deals.json',{cache:'no-store'});if(!r.ok)throw Error('Report unavailable');state=await r.json();render();document.getElementById('error').textContent=''}catch(error){document.getElementById('error').textContent='Refresh unavailable; displayed prices may be stale.'}}
['search','min-price','max-price'].forEach(id=>document.getElementById(id).addEventListener('input',render));document.getElementById('sort').addEventListener('change',render);document.getElementById('clear-price').addEventListener('click',()=>{document.getElementById('min-price').value='';document.getElementById('max-price').value='';render()});document.querySelectorAll('[data-filter]').forEach(b=>b.addEventListener('click',()=>{filter=b.dataset.filter;document.querySelectorAll('[data-filter]').forEach(x=>x.classList.toggle('active',x===b));render()}));refresh();setInterval(refresh,15000);
