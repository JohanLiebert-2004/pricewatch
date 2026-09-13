/* Shared comparison rules: only validated, equivalent GTINs are exact matches.
 * GS1: https://www.gs1.org/services/how-calculate-check-digit-manually
 * https://www.gs1.org/edi-xml/technical-user-guide/Item_Numbers
 */
(function(root){
  'use strict';
  function gtin(value){
    const digits=String(value ?? '').trim();
    if(![8,12,13,14].includes(digits.length) || !/^\d+$/.test(digits) || /^0+$/.test(digits)) return null;
    let sum=0;
    for(let i=digits.length-2, weight=3;i>=0;i--,weight=4-weight) sum+=Number(digits[i])*weight;
    if((10-sum%10)%10 !== Number(digits.at(-1))) return null;
    return digits.padStart(14,'0');
  }
  function aliases(value){
    const code=gtin(value);
    if(!code) return [];
    return [8,12,13,14].map(n=>code.slice(-n)).filter(v=>gtin(v)===code);
  }
  function price(row){return row.current_price != null && Number.isFinite(Number(row.current_price)) && Number(row.current_price)>0;}
  function fresh(row,now=Date.now()){
    const checked=Date.parse(row.last_seen || row.price_updated_at || '');
    return price(row) && Number.isFinite(checked) && checked<=now && now-checked<=36*60*60*1000;
  }
  function exactRows(product,rows){
    const code=gtin(product.gtin), seen=new Set();
    if(!code) return [];
    return rows.filter(row=>{
      const key=`${row.retailer}/${row.sku}`;
      if(!row.retailer || !row.sku || row.retailer===product.retailer || !price(row) || gtin(row.gtin)!==code || seen.has(key)) return false;
      seen.add(key);return true;
    }).sort((a,b)=>Number(a.current_price)-Number(b.current_price) || Number(a.is_marketplace===true)-Number(b.is_marketplace===true));
  }
  const stop=new Set(['the','and','with','for','from','set','pack','size','new','table','chair','desk','bed','white','black','grey','gray','cm','mm','m','l']);
  const words=s=>String(s||'').toLowerCase().match(/[a-z0-9]+/g)||[];
  const tokens=p=>new Set(words(p.title).filter(w=>w.length>1 && !stop.has(w) && !words(p.brand).includes(w)));
  const codes=p=>new Set(String(p.title||'').toLowerCase().match(/\b(?=[a-z0-9-]*\d)[a-z0-9-]{4,}\b/g)||[]);
  const overlap=(a,b)=>[...a].filter(w=>b.has(w)).length;
  function suggestions(product,rows){
    const result=[],seen=new Set(),pt=tokens(product),pc=codes(product),barcode=gtin(product.gtin);
    for(const row of rows){
      const key=`${row.retailer}/${row.sku}`;
      if(seen.has(key) || !price(row) || row.retailer===product.retailer || !fresh(row)) continue;
      if(barcode && gtin(row.gtin)===barcode) continue;
      if(!product.brand || String(row.brand||'').toLowerCase()!==String(product.brand).toLowerCase()) continue;
      const rt=tokens(row),common=overlap(pt,rt),union=new Set([...pt,...rt]).size||1;
      const model=overlap(pc,codes(row));
      if(model || common>=2 && common/union>=.32){
        seen.add(key);result.push({...row,label:model?'Model-code suggestion':'Similar listing',score:model?2:1});
      }
    }
    return result.sort((a,b)=>b.score-a.score || Number(a.current_price)-Number(b.current_price)).slice(0,5);
  }
  const esc=s=>String(s??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  const money=n=>new Intl.NumberFormat('en-AU',{style:'currency',currency:'AUD'}).format(Number(n));
  const link=r=>`/p/${encodeURIComponent(r.retailer)}/${encodeURIComponent(r.sku)}`;
  function checked(row){
    const date=new Date(row.last_seen||row.price_updated_at||'');
    return Number.isFinite(+date)?`Checked ${date.toLocaleString('en-AU',{day:'numeric',month:'short',year:'numeric',hour:'numeric',minute:'2-digit'})}`:'Check date unavailable';
  }
  function renderExact(product,rows,element,labels,truncated=false){
    const matches=exactRows(product,rows), current=matches.filter(r=>fresh(r)), older=matches.filter(r=>!fresh(r));
    const offers=[product,...current].filter(price).sort((a,b)=>Number(a.current_price)-Number(b.current_price));
    const best=current.length ? Math.min(...offers.filter(r=>fresh(r)).map(r=>Number(r.current_price))) : null;
    const diff=fresh(product) && current.length?Number(product.current_price)-best:0;
    const title='<h2>Compare the same barcode</h2>';
    const summary=diff>.005?`<p class="comparison-saving">${money(diff)} lower at another retailer</p>`:'';
    const notice='<p class="comparison-note">Matching retailer-supplied barcodes. Check size, pack, seller, stock and delivery before buying. Prices exclude delivery. <a href="/how-it-works.html#comparisons">How matching works</a></p>';
    const tableRows=list=>list.map(row=>{
      const isCurrent=row.retailer===product.retailer && row.sku===product.sku;
      const recent=fresh(row),lowest=recent && best!==null && Number(row.current_price)===best;
      return `<tr><td><a href="${link(row)}">${esc(labels[row.retailer]||row.retailer)}</a>${isCurrent?'<small>This listing</small>':''}${row.is_marketplace?'<small class="pill mkt">Marketplace seller</small>':''}<small>${esc(row.title)}</small></td><td class="comparison-price">${money(row.current_price)}${lowest?'<small class="pill deal">Lowest listed here</small>':''}</td><td><span>${esc(checked(row))}</span>${!recent?'<small class="comparison-stale">Older check · verify price</small>':''}</td></tr>`;
    }).join('');
    const table=list=>`<div class="comparison-table-wrap" role="region" aria-label="Barcode-matched retailer prices" tabindex="0"><table class="comparison-table"><thead><tr><th scope="col">Retailer</th><th scope="col">Item price</th><th scope="col">Last checked</th></tr></thead><tbody>${tableRows(list)}</tbody></table></div>`;
    element.innerHTML=title+summary+notice+(current.length?table(offers):'<p>No recently checked barcode matches at another retailer in our catalogue.</p>')+(older.length?`<details class="comparison-older"><summary>${older.length} older matching ${older.length===1?'listing':'listings'}</summary><p>These checks are older than 36 hours or have no check date. They are excluded from savings comparisons.</p>${table(older)}</details>`:'')+(truncated?'<p class="comparison-note">Showing up to 50 matching listings; this comparison may be incomplete.</p>':'');
    element.dataset.state='ready';
  }
  async function request(params){
    const response=await fetch(`/rest/v1/product_search?${params}`,{signal:AbortSignal.timeout(15000)});
    if(!response.ok) throw new Error('Comparison request failed');
    return response.json();
  }
  async function mount({product,element,suggestionElement,labels}){
    const select='retailer,sku,gtin,title,brand,current_price,is_marketplace,last_seen,price_updated_at';
    const base=()=>new URLSearchParams({select,retailer:`neq.${product.retailer}`,current_price:'gt.0'});
    const barcode=gtin(product.gtin);
    const loadExact=async()=>{
      if(!barcode){
        element.innerHTML='<h2>Compare the same barcode</h2><p>A valid product barcode is not available for this listing, so we cannot confirm an exact match at another retailer.</p><a href="/how-it-works.html#comparisons">How matching works</a>';
        element.dataset.state='unavailable';element.setAttribute('aria-busy','false');return;
      }
      element.setAttribute('aria-busy','true');element.dataset.state='loading';
      element.innerHTML='<h2>Compare the same barcode</h2><p>Checking other retailers…</p>';
      try{
        const params=base();params.set('gtin',`in.(${aliases(barcode).join(',')})`);
        params.set('order','last_seen.desc.nullslast,retailer.asc,sku.asc');params.set('limit','50');
        const rows=await request(params);renderExact(product,rows,element,labels,rows.length===50);
      }catch(e){
        element.dataset.state='error';
        element.innerHTML='<h2>Compare the same barcode</h2><p>We could not check other retailers right now.</p><button type="button" class="comparison-retry">Try comparison again</button>';
        element.querySelector('button').addEventListener('click',loadExact);
      }finally{element.setAttribute('aria-busy','false');}
    };
    const loadSuggestions=async()=>{
      suggestionElement.hidden=true;
      const brand=String(product.brand||'').trim();if(brand.length<3) return;
      try{
        const params=base();params.set('brand',`eq.${brand}`);params.set('order','last_seen.desc');params.set('limit','80');
        const rows=suggestions(product,await request(params));if(!rows.length)return;
        suggestionElement.innerHTML='<h2>Similar products to explore</h2><p class="comparison-note">These are model or title suggestions, not confirmed identical products. Compare variants and pack sizes yourself.</p><div class="related-list">'+rows.map(row=>`<a class="related-item" href="${link(row)}"><div><strong>${esc(row.title)}</strong><small>${esc(labels[row.retailer]||row.retailer)} · ${esc(row.label)}${row.is_marketplace?' · Marketplace seller':''}</small><small>${esc(checked(row))}</small></div><div class="price">${money(row.current_price)}</div></a>`).join('')+'</div>';
        suggestionElement.hidden=false;
      }catch(e){ /* Optional suggestions never hide or delay barcode comparisons. */ }
    };
    await Promise.all([loadExact(),loadSuggestions()]);
  }
  const api={gtin,aliases,price,fresh,exactRows,suggestions,mount};
  if(typeof module!=='undefined' && module.exports) module.exports=api;
  root.DealwatchComparisons=api;
})(typeof window!=='undefined'?window:globalThis);
