/* Batched, progressively loaded history. Prices remain usable if this fails. */
(function(root){
  'use strict';
  const key = item => JSON.stringify([item.retailer,item.sku]);
  const cache = new Map();
  const money = n => new Intl.NumberFormat('en-AU',{style:'currency',currency:'AUD',maximumFractionDigits:2}).format(n);
  function chart(raw, now=Date.now()) {
    const start=now-30*86400000;
    const points=(Array.isArray(raw)?raw:[]).filter(p=>Array.isArray(p)&&Number.isFinite(Date.parse(p[0]))&&Number(p[1])>0&&Number.isFinite(Number(p[1])))
      .map(p=>[Date.parse(p[0]),Number(p[1])]).filter(p=>p[0]>=start-60000&&p[0]<=now).sort((a,b)=>a[0]-b[0]);
    if(points.length<2) return {html:'<span class="spark-note">History building</span>',state:'sparse'};
    const low=Math.min(...points.map(p=>p[1])),high=Math.max(...points.map(p=>p[1]));
    const x=t=>4+Math.max(0,Math.min(1,(t-start)/(now-start)))*192;
    const y=v=>high===low?22:36-(v-low)/(high-low)*28;
    let d=`M${x(points[0][0]).toFixed(2)},${y(points[0][1]).toFixed(2)}`;
    for(const [t,v] of points.slice(1)) d+=` H${x(t).toFixed(2)} V${y(v).toFixed(2)}`;
    const first=points[0],last=points.at(-1);
    const label=`Recorded prices over the last 30 days, daily last price. ${money(first[1])} to ${money(last[1])}. Range ${money(low)} to ${money(high)}. Gaps before tracking began are left blank.`;
    return {state:'ready',html:`<div class="spark-caption"><span>Price history · 30d</span><span>${high===low?'No change':`${money(low)}–${money(high)}`}</span></div><svg viewBox="0 0 200 42" role="img" aria-label="${label}" preserveAspectRatio="none"><path class="spark-guide" d="M4 37 H196"/><path class="spark-line" d="${d}"/><circle cx="${x(last[0]).toFixed(2)}" cy="${y(last[1]).toFixed(2)}" r="2.5"/></svg>`};
  }
  function paint(node, value){node.innerHTML=value.html;node.dataset.state=value.state;}
  async function mount(container){
    const nodes=[...container.querySelectorAll('[data-history-retailer]')];
    const pending=new Map();
    for(const node of nodes){
      const item={retailer:node.dataset.historyRetailer,sku:node.dataset.historySku};
      const id=key(item), saved=cache.get(id);
      if(saved&&Date.now()-saved.at<300000){paint(node,saved.value);continue;}
      if(!pending.has(id))pending.set(id,{item,nodes:[]});
      pending.get(id).nodes.push(node);
    }
    const entries=[...pending.values()];
    // Sequential batches; a new filter disconnects old nodes and stops work.
    for(let i=0;i<entries.length;i+=24){
      const batch=entries.slice(i,i+24).filter(e=>e.nodes.some(n=>n.isConnected));
      if(!batch.length)continue;
      const controller=new AbortController(), timer=setTimeout(()=>controller.abort(),12000);
      try{
        const response=await fetch('/rest/v1/rpc/feed_price_history',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({p_items:batch.map(e=>e.item)}),signal:controller.signal});
        if(!response.ok)throw new Error('History unavailable');
        const rows=await response.json();
        if(!Array.isArray(rows))throw new Error('Invalid history');
        const found=new Map(rows.map(row=>[key(row),row.points]));
        for(const entry of batch){
          const value=chart(found.get(key(entry.item)));
          if(cache.size>240)cache.delete(cache.keys().next().value);
          cache.set(key(entry.item),{at:Date.now(),value});
          for(const node of entry.nodes)if(node.isConnected)paint(node,value);
        }
      }catch(_){
        for(const entry of batch)for(const node of entry.nodes)if(node.isConnected)
          paint(node,{html:'<span class="spark-note">History unavailable · open product to retry</span>',state:'error'});
      }finally{clearTimeout(timer);}
    }
  }
  if(typeof module==='object'&&module.exports)module.exports={chart};
  else root.DealwatchSparklines={mount,chart};
})(typeof window==='undefined'?globalThis:window);
