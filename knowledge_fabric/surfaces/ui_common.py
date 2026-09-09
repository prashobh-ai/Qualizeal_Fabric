"""Shared shell for the Stage-2 web consoles (Section G).

The three consoles — Ask (``/``), Curator (``/curator``) and Admin
(``/admin``) — share one brand shell: the QualiZeal navy palette used by
``surfaces/dashboard.py``, a header with navigation, a sign-in bar backed by
``POST /login`` (tenant + demo subject → bearer token kept in
``sessionStorage``), and a tiny JS runtime (``KF``) that every page uses for
authenticated ``fetch`` calls, HTML escaping, number formatting, toasts and
the 401/403 "role gate" message.

Everything is inline (CSS, JS, SVG): the pages work fully offline with zero
external dependencies. The demo tenant/user picker is generated from
``tenants/demo.py`` at import time so it can never drift from the seed data.

Helpers here are plain string builders; the consoles call ``shell`` once to
produce their ``*_HTML`` constant.
"""
from __future__ import annotations

import json

from ..tenants import demo

__all__ = ["BRAND_CSS", "RUNTIME_JS", "shell", "card", "demo_directory"]

#: navigation shown in every console header (label, path)
_ACTIVE = ' class="active"'
NAV = (("Ask", "/"), ("Curator", "/curator"), ("Admin", "/admin"), ("Dashboard", "/dashboard"))


def demo_directory() -> dict:
    """Tenant → users picker data, derived from the seeded demo directory.

    Shape: ``{"tenants": [{"tenant", "display"}], "users": {tenant: [{"subject", "roles"}]},
    "questions": {tenant: [question...]}}``. Deterministic (seed order).
    """
    display = {t.tenant: t.display for t in demo.DEMO_TENANTS}
    tenants = [{"tenant": t, "display": display.get(t, t)} for t in demo.DEMO_USERS]
    users = {t: [{"subject": s, "roles": list(r)} for s, r, _ in rows]
             for t, rows in demo.DEMO_USERS.items()}
    questions = {t: [q for q, _, _ in bank] for t, bank in demo.QUESTION_BANK.items()}
    return {"tenants": tenants, "users": users, "questions": questions}


# --------------------------------------------------------------------------
# brand stylesheet (dashboard palette + console components)
# --------------------------------------------------------------------------
BRAND_CSS = r"""
:root{
 /* QualiZeal brand tokens (P1.1 — lifted verbatim from the demo styles/main.css).
    These name the palette; the console component tokens below map onto them so
    a rebrand is a token swap, never a search-and-replace across CSS. */
 --qz-blue:#4D7CFF;--qz-blue-deep:#2E5DDB;--qz-blue-soft:rgba(77,124,255,.16);
 --qz-pink:#EE1C5C;--qz-pink-deep:#C81550;--qz-pink-soft:rgba(238,28,92,.16);
 --qz-violet:#7B5BFF;--qz-cyan:#4DD0E8;
 /* navy canvas: only the .galaxy and .health-ring components paint themselves
    onto this dark canvas so the graph looks exactly like the demo; the rest of
    the shell keeps its business-grade dark chrome. */
 --qz-canvas:#0E1A45;--qz-canvas-1:#142158;--qz-canvas-2:#1A2A6E;
 /* existing console shell — unchanged so the current consoles keep their look. */
 --navy:#0E1A45;--navy2:#132257;--ink:#0b1020;--panel:#0f1a3a;--panel2:#152351;--line:#26356b;
 --fg:#e8ecf7;--mut:#9fb0d8;--accent:#4f7cff;--good:#3ecf8e;--warn:#f0b429;--bad:#f06a6a;--info:#4bd6e5;--violet:#c77dff;
}
/* Galaxy panel + health ring: navy canvas only, per §1 rule. */
.galaxy,.health-ring{background:var(--qz-canvas);color:#eef2ff;border-radius:14px;
 background-image:radial-gradient(1200px 480px at 20% 0,rgba(77,124,255,.18),transparent 60%),
                  radial-gradient(900px 380px at 90% 100%,rgba(238,28,92,.14),transparent 55%)}
*{box-sizing:border-box}html{color-scheme:dark}
body{margin:0;background:var(--ink);color:var(--fg);font:14px/1.5 Inter,system-ui,Segoe UI,Roboto,sans-serif}
a{color:var(--accent)}
header{background:linear-gradient(90deg,var(--navy),var(--navy2));padding:12px 22px;display:flex;align-items:center;
gap:16px;border-bottom:1px solid var(--line);flex-wrap:wrap}
header h1{font-size:16px;margin:0;font-weight:700;letter-spacing:.3px;display:flex;align-items:center;gap:10px}
header .sub{color:var(--mut);font-size:12px}
nav{display:flex;gap:2px;margin-left:auto}
nav a{color:var(--mut);text-decoration:none;padding:8px 12px;border-radius:8px;font-weight:500;font-size:13px}
nav a.active{color:#fff;background:rgba(79,124,255,.18);border:1px solid rgba(79,124,255,.45)}
nav a:hover{color:#fff}
.loginbar{display:flex;gap:10px;flex-wrap:wrap;padding:10px 22px;background:var(--navy);border-bottom:1px solid var(--line);align-items:center}
.loginbar label{color:var(--mut);font-size:12px;margin-right:4px}
#kf-who{font-size:12px;color:var(--mut);display:flex;gap:6px;align-items:center;flex-wrap:wrap}
main{padding:20px 22px;max-width:1240px;margin:0 auto}
select,input,textarea{background:var(--ink);color:var(--fg);border:1px solid var(--line);border-radius:8px;padding:7px 9px;font:inherit}
textarea{width:100%;min-height:70px;resize:vertical}
input[type=number]{width:110px}
.btn{background:var(--panel2);color:var(--fg);border:1px solid var(--line);border-radius:8px;padding:7px 12px;font:inherit;
font-weight:600;cursor:pointer;font-size:13px;line-height:1.2}
.btn:hover{border-color:var(--accent)}.btn:disabled{opacity:.5;cursor:not-allowed}
.btn.primary{background:var(--accent);border-color:var(--accent);color:#fff}
.btn.danger{background:rgba(240,106,106,.15);border-color:var(--bad);color:#ffb3b3}
.btn.good{background:rgba(62,207,142,.15);border-color:var(--good);color:#b8f5d8}
.btn.sm{padding:4px 9px;font-size:12px}
.grid{display:grid;gap:14px}.kpis{grid-template-columns:repeat(auto-fit,minmax(150px,1fr))}
.cards{grid-template-columns:repeat(auto-fit,minmax(320px,1fr))}.two{grid-template-columns:1fr 1fr}
@media(max-width:860px){.two{grid-template-columns:1fr}}
.card{background:var(--panel);border:1px solid var(--line);border-radius:14px;padding:16px;min-width:0}
.card h3{margin:0 0 10px;font-size:13px;color:var(--mut);font-weight:600;text-transform:uppercase;letter-spacing:.4px;
display:flex;align-items:center;gap:8px}
.card h3 .right{margin-left:auto;text-transform:none;letter-spacing:0;font-weight:500}
.kpi .v{font-size:24px;font-weight:700}.kpi .l{color:var(--mut);font-size:12px}.kpi .d{font-size:12px;margin-top:3px}
table{width:100%;border-collapse:collapse;font-size:13px}
th,td{text-align:left;padding:7px 8px;border-bottom:1px solid var(--line);vertical-align:top}
th{color:var(--mut);font-weight:600;white-space:nowrap}
.tablewrap{overflow-x:auto}
.pill{display:inline-block;padding:1px 9px;border-radius:20px;font-size:11px;font-weight:600;white-space:nowrap;
background:#182449;color:var(--fg);border:1px solid transparent}
.pill.good{background:rgba(62,207,142,.15);color:#b8f5d8;border-color:rgba(62,207,142,.5)}
.pill.warn{background:rgba(240,180,41,.15);color:#ffe3a0;border-color:rgba(240,180,41,.5)}
.pill.bad{background:rgba(240,106,106,.15);color:#ffb3b3;border-color:rgba(240,106,106,.5)}
.pill.info{background:rgba(75,214,229,.15);color:#c6f4fa;border-color:rgba(75,214,229,.5)}
.pill.violet{background:rgba(199,125,255,.15);color:#e9ccff;border-color:rgba(199,125,255,.5)}
.pill.accent{background:rgba(79,124,255,.18);color:#d3dfff;border-color:rgba(79,124,255,.5)}
.mono{font-family:JetBrains Mono,ui-monospace,SFMono-Regular,Menlo,monospace;font-size:12px}
.muted{color:var(--mut)}.small{font-size:12px}.hidden{display:none !important}
.row{display:flex;gap:10px;align-items:center;flex-wrap:wrap}.col{display:flex;flex-direction:column;gap:8px}
.trk{flex:1;height:10px;background:#182449;border-radius:6px;overflow:hidden;min-width:60px}.trk>i{display:block;height:100%}
.empty{color:var(--mut);font-size:12px;padding:8px 0}
.gate{background:rgba(240,180,41,.12);border:1px solid var(--warn);border-radius:12px;padding:14px 16px;margin-bottom:16px}
.gate.bad{background:rgba(240,106,106,.12);border-color:var(--bad)}
.gate h4{margin:0 0 4px;font-size:14px}
.toast{position:fixed;right:18px;bottom:18px;background:var(--panel2);border:1px solid var(--line);border-left:4px solid var(--accent);
border-radius:10px;padding:10px 14px;font-size:13px;max-width:420px;box-shadow:0 8px 30px rgba(0,0,0,.4);z-index:50}
.toast.good{border-left-color:var(--good)}.toast.bad{border-left-color:var(--bad)}.toast.warn{border-left-color:var(--warn)}
.switch{position:relative;display:inline-block;width:38px;height:22px;vertical-align:middle}
.switch input{opacity:0;width:0;height:0}
.switch i{position:absolute;inset:0;background:#182449;border:1px solid var(--line);border-radius:22px;cursor:pointer;transition:.15s}
.switch i:before{content:"";position:absolute;width:16px;height:16px;left:2px;top:2px;background:var(--mut);border-radius:50%;transition:.15s}
.switch input:checked+i{background:rgba(62,207,142,.35);border-color:var(--good)}
.switch input:checked+i:before{transform:translateX(16px);background:var(--good)}
details>summary{cursor:pointer;color:var(--mut);font-weight:600;font-size:13px;list-style:none;display:flex;align-items:center;gap:6px}
details>summary::-webkit-details-marker{display:none}
details>summary:before{content:"▸";font-size:11px;transition:.15s}details[open]>summary:before{transform:rotate(90deg)}
.section-title{margin:26px 0 12px;font-size:15px;font-weight:700;display:flex;align-items:center;gap:10px}
.section-title .pill{font-weight:600}
.dot{display:inline-block;width:8px;height:8px;border-radius:50%;background:var(--mut);vertical-align:middle}
.dot.live{background:var(--good);box-shadow:0 0 0 0 rgba(62,207,142,.7);animation:pulse 1.4s infinite}
@keyframes pulse{0%{box-shadow:0 0 0 0 rgba(62,207,142,.6)}70%{box-shadow:0 0 0 8px rgba(62,207,142,0)}100%{box-shadow:0 0 0 0 rgba(62,207,142,0)}}
code{background:#182449;border-radius:4px;padding:1px 5px;font-size:12px}
pre{background:#0a0f26;border:1px solid var(--line);border-radius:10px;padding:12px;overflow:auto;font-size:12px;line-height:1.45;margin:0}
.drawer{position:fixed;top:0;right:0;height:100%;width:min(560px,100%);background:var(--panel);border-left:1px solid var(--line);
box-shadow:-10px 0 40px rgba(0,0,0,.45);padding:18px 20px;overflow:auto;z-index:40}
.drawer h3{margin-top:0}
"""

# --------------------------------------------------------------------------
# JS runtime shared by every console
# --------------------------------------------------------------------------
RUNTIME_JS = r"""
const KF=(()=>{
 const DIR=window.KF_DIRECTORY||{tenants:[],users:{},questions:{}};
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
  const r=await fetch(path,{method:opts.method||'GET',headers,body:opts.body===undefined?undefined:JSON.stringify(opts.body)});
  let j={};try{j=await r.json()}catch(e){j={error:'non-JSON response'}}
  if(!r.ok){const err=new Error(j.error||('HTTP '+r.status));err.status=r.status;err.body=j;throw err}
  return j}
 async function login(tenant,subject){const j=await api('/login',{method:'POST',body:{tenant,subject}});save(j);renderWho();return j}
 function logout(){save(null);renderWho()}
 function hasRole(){const roles=(session&&session.roles)||[];for(const r of arguments)if(roles.indexOf(r)>=0)return true;return false}
 let toastTimer=null;
 function toast(msg,kind){let t=$('#kf-toast');if(!t){t=document.createElement('div');t.id='kf-toast';document.body.appendChild(t)}
  t.className='toast '+(kind||'');t.textContent=msg;t.classList.remove('hidden');clearTimeout(toastTimer);
  toastTimer=setTimeout(()=>t.classList.add('hidden'),4200)}
 function gate(err,need){const box=$('#kf-gate');if(!box)return;
  if(!err){box.className='hidden';box.innerHTML='';return}
  const who=session?esc(session.subject)+' <span class="pill">'+esc((session.roles||[]).join(', ')||'no role')+'</span>':'nobody';
  if(err.status===401){box.className='gate';box.innerHTML='<h4>Sign in required</h4>Pick a tenant and a demo user above and press <b>Sign in</b>. '+esc(err.message||'')}
  else if(err.status===403){box.className='gate bad';box.innerHTML='<h4>This console needs the <code>'+esc(need||'admin')+'</code> role</h4>You are signed in as '+who+'. The platform answered: <i>'+esc(err.message)+'</i>. Switch to a user with the right role above.'}
  else{box.className='gate bad';box.innerHTML='<h4>Request failed</h4>'+esc(err.message||String(err))}}
 function renderWho(){const w=$('#kf-who');if(!w)return;
  if(!session){w.innerHTML='<span class="pill">signed out</span>';return}
  w.innerHTML='<span class="pill accent">'+esc(session.tenant)+'</span><b>'+esc(session.subject)+'</b>'+
   (session.roles||[]).map(r=>'<span class="pill '+({admin:'violet',curator:'info',asker:'good',agent:'warn'}[r]||'')+'">'+esc(r)+'</span>').join('')+
   '<span class="muted">scopes: '+esc((session.scopes||[]).join(', '))+'</span><button class="btn sm" id="kf-logout">Sign out</button>';
  $('#kf-logout').onclick=()=>{logout();if(window.KF_ON_SESSION)window.KF_ON_SESSION(null)}}
 function fillSubjects(){const t=$('#kf-tenant').value,s=$('#kf-subject');s.innerHTML='';
  (DIR.users[t]||[]).forEach(u=>{const o=document.createElement('option');o.value=u.subject;o.text=u.subject+' ('+u.roles.join(',')+')';s.add(o)})}
 function initBar(opts){opts=opts||{};const ts=$('#kf-tenant');if(!ts)return;
  DIR.tenants.forEach(t=>{const o=document.createElement('option');o.value=t.tenant;o.text=t.display;ts.add(o)});
  load();if(session&&DIR.users[session.tenant]){ts.value=session.tenant}
  fillSubjects();if(session)$('#kf-subject').value=session.subject;
  if(opts.preferRole&&!session){const list=DIR.users[ts.value]||[];const pick=list.find(u=>u.roles.indexOf(opts.preferRole)>=0);if(pick)$('#kf-subject').value=pick.subject}
  ts.addEventListener('change',fillSubjects);
  $('#kf-login-btn').onclick=async()=>{try{const s=await login(ts.value,$('#kf-subject').value);toast('Signed in as '+s.subject,'good');if(window.KF_ON_SESSION)window.KF_ON_SESSION(s)}catch(e){toast('Login failed: '+e.message,'bad')}};
  renderWho()}
 function bar(pctv,color){const v=Math.max(0,Math.min(1,Number(pctv)||0));return '<div class="trk"><i style="width:'+(v*100).toFixed(0)+'%;background:'+(color||'var(--accent)')+'"></i></div>'}
 function level(x,good,warn){x=Number(x)||0;return x>=good?'good':x>=warn?'warn':'bad'}
 return {DIR,$,$$,esc,num,pct,money,ms,when,ago,api,login,logout,hasRole,toast,gate,initBar,bar,level,get session(){return session}};
})();
"""

# inline SVG logo mark (navy shield with a check) — brand-neutral, no external asset
_LOGO_SVG = ('<svg width="22" height="22" viewBox="0 0 24 24" aria-hidden="true">'
             '<path d="M12 2l8 3v6c0 5-3.4 9.4-8 11-4.6-1.6-8-6-8-11V5l8-3z" fill="#4f7cff"/>'
             '<path d="M8.5 12.2l2.4 2.4 4.8-5" fill="none" stroke="#fff" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/>'
             '</svg>')

_SHELL = """<!doctype html>
<html lang="en" data-theme="dark"><head>
<meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>__TITLE__</title>
<style>__CSS__
__EXTRA_CSS__</style></head><body>
<header>
  <h1>__LOGO__ QualiZeal Knowledge Fabric</h1>
  <span class="sub">__SUBTITLE__</span>
  <nav id="kf-nav">__NAV__</nav>
</header>
<div class="loginbar" id="kf-login">
  <span><label for="kf-tenant">Tenant</label><select id="kf-tenant"></select></span>
  <span><label for="kf-subject">User</label><select id="kf-subject"></select></span>
  <button class="btn primary" id="kf-login-btn">Sign in</button>
  <span id="kf-who"></span>
</div>
<main>
<div id="kf-gate" class="hidden"></div>
__BODY__
</main>
<script>window.KF_DIRECTORY=__DIRECTORY__;</script>
<script>__RUNTIME__</script>
<script>__SCRIPT__</script>
</body></html>"""


def card(title: str, body: str, id_: str = "", extra: str = "", right: str = "") -> str:
    """One brand panel: ``<div class="card"><h3>title</h3>body</div>``."""
    attr = f' id="{id_}"' if id_ else ""
    right_html = f'<span class="right">{right}</span>' if right else ""
    return f'<div class="card"{attr}{(" " + extra) if extra else ""}><h3>{title}{right_html}</h3>{body}</div>'


def shell(title: str, subtitle: str, body: str, script: str, active: str, extra_css: str = "") -> str:
    """Assemble a complete console page from the shared shell.

    ``active`` names the highlighted navigation entry (``"Ask"``, ``"Curator"``,
    ``"Admin"``). ``script`` runs after the ``KF`` runtime is defined.
    Placeholders are substituted with ``str.replace`` so CSS/JS braces are
    never interpreted.
    """
    nav = "".join(f'<a href="{path}"{_ACTIVE if label == active else ""}>{label}</a>'
                  for label, path in NAV)
    directory = json.dumps(demo_directory(), sort_keys=True).replace("</", "<\\/")
    return (_SHELL.replace("__TITLE__", title).replace("__CSS__", BRAND_CSS)
            .replace("__EXTRA_CSS__", extra_css).replace("__LOGO__", _LOGO_SVG)
            .replace("__SUBTITLE__", subtitle).replace("__NAV__", nav)
            .replace("__DIRECTORY__", directory).replace("__RUNTIME__", RUNTIME_JS)
            .replace("__BODY__", body).replace("__SCRIPT__", script))
