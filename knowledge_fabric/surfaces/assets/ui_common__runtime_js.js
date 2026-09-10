
const KF=(()=>{
 const DIR=window.KF_DIRECTORY||{tenants:[],users:{},questions:{}};
 // Deploy-base indirection: '' on the server, the repo path under GitHub
 // Pages, '/kf' behind an AWS subpath. Set by the host page before this runs;
 // every navigation and API path is prefixed with it so one build serves any base.
 const BASE=(window.KF_BASE||'');
 // Optional path remap for embeds where the surfaces live at different paths
 // (the static showcase serves the Workspace at /workspace, not /). Default
 // identity, so the server is unaffected.
 function route(p){return (window.KF_ROUTES&&window.KF_ROUTES[p])||p}
 function nav(p){location.href=BASE+route(p)}
 function applyBase(){document.querySelectorAll('[data-path]').forEach(a=>{
  a.setAttribute('href',BASE+route(a.getAttribute('data-path')))})}
 const $=s=>document.querySelector(s);
 const $$=s=>Array.from(document.querySelectorAll(s));
 const esc=v=>String(v==null?'':v).replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
 const num=n=>Number(n||0).toLocaleString();
 const pct=(x,d)=>((Number(x)||0)*100).toFixed(d==null?0:d)+'%';
 const money=n=>'$'+Number(n||0).toFixed(4);
 const ms=x=>x==null?'—':(x<1000?x.toFixed(1)+' ms':(x/1000).toFixed(2)+' s');
 const when=t=>{if(t==null)return '—';const d=new Date(t<1e12?t*1000:t);return isNaN(d)?'—':d.toLocaleString()};
 const ago=t=>{if(t==null)return '—';const s=Math.max(0,(Date.now()-(t<1e12?t*1000:t))/1000);
  return s<60?Math.round(s)+'s ago':s<3600?Math.round(s/60)+' min ago':s<86400?(s/3600).toFixed(1)+' h ago':(s/86400).toFixed(1)+' d ago'};
 let session=null;
 function load(){try{session=JSON.parse(sessionStorage.getItem('kf.session')||'null')}catch(e){session=null}return session}
 function save(s){session=s;try{if(s)sessionStorage.setItem('kf.session',JSON.stringify(s));else sessionStorage.removeItem('kf.session')}catch(e){}}
 async function api(path,opts){opts=opts||{};const headers={'Content-Type':'application/json'};
  if(session&&session.token)headers.Authorization='Bearer '+session.token;
  const url=(path.charAt(0)==='/'?BASE:'')+path;
  const r=await fetch(url,{method:opts.method||'GET',headers,body:opts.body===undefined?undefined:JSON.stringify(opts.body)});
  let j={};try{j=await r.json()}catch(e){j={error:'non-JSON response'}}
  if(!r.ok){const err=new Error(j.error||('HTTP '+r.status));err.status=r.status;err.body=j;throw err}
  return j}
 const FABRIC='qualizeal';   // single in-house fabric (no tenant selector, D5)
 async function login(subject,password){const j=await api('/login',{method:'POST',body:{tenant:FABRIC,subject,password:password||''}});save(j);renderWho();return j}
 function logout(){save(null);renderWho();nav('/signin')}
 function hasRole(){const roles=(session&&session.roles)||[];for(const r of arguments)if(roles.indexOf(r)>=0)return true;return false}
 let toastTimer=null;
 function toast(msg,kind){let t=$('#kf-toast');if(!t){t=document.createElement('div');t.id='kf-toast';document.body.appendChild(t)}
  t.className='toast '+(kind||'');t.textContent=msg;t.classList.remove('hidden');clearTimeout(toastTimer);
  toastTimer=setTimeout(()=>t.classList.add('hidden'),4200)}
 function gate(err,need){const box=$('#kf-gate');if(!box)return;
  if(!err){box.className='hidden';box.innerHTML='';return}
  const who=session?esc(session.subject)+' <span class="pill">'+esc((session.roles||[]).join(', ')||'no role')+'</span>':'nobody';
  if(err.status===401){box.className='gate';box.innerHTML='<h4>Sign in required</h4>Please <a href="'+BASE+'/signin">sign in to QualiZeal Knowledge Fabric</a>. '+esc(err.message||'')}
  else if(err.status===403){box.className='gate bad';box.innerHTML='<h4>This console needs the <code>'+esc(need||'admin')+'</code> role</h4>You are signed in as '+who+'. The platform answered: <i>'+esc(err.message)+'</i>. <a href="'+BASE+'/signin">Switch user</a>.'}
  else{box.className='gate bad';box.innerHTML='<h4>Request failed</h4>'+esc(err.message||String(err))}}
 function applyRoleNav(){const roles=(session&&session.roles)||[];
  $$('#kf-nav a[data-roles]').forEach(a=>{const need=(a.getAttribute('data-roles')||'').split(',');
   a.style.display=(session&&need.some(r=>roles.indexOf(r)>=0))?'':'none'})}
 function renderWho(){const w=$('#kf-who');if(!w)return;applyRoleNav();
  if(!session){w.innerHTML='<a class="btn primary sm" href="'+BASE+'/signin">Sign in</a>';return}
  w.innerHTML='<b>'+esc(session.subject)+'</b>'+
   (session.roles||[]).map(r=>'<span class="pill '+({admin:'violet',curator:'info',asker:'good',agent:'warn'}[r]||'')+'">'+esc(r)+'</span>').join('')+
   '<button class="btn sm" id="kf-logout">Sign out</button>';
  $('#kf-logout').onclick=()=>{logout();if(window.KF_ON_SESSION)window.KF_ON_SESSION(null)}}
 function initBar(opts){opts=opts||{};load();applyBase();renderWho()}
 function bar(pctv,color){const v=Math.max(0,Math.min(1,Number(pctv)||0));return '<div class="trk"><i style="width:'+(v*100).toFixed(0)+'%;background:'+(color||'var(--accent)')+'"></i></div>'}
 function level(x,good,warn){x=Number(x)||0;return x>=good?'good':x>=warn?'warn':'bad'}
 return {DIR,$,$$,esc,num,pct,money,ms,when,ago,api,login,logout,hasRole,toast,gate,initBar,applyBase,nav,base:()=>BASE,bar,level,get session(){return session}};
})();
