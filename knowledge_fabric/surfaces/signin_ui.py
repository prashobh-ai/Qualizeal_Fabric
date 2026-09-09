"""Sign-in page (L1.4) — served at ``/signin``.

A white product sign-in: the lockup, a user id + password form against the
local IdP, and a disabled SSO placeholder ("available with the corporate
directory"). The token is kept in memory + ``sessionStorage``; every console
redirects here when it has no session.

The showcase build adds a labelled demo identity picker (Asker · Restricted
asker · Curator · Admin) that signs in through the same ``/login`` endpoint —
in the live product the corporate directory replaces it (config only).
"""
from __future__ import annotations

import json

from .ui_common import BRAND_CSS, RUNTIME_JS, _FAVICON, _LOCKUP, _VERSION, demo_directory

__all__ = ["SIGNIN_HTML"]

_CSS = r"""
.signin-wrap{min-height:100vh;display:flex;flex-direction:column;align-items:center;justify-content:center;
 background:linear-gradient(180deg,var(--qz-blue-tint),var(--qz-surface) 260px)}
.signin-card{background:var(--qz-surface);border:1px solid var(--qz-line);border-radius:14px;
 box-shadow:var(--qz-shadow);padding:28px 30px;width:min(400px,92vw)}
.signin-card .lockup{height:22px;width:auto;display:block;margin-bottom:18px}
.signin-card h1{font-size:18px;margin:0 0 4px;color:var(--qz-ink);font-weight:600}
.signin-card p.sub{margin:0 0 18px;color:var(--qz-muted);font-size:13px}
.signin-card label{display:block;font-size:12px;color:var(--qz-muted);margin:12px 0 4px;font-weight:600}
.signin-card input{width:100%}
.signin-card .full{width:100%;margin-top:18px}
.sso{width:100%;margin-top:10px;background:var(--qz-panel);color:var(--qz-soft);border:1px dashed var(--qz-line);
 border-radius:8px;padding:9px;font-size:13px;text-align:center;cursor:not-allowed}
.demo-picker{margin-top:20px;border-top:1px solid var(--qz-line-2);padding-top:16px}
.demo-picker .lbl{font-size:11px;color:var(--qz-soft);text-transform:uppercase;letter-spacing:.04em;margin-bottom:8px}
.demo-picker .chips{display:flex;gap:8px;flex-wrap:wrap}
.demo-picker button{flex:1 1 auto}
.signin-foot{margin-top:22px;font-size:12px;color:var(--qz-soft);text-align:center}
.err{color:var(--qz-coral);font-size:13px;margin-top:12px;min-height:16px}
"""

_BODY = """
<div class="signin-wrap">
  <form class="signin-card" id="signin-form">
    <img class="lockup" src="__LOCKUP__" alt="QualiZeal Knowledge Fabric">
    <h1>Sign in to QualiZeal Knowledge Fabric</h1>
    <p class="sub">Internal knowledge for QualiZeal.</p>
    <label for="su-user">User ID</label>
    <input id="su-user" name="user" autocomplete="username" placeholder="asker.public">
    <label for="su-pass">Password</label>
    <input id="su-pass" name="pass" type="password" autocomplete="current-password" placeholder="••••••••">
    <button class="btn primary full" type="submit">Sign in</button>
    <div class="sso" title="Single sign-on becomes available with the corporate directory">
      Single sign-on — available with the corporate directory
    </div>
    <div class="err" id="su-err"></div>
    <div class="demo-picker" id="demo-picker">
      <div class="lbl">Showcase sign-in</div>
      <div class="chips" id="demo-chips"></div>
    </div>
    <div class="signin-foot">&copy; QualiZeal. All rights reserved. &middot; __VERSION__</div>
  </form>
</div>
"""

_JS = r"""
const {$,$$,esc,api,toast}=KF;
const ROLES=[
 {subject:'asker.public',label:'Asker'},
 {subject:'asker.restricted',label:'Restricted asker'},
 {subject:'curator',label:'Curator'},
 {subject:'admin',label:'Admin'},
];
async function signIn(subject){
 try{const s=await KF.login(subject);toast('Signed in as '+s.subject,'good');
  KF.nav((s.roles&&s.roles.indexOf('admin')>=0)?'/admin':'/');}
 catch(e){$('#su-err').textContent='Sign-in failed: '+e.message;}
}
// Demo identity picker (showcase sign-in) — the corporate directory replaces
// this in the live product.
$('#demo-chips').innerHTML=ROLES.map(r=>'<button type="button" class="btn sm" data-s="'+esc(r.subject)+'">'+esc(r.label)+'</button>').join('');
$$('#demo-chips button').forEach(b=>b.onclick=()=>signIn(b.dataset.s));
$('#signin-form').addEventListener('submit',e=>{e.preventDefault();
 const u=$('#su-user').value.trim();if(!u){$('#su-err').textContent='Enter a user id.';return}
 // Local IdP accepts the demo users; the password field is validated by the
 // real IdP once the corporate directory is connected.
 signIn(u);});
KF.initBar({});
if(KF.session){KF.nav('/');}
"""

_SHELL = """<!doctype html>
<html lang="en"><head>
<meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>QualiZeal Knowledge Fabric — Sign in</title>
<link rel="icon" href="__FAVICON__">
<style>__CSS__
__EXTRA_CSS__</style></head><body>
__BODY__
<script>window.KF_DIRECTORY=__DIRECTORY__;</script>
<script>__RUNTIME__</script>
<script>__SCRIPT__</script>
</body></html>"""

SIGNIN_HTML = (_SHELL
               .replace("__CSS__", BRAND_CSS)
               .replace("__EXTRA_CSS__", _CSS)
               .replace("__BODY__", _BODY.replace("__LOCKUP__", _LOCKUP).replace("__VERSION__", _VERSION))
               .replace("__FAVICON__", _FAVICON)
               .replace("__DIRECTORY__", json.dumps(demo_directory(), sort_keys=True).replace("</", "<\\/"))
               .replace("__RUNTIME__", RUNTIME_JS)
               .replace("__SCRIPT__", _JS))
