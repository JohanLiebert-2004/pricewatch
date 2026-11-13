/* Black Friday: public read-only API, bounded per-store shortlist, no scraped prices in the browser. */
(() => {
  'use strict';
  const $ = id => document.getElementById(id);
  const esc = value => String(value ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  const safeURL = value => { try { const u = new URL(value, location.origin); return ['https:', 'http:'].includes(u.protocol) ? u.href : ''; } catch (_) { return ''; } };
  const money = value => new Intl.NumberFormat('en-AU', {style:'currency',currency:'AUD',maximumFractionDigits:Number(value)%1 ? 2 : 0}).format(value);
  const dateLabel = value => new Date(value).toLocaleDateString('en-AU', {day:'numeric',month:'short'});
  const MAX_REVIEW_AGE = 48 * 3600000, FRESH_AGE = 36 * 3600000;
  const state = {category:'all',store:'all',discount:0,sort:'recommended',budget:false,query:'',visible:24};
  let stores = [], curated = [], tracked = [], loading = false, failures = 0, checkedStores = 0, loadedAt = null, sourceFailed = false;
  function active(row) {
    const now = Date.now();
    return row.kind === 'tracked'
      ? Number.isFinite(Date.parse(row.checked_at)) && now-Date.parse(row.checked_at) <= FRESH_AGE
      : Number.isFinite(Date.parse(row.checked_at)) && now-Date.parse(row.checked_at) <= MAX_REVIEW_AGE && (!row.expires_at || Date.parse(row.expires_at) > now);
  }
  function status() {
    const el = $('bf-status');
    el.hidden = false;
    el.dataset.error = String(sourceFailed || failures > 0);
    if (loading) el.textContent = 'Checking tracked stores against their current catalogue prices… Sourced early offers are available below.';
    else if (sourceFailed) el.textContent = 'The sourced offer file could not load. Please refresh this page. Official promotion links below remain available.';
    else if (failures) el.textContent = `${checkedStores} of ${stores.filter(s=>s.tracked).length} tracked stores checked; ${failures} could not be verified. Showing only verified tracked results and recently reviewed early offers. Try Refresh to check again.`;
    else el.textContent = `Tracked listings checked across ${checkedStores} supported stores. Early offers are reviewed separately; their review dates appear on each card.`;
    $('bf-updated').textContent = loadedAt ? `Tracked check ${loadedAt.toLocaleTimeString('en-AU',{hour:'2-digit',minute:'2-digit'})} · your local time` : '';
  }
  function rows() {
    const keys = new Set();
    // Prefer independently catalogued prices over a manual offer for the same retailer/SKU.
    return [...tracked, ...curated].filter(d => {
      if (!active(d)) return false;
      const key = `${d.retailer}:${d.sku || d.id}`;
      if (keys.has(key)) return false;
      keys.add(key); return true;
    });
  }
  const score = d => d.pct_off + (d.is_30d_low ? 12 : 0) + Math.min(20, (d.reference_price-d.price)/50);
  function recommend(items) {
    const sorted = [...items].sort((a,b)=>score(b)-score(a) || a.id.localeCompare(b.id));
    const output = [], seen = new Map();
    // Three picks per store per pass keeps a large catalogue from taking over.
    for (let pass=1; sorted.length; pass++) {
      for (let i=0;i<sorted.length;) {
        const d=sorted[i], count=seen.get(d.retailer)||0;
        if(count<pass*3){output.push(d);seen.set(d.retailer,count+1);sorted.splice(i,1);}else i++;
      }
    }
    return output;
  }
  function card(d) {
    const store = stores.find(s=>s.id===d.retailer)?.name || d.retailer;
    const history = d.kind==='tracked' ? `/p/${encodeURIComponent(d.retailer)}/${encodeURIComponent(d.sku)}` : '';
    const href = history || safeURL(d.url);
    const image = d.image_url ? (d.kind==='tracked' ? `https://159-13-59-184.sslip.io/img?u=${encodeURIComponent(d.image_url)}` : safeURL(d.image_url)) : '';
    return `<article class="bf-card"><a class="bf-image" href="${esc(href)}" ${history?'':'target="_blank" rel="noopener noreferrer"'} aria-label="${esc(d.title)}"><span class="bf-badge">${d.pct_off}% off</span><span class="bf-image-placeholder" ${image?'hidden':''}>${esc(store.slice(0,2))}</span>${image?`<img src="${esc(image)}" width="260" height="220" loading="lazy" alt="${esc(d.title)}">`:''}${d.is_30d_low?'<span class="bf-low">Recorded 30-day low</span>':''}</a><div class="bf-card-body"><div class="bf-store-line"><span>${esc(store)}</span><span>${d.kind==='tracked'?'Tracked discount':'Early offer'}</span></div><h3><a href="${esc(href)}" ${history?'':'target="_blank" rel="noopener noreferrer"'}>${esc(d.title)}</a></h3><div class="bf-prices"><strong>${money(d.price)}</strong><del>${money(d.reference_price)}</del></div><p class="bf-saving">Save ${money(d.reference_price-d.price)}</p><p class="bf-reference">Compared with ${esc(d.reference_source)}<br>${d.kind==='tracked'?'Listing checked':'Source reviewed'} ${esc(dateLabel(d.checked_at))}${d.kind==='curated'?` · <a href="${esc(safeURL(d.source_url))}" target="_blank" rel="noopener noreferrer">Official source</a>`:''}</p>${d.terms?`<p class="bf-terms">${esc(d.terms)}</p>`:''}<div class="bf-card-foot"><a class="bf-card-link" href="${esc(safeURL(d.url))}" target="_blank" rel="noopener noreferrer">Check price at ${esc(store)} <span aria-hidden="true">↗</span></a>${history?`<a class="bf-card-link" href="${esc(history)}">Price history &amp; alert <span aria-hidden="true">→</span></a>`:''}</div></div></article>`;
  }
  function render() {
    let items = rows().filter(d => (state.category==='all'||d.category===state.category) && (state.store==='all'||d.retailer===state.store) && d.pct_off>=state.discount && (!state.budget||d.price<100) && (!state.query||`${d.title} ${stores.find(s=>s.id===d.retailer)?.name||d.retailer}`.toLowerCase().includes(state.query.toLowerCase())));
    const sorts = {discount:(a,b)=>b.pct_off-a.pct_off,saving:(a,b)=>(b.reference_price-b.price)-(a.reference_price-a.price),price:(a,b)=>a.price-b.price,recent:(a,b)=>Date.parse(b.checked_at)-Date.parse(a.checked_at)};
    items = state.sort==='recommended' ? recommend(items) : items.sort(sorts[state.sort]);
    $('bf-count').textContent = `${items.length} ${items.length===1?'deal':'deals'}${loading?' · checking stores…':''}`;
    $('bf-list').setAttribute('aria-busy',String(loading));
    $('bf-list').innerHTML = items.length ? items.slice(0,state.visible).map(card).join('') : `<div class="bf-empty"><h3>${loading?'Checking the stores…':'No matches in this shortlist'}</h3><p>${loading?'Recently reviewed early offers and fresh tracked prices will appear here.':'Try another filter, refresh tracked deals, or search the full catalogue.'}</p>${loading?'':'<a href="/catalogue.html">Browse the catalogue ↗</a>'}</div>`;
    $('bf-more').hidden = items.length<=state.visible;
    $('bf-more').textContent = `Show ${Math.min(24,Math.max(0,items.length-state.visible))} more deals`;
    $('bf-list').querySelectorAll('img').forEach(img=>img.addEventListener('error',()=>{img.hidden=true;img.previousElementSibling.hidden=false;},{once:true}));
    status();
  }
  async function get(url) {
    const response = await fetch(url,{cache:'no-store',signal:AbortSignal.timeout(20000)});
    if(!response.ok) throw new Error(`Request failed (${response.status})`);
    const data = await response.json();
    if(!Array.isArray(data)) throw new Error('Invalid catalogue response');
    return data;
  }
  async function loadStore(store) {
    const query=new URLSearchParams({select:'retailer,sku,title,category,price,reference_price,reference_source,pct_off,is_30d_low,image_url,url',retailer:`eq.${store.id}`,is_marketplace:'is.false',pct_off:'gte.20',order:'pct_off.desc,price.asc,sku.asc',limit:'12'});
    // Extreme reference-price gaps need individual review before promotion here.
    query.append('pct_off','lte.85');
    const candidates=await get(`/rest/v1/discount_feed?${query}`);
    if(!candidates.length) return [];
    const skus=candidates.map(d=>`"${String(d.sku).replace(/["\\]/g,'')}"`).join(',');
    const verify=new URLSearchParams({select:'retailer,sku,current_price,last_seen,is_marketplace',retailer:`eq.${store.id}`,sku:`in.(${skus})`,last_seen:`gte.${new Date(Date.now()-FRESH_AGE).toISOString()}`,is_marketplace:'is.false',limit:'12'});
    const current=await get(`/rest/v1/product_search?${verify}`);
    const live=new Map(current.map(p=>[String(p.sku),p]));
    return candidates.filter(d=>{
      const p=live.get(String(d.sku));
      return p && p.is_marketplace===false && Number(p.current_price)===Number(d.price) && Number(d.price)>0 && Number(d.reference_price)>Number(d.price) && safeURL(d.url) && Number.isFinite(Date.parse(p.last_seen)) && Date.now()-Date.parse(p.last_seen)<=FRESH_AGE;
    }).map(d=>({...d,id:`${d.retailer}-${d.sku}`,price:Number(d.price),reference_price:Number(d.reference_price),pct_off:Math.round((1-Number(d.price)/Number(d.reference_price))*100),kind:'tracked',checked_at:live.get(String(d.sku)).last_seen}));
  }
  async function refresh() {
    if(loading||sourceFailed) return;
    loading=true;failures=0;checkedStores=0;tracked=[];
    $('bf-refresh').disabled=true;render();
    const queue=stores.filter(s=>s.tracked);
    // Four read-only API tasks at a time; these never contact retailer sites.
    try {
      await Promise.all(Array.from({length:Math.min(4,queue.length)},async()=>{
        while(queue.length){const store=queue.shift();try{tracked.push(...await loadStore(store));checkedStores++;}catch(_){failures++;}render();}
      }));
    } finally { loading=false;loadedAt=new Date();$('bf-refresh').disabled=false;render(); }
  }
  function filters() {
    Object.assign(state,{store:$('bf-store').value,discount:Number($('bf-discount').value),sort:$('bf-sort').value,budget:$('bf-budget').checked,query:$('bf-query').value.trim(),visible:24});
    render();
  }
  function countdown() {
    const days=Math.ceil((Date.parse('2026-11-27T00:00:00+11:00')-Date.now())/86400000);
    $('bf-countdown').textContent=days>0?`${days} days to go · early offers now` : Date.now()<=Date.parse('2026-11-30T23:59:59+11:00')?'Cyber Weekend is here':'Cyber Weekend has ended';
  }
  $('bf-search-form').addEventListener('submit',e=>{e.preventDefault();filters();});
  $('bf-query').addEventListener('input',filters);
  ['bf-store','bf-discount','bf-sort','bf-budget'].forEach(id=>$(id).addEventListener('change',filters));
  $('bf-categories').addEventListener('click',e=>{const button=e.target.closest('[data-category]');if(!button)return;state.category=button.dataset.category;state.visible=24;document.querySelectorAll('[data-category]').forEach(b=>b.setAttribute('aria-pressed',String(b===button)));render();});
  $('bf-more').addEventListener('click',()=>{const firstNew=state.visible;state.visible+=24;render();const link=$('bf-list').children[firstNew]?.querySelector('a');link?.focus({preventScroll:true});link?.scrollIntoView({block:'nearest'});});
  $('bf-reset').addEventListener('click',()=>{$('bf-query').value='';$('bf-store').value='all';$('bf-discount').value='0';$('bf-sort').value='recommended';$('bf-budget').checked=false;state.category='all';document.querySelectorAll('[data-category]').forEach(b=>b.setAttribute('aria-pressed',String(b.dataset.category==='all')));filters();});
  $('bf-refresh').addEventListener('click',refresh);
  async function init() {
    countdown();
    try {
      const response=await fetch('/black-friday-offers.json',{cache:'no-store',signal:AbortSignal.timeout(15000)});
      if(!response.ok)throw new Error('Offers unavailable');
      const data=await response.json();
      if(!Array.isArray(data.products)||!Array.isArray(data.stores))throw new Error('Invalid offers');
      stores=data.stores;
      curated=data.products.filter(d=>Number(d.price)>0&&Number(d.reference_price)>Number(d.price)&&safeURL(d.url)).map(d=>({...d,price:Number(d.price),reference_price:Number(d.reference_price),pct_off:Math.round((1-d.price/d.reference_price)*100),kind:'curated',checked_at:d.reviewed_at||data.reviewed_at}));
      $('bf-store').innerHTML='<option value="all">All stores</option>'+stores.filter(s=>s.tracked||curated.some(d=>d.retailer===s.id)).sort((a,b)=>a.name.localeCompare(b.name)).map(s=>`<option value="${esc(s.id)}">${esc(s.name)}</option>`).join('');
      $('bf-stores').innerHTML=stores.map(s=>`<a href="${esc(safeURL(s.url))}" target="_blank" rel="noopener noreferrer">${esc(s.name)} <span aria-hidden="true">↗</span></a>`).join('');
      render();await refresh();
    } catch(_) { sourceFailed=true;render(); }
  }
  init();
  // Expire old offers in long-lived tabs. No unattended retailer requests.
  setInterval(()=>{countdown();render();},60000);
})();
