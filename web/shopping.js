/* Browser-local shopping tools. Email alerts are separate server subscriptions. */
(() => {
  'use strict';
  const stores = {kmart:'Kmart',bigw:'Big W',target:'Target',officeworks:'Officeworks',jbhifi:'JB Hi-Fi',goodguys:'The Good Guys',myer:'Myer',supercheap:'Supercheap Auto',sephora:'Sephora',chemistwarehouse:'Chemist Warehouse',booktopia:'Booktopia',qbd:'QBD Books',ikea:'IKEA'};
  const esc = s => String(s ?? '').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  const money = n => '$'+Number(n).toFixed(2);
  const keys = {wishlist:'dealwatch_wishlist',searches:'dealwatch_saved_searches',keywords:'dealwatch_keyword_alerts'};
  function read(kind){try {const v=JSON.parse(localStorage.getItem(keys[kind])||'[]');return Array.isArray(v)?v.filter(x=>x&&typeof x==='object').slice(0,100):[];}catch(_){return [];}}
  function write(kind,items){localStorage.setItem(keys[kind],JSON.stringify(items));window.dispatchEvent(new CustomEvent('shoppingchange',{detail:kind}));}
  function message(text){let box=document.getElementById('shopping-status');if(!box){box=document.createElement('p');box.id='shopping-status';box.setAttribute('role','status');document.querySelector('main')?.prepend(box);}box.textContent=text;}
  function valid(p){return p && Object.hasOwn(stores,p.retailer)&&typeof p.sku==='string'&&p.sku.length>0&&p.sku.length<=200;}
  const identity=p=>p.retailer+'\n'+p.sku;
  function button(p){
    p={retailer:p.retailer,sku:String(p.sku),title:p.title,current_price:p.current_price??p.price};
    if(!valid(p))return '';
    const saved=read('wishlist').some(x=>identity(x)===identity(p));
    return `<button type="button" class="shopping-button wishlist-button" data-wishlist="${esc(encodeURIComponent(JSON.stringify(p)))}" aria-pressed="${saved}">${saved?'Saved to wishlist':'Save to wishlist'}</button>`;
  }
  function canonicalSearch(path,params){
    if(!['/catalogue.html','/search.html'].includes(path))throw Error('Unsupported search');
    const allowed=['q','retailers','cats','subcats','pmin','pmax','sort'];
    const clean=new URLSearchParams();
    for(const key of allowed)if(params.get(key))clean.set(key,params.get(key).slice(0,500));
    return path+'?'+clean.toString();
  }
  function saveSearch(path,params,label){
    const href=canonicalSearch(path,params);const items=read('searches').filter(x=>x.href!==href);
    if(items.length>=30)throw Error('You can save up to 30 searches. Remove one first.');
    write('searches',[{href,label:String(label||params.get('q')||'Catalogue filters').slice(0,120),created_at:Date.now()},...items]);
    message('Search saved. Open My shopping to use it again.');
  }
  async function rpc(name,body){
    const response=await fetch('/rest/v1/rpc/'+name,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)});
    const value=await response.json();
    if(!response.ok)throw Error(value.message||'Request failed. Please try again.');
    return value;
  }
  function keywordForm(){
    const root=document.getElementById('keyword-tools');if(!root)return;
    root.innerHTML=`<h2>Keyword price alerts</h2><p>Get email when new products match, or a previously emailed match becomes cheaper. Confirm your email first. Checks run hourly; retailer prices and stock can change.</p>
      <form id="keyword-form" class="shopping-form">
      <label>Keywords <input name="keywords" required minlength="2" maxlength="100" placeholder="e.g. LEGO Star Wars"></label>
      <label>Exclude words <input name="excluded" maxlength="100" placeholder="e.g. keyring case"></label>
      <p class="note">Every keyword must appear in the title, in any order. A title containing any excluded word is skipped. Marketplace sellers are excluded.</p>
      <fieldset><legend>Stores — leave unchecked for all</legend><div class="store-options">${Object.entries(stores).map(([key,label])=>`<label><input type="checkbox" name="stores" value="${key}"> ${label}</label>`).join('')}</div></fieldset>
      <div class="shopping-fields"><label>Minimum price $ <input type="number" name="min" min="0" max="100000" step="0.01" value="0" required></label><label>Maximum price $ <input type="number" name="max" min="0.01" max="100000" step="0.01" required placeholder="50.00"></label><label>Minimum discount % <input type="number" name="discount" min="0" max="100" step="1" value="0" required></label></div>
      <p class="note">Discount compares the current price with the retailer reference price. Use 0% to include products without a reference price.</p>
      <label>Email <input type="email" name="email" required maxlength="254" autocomplete="email"></label>
      <button class="shopping-button" type="submit">Send confirmation email</button><p role="status" id="keyword-result"></p></form><div id="keyword-list"></div>`;
    const query=new URLSearchParams(location.search);
    root.querySelector('[name=keywords]').value=query.get('q')||'';
    const form=document.getElementById('keyword-form');
    form.addEventListener('submit',async event=>{
      event.preventDefault();const data=new FormData(form),out=document.getElementById('keyword-result'),submit=form.querySelector('[type=submit]');
      const payload={p_email:data.get('email'),p_keywords:data.get('keywords').trim(),p_excluded_words:data.get('excluded').trim(),p_retailers:data.getAll('stores'),p_min_price:Number(data.get('min')),p_max_price:Number(data.get('max')),p_min_discount:Number(data.get('discount'))};
      if(payload.p_min_price>payload.p_max_price){out.textContent='Minimum price must not exceed maximum price.';return;}
      // Verify storage before registering; never lose the returned management token.
      try{localStorage.setItem(keys.keywords,JSON.stringify(read('keywords')));}catch(_){out.textContent='Browser storage is unavailable. Enable it to manage this alert here.';return;}
      submit.disabled=true;out.textContent='Requesting confirmation…';
      try{
        const token=await rpc('create_keyword_alert',payload);
        if(typeof token!=='string'||!token)throw Error('Unexpected response. Please try again.');
        const item={token,keywords:payload.p_keywords,min:payload.p_min_price,max:payload.p_max_price,stores:payload.p_retailers,status:'pending'};
        try{write('keywords',[item,...read('keywords')].slice(0,100));}
        catch(_){out.textContent='Alert requested, but this browser could not save it. The confirmation email includes a cancellation link.';return;}
        form.querySelector('[name=email]').value='';
        out.textContent='Check your email for confirmation. It will be sent on the next hourly check. The link expires after 7 days.';
      }catch(error){out.textContent=error.message;}finally{submit.disabled=false;}
    });
  }
  async function keywordLinks(){
    const query=new URLSearchParams(location.search),confirm=query.get('keyword_confirm'),cancel=query.get('keyword_cancel');
    if(!confirm&&!cancel)return;
    const root=document.getElementById('keyword-tools');if(!root)return;
    const box=document.createElement('div');box.className='shopping-notice';root.prepend(box);
    if(confirm){
      box.textContent='Confirming your keyword alert…';
      try{const ok=await rpc('confirm_keyword_alert',{p_token:confirm});box.textContent=ok?'Your keyword alert is active. New matches and lower prices will be emailed after scheduled checks.':'This confirmation link has expired or was cancelled.';if(ok){refreshKeywordStatuses();query.delete('keyword_confirm');history.replaceState({},'',location.pathname+(query.size?'?'+query:'')+location.hash);}}
      catch(_){box.textContent='Could not confirm. Reopen the email link to try again.';}
    }else{
      box.innerHTML='<p>Cancel this keyword alert? You will stop receiving its matching-product emails.</p><button type="button" class="shopping-button">Cancel keyword alert</button>';
      box.querySelector('button').onclick=async()=>{try{const ok=await rpc('cancel_keyword_alert',{p_token:cancel});box.textContent=ok?'Keyword alert cancelled.':'Alert not found.';if(ok){write('keywords',read('keywords').map(x=>x.token===cancel?{...x,status:'cancelled'}:x));query.delete('keyword_cancel');history.replaceState({},'',location.pathname+(query.size?'?'+query:'')+location.hash);}}catch(_){message('Could not cancel. Try again.');}};
    }
  }
  function renderSaved(){
    const root=document.getElementById('saved-searches');if(!root)return;
    const rows=read('searches');root.innerHTML='<h2>Saved searches</h2>'+(rows.length?rows.map((s,i)=>{
      let href;try{const url=new URL(s.href,location.origin);if(url.origin!==location.origin)throw Error();href=canonicalSearch(url.pathname,url.searchParams);}catch(_){return '';}
      return `<div class="shopping-row"><a href="${esc(href)}">${esc(s.label)}</a><button type="button" class="shopping-button" data-remove-search="${i}">Remove</button></div>`;
    }).join(''):'<p>Save a search from the search or catalogue page to use its filters again.</p>');
  }
  function renderKeywords(){
    const root=document.getElementById('keyword-list');if(!root)return;
    const rows=read('keywords');root.innerHTML='<h3>Your keyword alerts on this browser</h3>'+(rows.length?rows.map((a,i)=>`<div class="shopping-row"><div><strong>${esc(a.keywords)}</strong><p>${money(a.min)}–${money(a.max)} · ${esc((a.stores||[]).map(s=>stores[s]||s).join(', ')||'All stores')} · <span data-keyword-status="${i}">${esc(a.status||'pending')}</span></p></div><button type="button" class="shopping-button" data-cancel-keyword="${i}" ${a.status==='cancelled'?'disabled':''}>Cancel alert</button></div>`).join(''):'<p>No keyword alerts saved here yet.</p>');
  }
  let wishlistSerial=0;
  async function renderWishlist(){
    const root=document.getElementById('wishlist-items');if(!root)return;
    const serial=++wishlistSerial,rows=read('wishlist').filter(valid);
    root.innerHTML='<h2>Wishlist</h2>'+(rows.length?'<p role="status">Checking current catalogue prices…</p>':'<p>Choose “Save to wishlist” on a product, search result or catalogue listing.</p>');
    if(!rows.length)return;
    let prices=new Map(),failed=false;
    try{
      const quote=value=>'"'+String(value).replace(/["\\]/g,'\\$&')+'"';
      const params=new URLSearchParams({select:'retailer,sku,title,current_price',or:'('+rows.map(p=>`and(retailer.eq.${quote(p.retailer)},sku.eq.${quote(p.sku)})`).join(',')+')',limit:'100'});
      const response=await fetch('/rest/v1/product_search?'+params);
      if(!response.ok)throw Error();const data=await response.json();
      for(const p of data)prices.set(identity(p),p);
    }catch(_){failed=true;}
    if(serial!==wishlistSerial)return;
    root.innerHTML='<h2>Wishlist</h2>'+(failed?'<p role="status">Current prices are unavailable. Showing your saved prices.</p>':'')+rows.map(p=>{
      const current=prices.get(identity(p));const amount=current?.current_price??p.current_price;
      return `<div class="shopping-row"><div><a href="/p/${encodeURIComponent(p.retailer)}/${encodeURIComponent(p.sku)}">${esc(current?.title||p.title)}</a><p>${esc(stores[p.retailer])} · ${amount==null?'Price unavailable':money(amount)}${current?' · latest known price':failed?' · saved price':' · saved price; listing not currently available'}</p></div>${button(p)}</div>`;
    }).join('');
  }
  function updateButtons(){document.querySelectorAll('[data-wishlist]').forEach(b=>{try{const p=JSON.parse(decodeURIComponent(b.dataset.wishlist));const saved=read('wishlist').some(x=>identity(x)===identity(p));b.setAttribute('aria-pressed',String(saved));b.textContent=saved?'Saved to wishlist':'Save to wishlist';}catch(_){}});}
  document.addEventListener('click',async event=>{
    const b=event.target.closest('button');if(!b)return;
    try{
      if(b.hasAttribute('data-wishlist')){
        const p=JSON.parse(decodeURIComponent(b.dataset.wishlist));if(!valid(p))return;
        const rows=read('wishlist'),saved=rows.some(x=>identity(x)===identity(p));
        if(!saved&&rows.length>=100)throw Error('Your wishlist holds up to 100 products. Remove one first.');
        write('wishlist',saved?rows.filter(x=>identity(x)!==identity(p)):[p,...rows]);message(saved?'Removed from wishlist.':'Saved to wishlist. Open My shopping to see it.');
      }
      if(b.hasAttribute('data-remove-search'))write('searches',read('searches').filter((_,i)=>i!==Number(b.dataset.removeSearch)));
      if(b.hasAttribute('data-cancel-keyword')){
        const i=Number(b.dataset.cancelKeyword),item=read('keywords')[i];b.disabled=true;
        if(!await rpc('cancel_keyword_alert',{p_token:item.token}))throw Error('Could not find this alert.');
        write('keywords',read('keywords').map(x=>x.token===item.token?{...x,status:'cancelled'}:x));message('Keyword alert cancelled.');
      }
    }catch(error){b.disabled=false;message(error.message||'Browser storage is unavailable. Changes could not be saved.');}
  });
  function render(event){const kind=event?.detail;if(!kind||kind==='searches')renderSaved();if(!kind||kind==='keywords')renderKeywords();if(!kind||kind==='wishlist'){renderWishlist();updateButtons();}}
  window.addEventListener('shoppingchange',render);
  window.addEventListener('storage',event=>{if(Object.values(keys).includes(event.key))render();});
  function refreshKeywordStatuses(){
    read('keywords').filter(x=>x.status!=='cancelled').slice(0,20).forEach(async item=>{
      try{const status=await rpc('keyword_alert_status',{p_token:item.token});if(status&&status!==item.status)write('keywords',read('keywords').map(x=>x.token===item.token?{...x,status}:x));}catch(_){}
    });
  }
  document.addEventListener('DOMContentLoaded',()=>{
    keywordForm();render();keywordLinks();
    // Fetch status once on load, rather than trusting browser-local state.
    refreshKeywordStatuses();
  });
  window.DealwatchShopping={button,saveSearch,read,canonicalSearch};
})();
