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
# Page switcher (L1.2): Workspace · Curator · Admin — three surfaces, shown by
# role client-side. Each entry: (label, path, roles-that-see-it or None=all).
NAV = (("Workspace", "/", None),
       ("Curator", "/curator", ("curator", "admin")),
       ("Admin", "/admin", ("admin",)))


def demo_directory() -> dict:
    """Tenant → users picker data, derived from the seeded demo directory.

    Shape: ``{"tenants": [{"tenant", "display"}], "users": {tenant: [{"subject", "roles"}]},
    "questions": {tenant: [question...]}}``. Deterministic (seed order).
    """
    display = {t.tenant: t.display for t in demo.DEMO_TENANTS}
    tenants = [{"tenant": t, "display": display.get(t, t)} for t in demo.DEMO_USERS]
    users = {t: [{"subject": s, "roles": list(r)} for s, r, _ in rows]
             for t, rows in demo.DEMO_USERS.items()}
    # Suggested questions now come from the live question bank over the real
    # corpus (P1.6 / L0.3) via GET /api/suggestions; the static directory
    # carries no synthetic question list (L0.2).
    questions = {t: [] for t in demo.DEMO_USERS}
    return {"tenants": tenants, "users": users, "questions": questions}


# --------------------------------------------------------------------------
# brand stylesheet (dashboard palette + console components)
# --------------------------------------------------------------------------
BRAND_CSS = r"""
:root{
 /* QualiZeal deck-template tokens (L1.1 — inlined from static/assets/brand/tokens.css
    so the shell stays same-origin and self-contained). The shell component
    variables below map onto these, so a rebrand is a token swap. */
 --qz-ink:#0D1523;--qz-body:#2B3B4A;--qz-muted:#5A6B7C;--qz-soft:#7C8DA1;
 --qz-line:#CFE0F0;--qz-line-2:#DCE4EC;--qz-panel:#F4F8FC;--qz-surface:#FFFFFF;
 --qz-blue:#0096FF;--qz-blue-deep:#00619F;--qz-blue-tint:#EAF4FF;
 --qz-coral:#F53E5A;--qz-coral-tint:#FDEEF1;--qz-green:#0CA678;--qz-purple:#7048E8;--qz-amber:#E8A23A;
 --qz-radius-card:12px;--qz-radius-chip:8px;--qz-radius-pill:999px;
 --qz-shadow:0 1px 2px rgba(13,21,35,.06),0 8px 24px rgba(13,21,35,.06);
 --qz-font:"Inter",system-ui,-apple-system,"Segoe UI",Roboto,sans-serif;
 /* the navy canvas survives ONLY inside .galaxy / .health-ring (L1.1). */
 --qz-canvas:#0E1A45;
 /* shell component tokens mapped onto the white brand palette. */
 --surface:var(--qz-surface);--panel:var(--qz-panel);--panel2:var(--qz-blue-tint);
 --line:var(--qz-line);--fg:var(--qz-body);--ink:var(--qz-ink);--mut:var(--qz-muted);--soft:var(--qz-soft);
 --accent:var(--qz-blue);--good:var(--qz-green);--warn:var(--qz-amber);--bad:var(--qz-coral);
 --info:var(--qz-blue);--violet:var(--qz-purple);
}
/* Galaxy panel + health ring: the navy canvas, and only here. */
.galaxy,.health-ring{background:var(--qz-canvas);color:#eef2ff;border-radius:14px;
 background-image:radial-gradient(1200px 480px at 20% 0,rgba(0,150,255,.18),transparent 60%),
                  radial-gradient(900px 380px at 90% 100%,rgba(245,62,90,.14),transparent 55%)}
*{box-sizing:border-box}html{color-scheme:light}
body{margin:0;background:var(--surface);color:var(--fg);
 font:14px/1.5 var(--qz-font);font-variant-numeric:tabular-nums;
 display:flex;flex-direction:column;min-height:100vh}
a{color:var(--accent);text-decoration:none}a:hover{text-decoration:underline}
/* top bar (L1.2) — 56px, lockup left, role page switcher, user menu right. */
header.topbar{height:56px;display:flex;align-items:center;gap:16px;padding:0 24px;
 background:var(--surface);border-bottom:1px solid var(--line);position:sticky;top:0;z-index:10}
header.topbar .lockup{height:18px;width:auto;display:block}
header.topbar .lockup-link{display:inline-flex;align-items:center}
nav{display:flex;gap:2px}
nav a{color:var(--qz-muted);padding:8px 12px;border-radius:8px;font-weight:600;font-size:13px}
nav a.active{color:var(--qz-blue-deep);background:var(--qz-blue-tint)}
nav a:hover{color:var(--qz-ink);text-decoration:none}
.topbar .spacer{flex:1}
#kf-who{font-size:12px;color:var(--mut);display:flex;gap:8px;align-items:center;flex-wrap:wrap}
main{padding:22px 24px;max-width:1240px;margin:0 auto;width:100%;flex:1}
select,input,textarea{background:var(--surface);color:var(--fg);border:1px solid var(--line);
 border-radius:8px;padding:8px 10px;font:inherit}
select:focus,input:focus,textarea:focus{outline:2px solid var(--qz-blue-tint);border-color:var(--qz-blue)}
textarea{width:100%;min-height:70px;resize:vertical}
input[type=number]{width:110px}
.btn{background:var(--surface);color:var(--qz-ink);border:1px solid var(--line);border-radius:8px;padding:8px 13px;
 font:inherit;font-weight:600;cursor:pointer;font-size:13px;line-height:1.2}
.btn:hover{border-color:var(--accent)}.btn:disabled{opacity:.5;cursor:not-allowed}
.btn.primary{background:var(--qz-blue);border-color:var(--qz-blue);color:#fff}
.btn.primary:hover{background:var(--qz-blue-deep);border-color:var(--qz-blue-deep)}
.btn.danger{background:var(--qz-coral-tint);border-color:var(--qz-coral);color:var(--qz-coral)}
.btn.good{background:#E6F7F1;border-color:var(--qz-green);color:var(--qz-green)}
.btn.sm{padding:5px 10px;font-size:12px}
.grid{display:grid;gap:14px}.kpis{grid-template-columns:repeat(auto-fit,minmax(150px,1fr))}
.cards{grid-template-columns:repeat(auto-fit,minmax(320px,1fr))}.two{grid-template-columns:1fr 1fr}
@media(max-width:860px){.two{grid-template-columns:1fr}}
.card{background:var(--qz-panel);border:1px solid var(--line);border-radius:var(--qz-radius-card);padding:16px;min-width:0;
 box-shadow:var(--qz-shadow)}
.card h3{margin:0 0 10px;font-size:12px;color:var(--qz-muted);font-weight:600;text-transform:uppercase;letter-spacing:.04em;
 display:flex;align-items:center;gap:8px}
.card h3 .right{margin-left:auto;text-transform:none;letter-spacing:0;font-weight:500}
.kpi .v{font-size:28px;font-weight:600;color:var(--qz-ink)}.kpi .l{color:var(--qz-muted);font-size:12px}.kpi .d{font-size:12px;margin-top:3px}
table{width:100%;border-collapse:collapse;font-size:13px}
th,td{text-align:left;padding:8px;border-bottom:1px solid var(--qz-line-2);vertical-align:top}
th{color:var(--qz-muted);font-weight:600;white-space:nowrap;font-size:12px;text-transform:uppercase;letter-spacing:.04em}
.tablewrap{overflow-x:auto}
.pill{display:inline-block;padding:2px 9px;border-radius:var(--qz-radius-pill);font-size:11px;font-weight:600;white-space:nowrap;
 background:var(--qz-blue-tint);color:var(--qz-blue-deep);border:1px solid transparent}
.pill.good{background:#E6F7F1;color:var(--qz-green);border-color:rgba(12,166,120,.25)}
.pill.warn{background:#FCF2E1;color:#9A6516;border-color:rgba(232,162,58,.3)}
.pill.bad{background:var(--qz-coral-tint);color:var(--qz-coral);border-color:rgba(245,62,90,.25)}
.pill.info{background:var(--qz-blue-tint);color:var(--qz-blue-deep);border-color:rgba(0,150,255,.25)}
.pill.violet{background:#EFEBFD;color:var(--qz-purple);border-color:rgba(112,72,232,.25)}
.pill.accent{background:var(--qz-blue-tint);color:var(--qz-blue-deep);border-color:rgba(0,150,255,.35)}
/* the four reader levels, in their fixed brand colours (never shown as numbers). */
.pill.lv-look{background:#E6F7F1;color:var(--qz-green);border-color:rgba(12,166,120,.3)}
.pill.lv-quote{background:var(--qz-blue-tint);color:var(--qz-blue-deep);border-color:rgba(0,150,255,.3)}
.pill.lv-sum{background:#EFEBFD;color:var(--qz-purple);border-color:rgba(112,72,232,.3)}
.pill.lv-reason{background:var(--qz-coral-tint);color:var(--qz-coral);border-color:rgba(245,62,90,.3)}
.mono{font-family:ui-monospace,SFMono-Regular,Menlo,monospace;font-size:12px}
.muted{color:var(--mut)}.small{font-size:12px}.hidden{display:none !important}
.row{display:flex;gap:10px;align-items:center;flex-wrap:wrap}.col{display:flex;flex-direction:column;gap:8px}
.trk{flex:1;height:10px;background:var(--qz-blue-tint);border-radius:6px;overflow:hidden;min-width:60px}.trk>i{display:block;height:100%}
.empty{color:var(--mut);font-size:12px;padding:8px 0}
.gate{background:#FCF2E1;border:1px solid var(--qz-amber);border-radius:12px;padding:14px 16px;margin-bottom:16px;color:var(--qz-ink)}
.gate.bad{background:var(--qz-coral-tint);border-color:var(--qz-coral)}
.gate h4{margin:0 0 4px;font-size:14px}
.toast{position:fixed;right:18px;bottom:18px;background:var(--surface);border:1px solid var(--line);border-left:4px solid var(--accent);
 border-radius:10px;padding:10px 14px;font-size:13px;max-width:420px;box-shadow:var(--qz-shadow);z-index:60;color:var(--qz-ink)}
.toast.good{border-left-color:var(--good)}.toast.bad{border-left-color:var(--bad)}.toast.warn{border-left-color:var(--warn)}
.switch{position:relative;display:inline-block;width:38px;height:22px;vertical-align:middle}
.switch input{opacity:0;width:0;height:0}
.switch i{position:absolute;inset:0;background:var(--qz-line);border:1px solid var(--line);border-radius:22px;cursor:pointer;transition:.15s}
.switch i:before{content:"";position:absolute;width:16px;height:16px;left:2px;top:2px;background:#fff;border-radius:50%;transition:.15s;box-shadow:0 1px 2px rgba(13,21,35,.2)}
.switch input:checked+i{background:var(--qz-green);border-color:var(--qz-green)}
.switch input:checked+i:before{transform:translateX(16px)}
details>summary{cursor:pointer;color:var(--mut);font-weight:600;font-size:13px;list-style:none;display:flex;align-items:center;gap:6px}
details>summary::-webkit-details-marker{display:none}
details>summary:before{content:"▸";font-size:11px;transition:.15s}details[open]>summary:before{transform:rotate(90deg)}
.section-title{margin:26px 0 12px;font-size:15px;font-weight:600;color:var(--qz-ink);display:flex;align-items:center;gap:10px}
.section-title .pill{font-weight:600}
.dot{display:inline-block;width:8px;height:8px;border-radius:50%;background:var(--mut);vertical-align:middle}
.dot.live{background:var(--good);box-shadow:0 0 0 0 rgba(12,166,120,.6);animation:pulse 1.4s infinite}
@keyframes pulse{0%{box-shadow:0 0 0 0 rgba(12,166,120,.5)}70%{box-shadow:0 0 0 8px rgba(12,166,120,0)}100%{box-shadow:0 0 0 0 rgba(12,166,120,0)}}
code{background:var(--qz-blue-tint);border-radius:4px;padding:1px 5px;font-size:12px;color:var(--qz-blue-deep)}
pre{background:var(--qz-panel);border:1px solid var(--line);border-radius:10px;padding:12px;overflow:auto;font-size:12px;line-height:1.45;margin:0;color:var(--qz-ink)}
.drawer{position:fixed;top:0;right:0;height:100%;width:min(560px,100%);background:var(--surface);border-left:1px solid var(--line);
 box-shadow:-10px 0 40px rgba(13,21,35,.18);padding:18px 20px;overflow:auto;z-index:40}
.drawer h3{margin-top:0}
/* watermark + footer (L1.3) */
.qz-watermark{position:fixed;right:24px;bottom:56px;width:220px;height:220px;pointer-events:none;z-index:0;
 background:url("/static/assets/brand/logo/qualizeal-mark.png") no-repeat center/contain;opacity:.04}
.qz-footer{height:40px;display:flex;align-items:center;justify-content:space-between;padding:0 24px;
 font-size:12px;color:var(--qz-soft);border-top:1px solid var(--line);background:var(--surface)}
.qz-footer .mid{text-align:center}
"""

# --------------------------------------------------------------------------
# JS runtime shared by every console
# --------------------------------------------------------------------------
RUNTIME_JS = r"""
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
 async function login(subject){const j=await api('/login',{method:'POST',body:{tenant:FABRIC,subject}});save(j);renderWho();return j}
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
"""

#: served brand assets (same-origin, L1.1/L1.2)
_LOCKUP = "/static/assets/brand/logo/qualizeal-lockup.png"
_FAVICON = "/static/assets/brand/logo/favicon-32.png"

_SHELL = """<!doctype html>
<html lang="en"><head>
<meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>__TITLE__</title>
<link rel="icon" href="__FAVICON__">
<style>__CSS__
__EXTRA_CSS__</style></head><body>
<header class="topbar">
  <a class="lockup-link" href="/" data-path="/" title="QualiZeal Knowledge Fabric"><img class="lockup" src="__LOCKUP__" alt="QualiZeal Knowledge Fabric"></a>
  <nav id="kf-nav">__NAV__</nav>
  <span class="spacer"></span>
  <span id="kf-who"></span>
</header>
<main>
<div id="kf-gate" class="hidden"></div>
__BODY__
</main>
<div class="qz-watermark" aria-hidden="true"></div>
<footer class="qz-footer">
  <span>&copy; QualiZeal. All rights reserved.</span>
  <span class="mid">QualiZeal Knowledge Fabric &middot; Internal</span>
  <span>__VERSION__</span>
</footer>
<script>window.KF_DIRECTORY=__DIRECTORY__;</script>
<script>__RUNTIME__</script>
<script>__SCRIPT__</script>
</body></html>"""

_VERSION = "v0.3"


def card(title: str, body: str, id_: str = "", extra: str = "", right: str = "") -> str:
    """One brand panel: ``<div class="card"><h3>title</h3>body</div>``."""
    attr = f' id="{id_}"' if id_ else ""
    right_html = f'<span class="right">{right}</span>' if right else ""
    return f'<div class="card"{attr}{(" " + extra) if extra else ""}><h3>{title}{right_html}</h3>{body}</div>'


def shell(title: str, subtitle: str, body: str, script: str, active: str, extra_css: str = "") -> str:
    """Assemble a complete console page from the shared shell.

    ``active`` names the highlighted navigation entry (``"Workspace"``,
    ``"Curator"``, ``"Admin"``). ``subtitle`` is accepted for call-site
    compatibility but is NOT rendered — the product chrome has no narrative
    header (L1.2 / D6). The browser title is
    ``QualiZeal Knowledge Fabric — <page>`` (L1.2).
    """
    parts = []
    for label, path, roles in NAV:
        active_attr = _ACTIVE if label == active else ""
        roles_attr = "" if roles is None else ' data-roles="{}"'.format(",".join(roles))
        parts.append('<a href="{0}" data-path="{0}"{1}{2}>{3}</a>'.format(path, active_attr, roles_attr, label))
    nav = "".join(parts)
    directory = json.dumps(demo_directory(), sort_keys=True).replace("</", "<\\/")
    browser_title = f"QualiZeal Knowledge Fabric — {active}"
    return (_SHELL.replace("__TITLE__", browser_title).replace("__CSS__", BRAND_CSS)
            .replace("__EXTRA_CSS__", extra_css)
            .replace("__LOCKUP__", _LOCKUP).replace("__FAVICON__", _FAVICON)
            .replace("__VERSION__", _VERSION).replace("__NAV__", nav)
            .replace("__DIRECTORY__", directory).replace("__RUNTIME__", RUNTIME_JS)
            .replace("__BODY__", body).replace("__SCRIPT__", script))
