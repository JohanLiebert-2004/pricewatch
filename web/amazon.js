let state={products:[],catalogue:[],community_leads:[]},filter='tracked',page=1,pageSize=50;
const views=['tracked','recent','priced','errors','double','community'];
const e=s=>String(s??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const known=p=>Number.isFinite(p.cents)&&p.cents>0;
const aud=n=>Number.isFinite(n)?new Intl.NumberFormat('en-AU',{style:'currency',currency:'AUD'}).format(n/100):'—';
const fresh=p=>p.observed&&Date.now()-Date.parse(p.observed)<=15*60000;
const anomaly=p=>fresh(p)&&p.historical_drop_percent>=60&&p.baseline_cents-p.cents>=3000;
const priceBounds=()=>{const min=document.getElementById('min-price').value,max=document.getElementById('max-price').value;return {active:min!==''||max!=='',min:min===''?0:Math.round(Number(min)*100),max:max===''?Infinity:Math.round(Number(max)*100)}};
const inPriceRange=(cents,b)=>Number.isFinite(cents)?cents>=b.min&&cents<=b.max:!b.active;

function compareDeals(a,b,sort){
 if(known(a)!==known(b))return known(a)?-1:1;
 let result=sort==='discount'?(b.advertised_discount_percent||0)-(a.advertised_discount_percent||0):sort==='saving'?Math.max(0,(b.reference_cents||b.cents)-b.cents)-Math.max(0,(a.reference_cents||a.cents)-a.cents):sort==='price'?(a.cents||0)-(b.cents||0):sort==='price-desc'?(b.cents||0)-(a.cents||0):0;
 return result||String(a.asin).localeCompare(String(b.asin));
}
function shortName(p){const title=(p.title||p.asin).split(/,|\s[|–]\s/)[0];return title.length<=100?title:title.slice(0,97).trimEnd()+'…'}
function catalogue(){return state.catalogue?.length?state.catalogue:(state.products||[])}
function selectedRows(){
 const bounds=priceBounds(),query=document.getElementById('search').value.trim().toLowerCase();let rows;
 if(filter==='community')rows=(state.community_leads||[]).filter(l=>!l.expired&&l.quoted_cents&&l.published&&Date.now()-Date.parse(l.published)<48*3600000).map(l=>({...l,cents:l.quoted_cents,quote:true,advertised_discount_percent:l.reference_cents?Math.round(1000*(1-l.quoted_cents/l.reference_cents))/10:null}));
 else if(filter==='tracked')rows=catalogue();
 else if(filter==='priced')rows=catalogue().filter(known);
 else rows=(state.products||[]).filter(p=>known(p)&&fresh(p));
 if(filter==='errors')rows=rows.filter(p=>anomaly(p)||(p.signals||[]).some(s=>s.includes('reports a price error')));
 if(filter==='double')rows=rows.filter(p=>p.double_checked);
 return rows.filter(p=>inPriceRange(p.cents,bounds)&&[p.title,p.asin].join(' ').toLowerCase().includes(query)).sort((a,b)=>compareDeals(a,b,document.getElementById('sort').value));
}
function rowStatus(p){
 if(p.quote)return 'Community quote · unverified';
 if(p.status==='awaiting_au_check')return 'Awaiting AU price check';
 if(p.status==='awaiting_check'||!p.observed)return 'Awaiting check';
 if(!known(p))return p.status==='unavailable'?'Unavailable at last check':'Price not confirmed';
 if(fresh(p))return '';
 const minutes=Math.max(1,Math.floor((Date.now()-Date.parse(p.observed))/60000));
 return Number.isFinite(minutes)?'Last checked '+(minutes<60?minutes+'m':minutes<1440?Math.floor(minutes/60)+'h':Math.floor(minutes/1440)+'d')+' ago':'Check time unknown';
}
function pageButtons(total){
 const wanted=new Set([1,total,page-2,page-1,page,page+1,page+2]);let last=0,html='';
 for(const n of [...wanted].filter(n=>n>=1&&n<=total).sort((a,b)=>a-b)){
  if(last&&n>last+1)html+='<span aria-hidden="true">…</span>';
  html+='<button type="button" data-page="'+n+'"'+(n===page?' class="active" aria-current="page"':'')+' aria-label="Page '+n+'">'+n+'</button>';last=n;
 }
 return html;
}
function render(){
 const rows=selectedRows(),pages=Math.max(1,Math.ceil(rows.length/pageSize));page=Math.min(Math.max(1,page),pages);
 const start=(page-1)*pageSize,visible=rows.slice(start,start+pageSize),first=rows.length?start+1:0,last=start+visible.length;
 const all=catalogue(),coverage=state.coverage||{},recorded=coverage.priced_products??all.filter(known).length,checked=coverage.checked_products??all.filter(p=>p.observations||p.observed).length;
 const recent=(state.products||[]).filter(p=>known(p)&&fresh(p)).length;
 document.getElementById('count').textContent=(coverage.tracked_products??all.length).toLocaleString('en-AU')+' tracked · '+checked.toLocaleString('en-AU')+' checked · '+recorded.toLocaleString('en-AU')+' recorded prices · '+recent.toLocaleString('en-AU')+' refreshed in 15 min · Showing '+first+'–'+last+' of '+rows.length.toLocaleString('en-AU');
 document.getElementById('deals').innerHTML=visible.map(p=>{
  const status=rowStatus(p),discount=known(p)&&p.advertised_discount_percent>0?e(p.advertised_discount_percent)+'% · '+aud(Math.max(0,p.reference_cents-p.cents)):'—';
  return '<tr><td><a title="'+e(p.title)+'" target="_blank" rel="noopener" href="https://www.amazon.com.au/dp/'+e(p.asin)+'">'+e(shortName(p))+'</a>'+(status?'<small class="row-status">'+e(status)+'</small>':'')+'</td><td>'+aud(p.cents)+'</td><td>'+discount+'</td></tr>';
 }).join('')||'<tr><td class="empty" colspan="3">'+(filter==='errors'?'No possible pricing errors verified yet.':'No matching products.')+'</td></tr>';
 document.getElementById('pagination').innerHTML='<button type="button" data-page="'+(page-1)+'"'+(page===1?' disabled':'')+'>← Previous</button>'+pageButtons(pages)+'<button type="button" data-page="'+(page+1)+'"'+(page===pages?' disabled':'')+'>Next →</button><span class="page-label">Page '+page+' of '+pages+'</span>';
 document.getElementById('updated').textContent=state.generated_at?'Updated '+new Date(state.generated_at).toLocaleTimeString('en-AU',{timeZone:'Australia/Perth',hour12:false})+' AWST':'';
 document.querySelectorAll('[data-filter]').forEach(b=>{const active=b.dataset.filter===filter;b.classList.toggle('active',active);b.setAttribute('aria-pressed',String(active))});
}
function updateUrl(push=false){
 if(!globalThis.history||!globalThis.location)return;
 const url=new URL(location.href),values={view:filter,page:String(page),size:String(pageSize),sort:document.getElementById('sort').value,q:document.getElementById('search').value,min:document.getElementById('min-price').value,max:document.getElementById('max-price').value};
 for(const [key,value] of Object.entries(values)){if(value!=='')url.searchParams.set(key,value);else url.searchParams.delete(key)}
 history[push?'pushState':'replaceState'](null,'',url.pathname+url.search);
}
function hydrate(){
 const params=new URLSearchParams(globalThis.location?.search||'');
 filter=views.includes(params.get('view'))?params.get('view'):'tracked';page=Math.max(1,parseInt(params.get('page'),10)||1);
 pageSize=[25,50,100].includes(Number(params.get('size')))?Number(params.get('size')):50;document.getElementById('page-size').value=String(pageSize);
 document.getElementById('sort').value=['discount','saving','price','price-desc'].includes(params.get('sort'))?params.get('sort'):'discount';
 document.getElementById('search').value=(params.get('q')||'').slice(0,200);
 for(const key of ['min','max']){const value=params.get(key)||'';document.getElementById(key+'-price').value=value!==''&&Number.isFinite(Number(value))&&Number(value)>=0?value:''}
}
function goToPage(value){page=value;render();updateUrl(true)}
function resetPage(){page=1;render();updateUrl()}
async function refresh(){
 try{
  if(globalThis.location?.protocol==='file:'){state=JSON.parse(document.getElementById('snapshot').textContent);render();if(!globalThis.fileReload)globalThis.fileReload=setTimeout(()=>location.reload(),60000);return}
  const response=await fetch('/amazon-deals.json',{cache:'no-store'});if(!response.ok)throw Error('Report unavailable');state=await response.json();render();document.getElementById('error').textContent='';
 }catch(error){document.getElementById('error').textContent='Refresh unavailable; displayed prices may be stale.'}
}
hydrate();
['search','min-price','max-price'].forEach(id=>document.getElementById(id).addEventListener('input',resetPage));
document.getElementById('sort').addEventListener('change',resetPage);
document.getElementById('page-size').addEventListener('change',()=>{pageSize=Number(document.getElementById('page-size').value);resetPage()});
document.getElementById('clear-price').addEventListener('click',()=>{document.getElementById('min-price').value='';document.getElementById('max-price').value='';resetPage()});
document.querySelectorAll('[data-filter]').forEach(b=>b.addEventListener('click',()=>{filter=b.dataset.filter;resetPage()}));
document.getElementById('pagination').addEventListener('click',event=>{const button=event.target.closest('button[data-page]');if(button&&!button.disabled)goToPage(Number(button.dataset.page))});
globalThis.addEventListener?.('popstate',()=>{hydrate();render()});
refresh();setInterval(refresh,15000);
