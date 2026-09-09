"""QualiZeal Knowledge Fabric — telemetry dashboard (WS3 · PROVE).

A Power BI-style, multi-tab analytics surface served by the platform. Charts
are drawn with inline SVG (zero external dependencies, works fully offline).
Reads /api/analytics and /admin/sources with the filters the roadmap and
leadership asked for: window (last 24h / last 7d / all), per user, per role;
tokens in/out, model routing WITH the selector's reason, cost, and cost saved
by cache technique.
"""

DASHBOARD_HTML = r"""<!doctype html>
<html lang="en"><head>
<meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>QualiZeal Knowledge Fabric — Telemetry</title>
<link rel="icon" href="/static/assets/brand/logo/favicon-32.png">
<style>
:root{--surface:#FFFFFF;--ink:#0D1523;--panel:#F4F8FC;--line:#CFE0F0;--navy:#FFFFFF;--navy2:#FFFFFF;
--fg:#2B3B4A;--mut:#5A6B7C;--accent:#0096FF;--good:#0CA678;--warn:#E8A23A;--bad:#F53E5A;
--c1:#0096FF;--c2:#0CA678;--c3:#E8A23A;--c4:#7048E8;--c5:#4DD0E8;--c6:#F53E5A;}
*{box-sizing:border-box}html{color-scheme:light}body{margin:0;background:var(--surface);color:var(--fg);
font:14px/1.5 "Inter",system-ui,-apple-system,"Segoe UI",Roboto,sans-serif;font-variant-numeric:tabular-nums}
header{background:var(--surface);height:56px;padding:0 24px;
display:flex;align-items:center;gap:14px;border-bottom:1px solid var(--line)}
header h1{font-size:16px;margin:0;font-weight:600;color:var(--ink)}
header .lockup{height:18px;width:auto}
header .sub{color:var(--mut);font-size:12px}
.filters{display:flex;gap:10px;flex-wrap:wrap;padding:12px 24px;background:var(--surface);
border-bottom:1px solid var(--line);align-items:center}
select{background:var(--surface);color:var(--fg);border:1px solid var(--line);border-radius:8px;padding:7px 9px}
label{color:var(--mut);font-size:12px;margin-right:4px}
.tabs{display:flex;gap:2px;padding:0 24px;background:var(--surface);border-bottom:1px solid var(--line)}
.tab{padding:11px 16px;cursor:pointer;color:var(--mut);border-bottom:2px solid transparent;font-weight:600}
.tab.active{color:var(--accent);border-bottom-color:var(--accent)}
main{padding:20px 24px;max-width:1200px;margin:0 auto}
.grid{display:grid;gap:14px}.kpis{grid-template-columns:repeat(auto-fit,minmax(160px,1fr))}
.cards{grid-template-columns:repeat(auto-fit,minmax(320px,1fr))}
.card{background:var(--panel);border:1px solid var(--line);border-radius:14px;padding:16px}
.card h3{margin:0 0 10px;font-size:13px;color:var(--mut);font-weight:600;text-transform:uppercase;letter-spacing:.4px}
.kpi .v{font-size:26px;font-weight:700}.kpi .l{color:var(--mut);font-size:12px}
.kpi .d{font-size:12px;margin-top:3px}
table{width:100%;border-collapse:collapse;font-size:13px}
th,td{text-align:left;padding:7px 8px;border-bottom:1px solid var(--line)}
th{color:var(--mut);font-weight:600}
.pill{display:inline-block;padding:1px 8px;border-radius:20px;font-size:11px;font-weight:600}
.hidden{display:none}.legend{display:flex;gap:14px;flex-wrap:wrap;margin-top:8px;font-size:12px;color:var(--mut)}
.legend i{display:inline-block;width:10px;height:10px;border-radius:3px;margin-right:5px;vertical-align:middle}
.mono{font-family:JetBrains Mono,ui-monospace,monospace}
.bar-row{display:flex;align-items:center;gap:8px;margin:5px 0}.bar-row .n{width:150px;color:var(--mut);font-size:12px}
.bar-row .t{width:44px;text-align:right;font-size:12px}
.trk{flex:1;height:12px;background:var(--panel);border-radius:6px;overflow:hidden}.trk>i{display:block;height:100%}
.qz-watermark{position:fixed;right:24px;bottom:56px;width:220px;height:220px;pointer-events:none;z-index:0;
 background:url("/static/assets/brand/logo/qualizeal-mark.png") no-repeat center/contain;opacity:.04}
.qz-footer{height:40px;display:flex;align-items:center;justify-content:space-between;padding:0 24px;
 font-size:12px;color:#7C8DA1;border-top:1px solid var(--line);background:var(--surface)}
</style></head><body>
<header>
  <a href="/" title="QualiZeal Knowledge Fabric"><img class="lockup" src="/static/assets/brand/logo/qualizeal-lockup.png" alt="QualiZeal Knowledge Fabric"></a>
</header>
<div class="filters">
  <span><label>Window</label><select id="window">
    <option value="24h">Last 24 hours</option><option value="7d" selected>Last 7 days</option>
    <option value="all">All time</option></select></span>
  <span><label>User</label><select id="user"><option value="">All users</option></select></span>
  <span><label>Role</label><select id="role"><option value="">All roles</option>
    <option>asker</option><option>curator</option><option>admin</option><option>agent</option></select></span>
  <span style="color:var(--mut);font-size:12px" id="status"></span>
</div>
<div class="tabs" id="tabs"></div>
<main id="view"></main>
<div class="qz-watermark" aria-hidden="true"></div>
<footer class="qz-footer">
  <span>&copy; QualiZeal. All rights reserved.</span>
  <span>QualiZeal Knowledge Fabric &middot; Internal</span>
  <span>v0.3</span>
</footer>
<script>
const TABS=["Overview","Trust","Sources","Models","Usage","Cost & Caching"];
let TAB="Overview", TOKEN=null, DATA=null, SOURCES=null;
const FABRIC="qualizeal";   // single in-house fabric (no tenant selector)
const $=s=>document.querySelector(s);
const COL=["#0096FF","#0CA678","#E8A23A","#7048E8","#4DD0E8","#F53E5A"];
function fmt(n){return (n||0).toLocaleString()}
function money(n){return "$"+(n||0).toFixed(4)}

async function login(){
 const r=await fetch('/login',{method:'POST',body:JSON.stringify({tenant:FABRIC,subject:'admin'})});
 return (await r.json()).token;
}
async function load(){
 TOKEN=await login();
 const qs=new URLSearchParams({window:$('#window').value});
 if($('#user').value)qs.set('subject',$('#user').value);
 if($('#role').value)qs.set('role',$('#role').value);
 const h={'Authorization':'Bearer '+TOKEN};
 DATA=await (await fetch('/api/analytics?'+qs,{headers:h})).json();
 SOURCES=await (await fetch('/admin/sources',{headers:h})).json();
 // populate user filter
 const u=$('#user'); const cur=u.value; u.innerHTML='<option value="">All users</option>';
 Object.keys(DATA.per_user||{}).forEach(k=>{let o=document.createElement('option');o.value=o.text=k;u.add(o)});
 u.value=cur;
 $('#status').textContent=`${fmt(DATA.answers)} answers · ${DATA.window}`;
 render();
}
function tile(l,v,d,cls){return `<div class="card kpi"><div class="l">${l}</div><div class="v">${v}</div>${d?`<div class="d" style="color:${cls||'var(--mut)'}">${d}</div>`:''}</div>`}
function bars(obj,fmtv){const keys=Object.keys(obj||{});const max=Math.max(1,...keys.map(k=>obj[k]));
 return keys.map((k,i)=>`<div class="bar-row"><span class="n">${k}</span><div class="trk"><i style="width:${100*obj[k]/max}%;background:${COL[i%6]}"></i></div><span class="t">${(fmtv?fmtv(obj[k]):obj[k])}</span></div>`).join('')||'<div class="l" style="color:var(--mut)">no data</div>'}
function donut(obj){const keys=Object.keys(obj||{});const tot=keys.reduce((a,k)=>a+obj[k],0)||1;let acc=0;
 const segs=keys.map((k,i)=>{const frac=obj[k]/tot;const a0=acc*2*Math.PI-Math.PI/2;acc+=frac;const a1=acc*2*Math.PI-Math.PI/2;
  const x0=60+45*Math.cos(a0),y0=60+45*Math.sin(a0),x1=60+45*Math.cos(a1),y1=60+45*Math.sin(a1);
  const large=frac>0.5?1:0;return `<path d="M60,60 L${x0},${y0} A45,45 0 ${large} 1 ${x1},${y1} Z" fill="${COL[i%6]}"/>`}).join('');
 const leg=keys.map((k,i)=>`<span><i style="background:${COL[i%6]}"></i>${k} (${obj[k]})</span>`).join('');
 return `<svg width="120" height="120" viewBox="0 0 120 120">${segs}<circle cx="60" cy="60" r="26" fill="var(--panel)"/></svg><div class="legend">${leg}</div>`}
function line(series,key,color){if(!series||!series.length)return '<div class="l" style="color:var(--mut)">no data</div>';
 const w=520,h=140,pad=24;const xs=series.map(s=>s.bucket);const ys=series.map(s=>s[key]||0);
 const maxy=Math.max(1,...ys);const n=series.length;
 const pts=series.map((s,i)=>{const x=pad+(w-2*pad)*(n<2?0.5:i/(n-1));const y=h-pad-(h-2*pad)*(s[key]||0)/maxy;return [x,y]});
 const path=pts.map((p,i)=>(i?'L':'M')+p[0].toFixed(1)+','+p[1].toFixed(1)).join(' ');
 const dots=pts.map(p=>`<circle cx="${p[0].toFixed(1)}" cy="${p[1].toFixed(1)}" r="3" fill="${color}"/>`).join('');
 return `<svg width="100%" viewBox="0 0 ${w} ${h}"><path d="${path}" fill="none" stroke="${color}" stroke-width="2"/>${dots}<text x="${pad}" y="14" fill="var(--mut)" font-size="11">max ${maxy}</text></svg>`}

function render(){
 $('#tabs').innerHTML=TABS.map(t=>`<div class="tab ${t===TAB?'active':''}" onclick="setTab('${t}')">${t}</div>`).join('');
 const d=DATA||{}; let h='';
 if(TAB==="Overview"){
  h+=`<div class="grid kpis">
   ${tile('Answers',fmt(d.answers))}
   ${tile('Tokens in / out',fmt(d.tokens_in)+' / '+fmt(d.tokens_out))}
   ${tile('Total cost',money(d.total_cost))}
   ${tile('Cost saved (cache)',money(d.total_cost_saved),(d.cache_hit_rate*100).toFixed(0)+'% hit rate','var(--good)')}
   ${tile('Grounding avg',(d.grounding_avg*100||0).toFixed(0)+'%')}
   ${tile('Citation coverage',(d.citation_coverage*100||0).toFixed(0)+'%')}
   ${tile('p95 latency',(d.latency_p95_ms||0)+' ms')}
   ${tile('Clarify-back rate',(d.clarify_back_rate*100||0).toFixed(0)+'%')}
  </div>
  <div class="grid cards" style="margin-top:14px">
   <div class="card"><h3>Model routing — by level</h3>${donut(d.routing_by_level)}</div>
   <div class="card"><h3>Volume over ${d.window}</h3>${line(d.timeseries,'answers','#4f7cff')}</div>
  </div>`;
 }
 if(TAB==="Trust"){
  h+=`<div class="grid kpis">
   ${tile('Grounding avg',(d.grounding_avg*100||0).toFixed(0)+'%',null)}
   ${tile('Citation coverage',(d.citation_coverage*100||0).toFixed(0)+'%',null)}
   ${tile('Clarify-back rate',(d.clarify_back_rate*100||0).toFixed(0)+'%','honest refusals','var(--warn)')}
  </div>
  <div class="card" style="margin-top:14px"><h3>Answers by language (cited to English source)</h3>${bars(d.by_language)}</div>`;
 }
 if(TAB==="Sources"){
  const rows=(SOURCES&&SOURCES.sources||[]).map(s=>`<tr><td>${s.source}</td><td>${fmt(s.items)}</td>
   <td class="mono">${s.freshness_minutes} min</td></tr>`).join('')||'<tr><td colspan=3 style="color:var(--mut)">no sync yet</td></tr>';
  h+=`<div class="card"><h3>Source health — freshness in minutes, per source</h3>
   <table><tr><th>Source</th><th>Items</th><th>Freshness</th></tr>${rows}</table></div>`;
 }
 if(TAB==="Models"){
  h+=`<div class="grid cards">
   <div class="card"><h3>Routing by level (look-up → reason)</h3>${bars(d.routing_by_level)}</div>
   <div class="card"><h3>Routing by tier</h3>${bars(d.routing_by_tier)}</div>
   <div class="card"><h3>Why routed — reason codes (complexity classification)</h3>${bars(d.routing_reasons)}</div>
   <div class="card"><h3>By language</h3>${donut(d.by_language)}</div>
  </div>`;
 }
 if(TAB==="Usage"){
  const ur=Object.entries(d.per_user||{}).map(([k,v])=>`<tr><td>${k}</td><td>${fmt(v.answers)}</td><td>${fmt(v.tokens)}</td><td class="mono">${money(v.cost)}</td></tr>`).join('');
  const rr=Object.entries(d.per_role||{}).map(([k,v])=>`<tr><td><span class="pill" style="background:var(--panel)">${k}</span></td><td>${fmt(v.answers)}</td><td class="mono">${money(v.cost)}</td></tr>`).join('');
  h+=`<div class="grid cards">
   <div class="card"><h3>Per user</h3><table><tr><th>User</th><th>Answers</th><th>Tokens</th><th>Cost</th></tr>${ur||'<tr><td colspan=4 style=color:var(--mut)>no data</td></tr>'}</table></div>
   <div class="card"><h3>Per role</h3><table><tr><th>Role</th><th>Answers</th><th>Cost</th></tr>${rr||'<tr><td colspan=3 style=color:var(--mut)>no data</td></tr>'}</table></div>
   <div class="card"><h3>Answers over ${d.window}</h3>${line(d.timeseries,'answers','#3ecf8e')}</div>
   <div class="card"><h3>Tokens out over ${d.window}</h3>${line(d.timeseries,'tokens_out','#c77dff')}</div>
  </div>`;
 }
 if(TAB==="Cost & Caching"){
  h+=`<div class="grid kpis">
   ${tile('Total cost',money(d.total_cost))}
   ${tile('Total saved',money(d.total_cost_saved),'by caching','var(--good)')}
   ${tile('Cache hit rate',(d.cache_hit_rate*100||0).toFixed(0)+'%')}
   ${tile('Net cost',money((d.total_cost)))}
  </div>
  <div class="grid cards" style="margin-top:14px">
   <div class="card"><h3>Savings by technique (not just a total)</h3>${bars(d.savings_by_technique,money)}</div>
   <div class="card"><h3>Cost saved over ${d.window}</h3>${line(d.timeseries,'cost_saved','#3ecf8e')}</div>
  </div>`;
 }
 $('#view').innerHTML=h;
}
function setTab(t){TAB=t;render();}
window.setTab=setTab;
(function init(){
 ['window','user','role'].forEach(id=>$('#'+id).addEventListener('change',load));
 load();
})();
</script></body></html>"""
