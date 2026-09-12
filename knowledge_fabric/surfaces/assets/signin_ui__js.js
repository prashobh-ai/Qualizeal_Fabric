
const {$,$$,esc,api,toast}=KF;
// Dev-only identity picker (shown only when the server reports KF_DEV_LOGIN);
// the deployed build has no passwordless entry.
const ROLES=[
 {subject:'asker.public',label:'Asker'},
 {subject:'asker.restricted',label:'Restricted asker'},
 {subject:'curator',label:'Curator'},
 {subject:'admin',label:'Admin'},
];
function landing(roles){roles=roles||[];return roles.indexOf('admin')>=0?'/admin':roles.indexOf('curator')>=0?'/curator':'/';}

async function passwordSignIn(email,password){
 try{const s=await KF.authLogin(email,password);toast('Signed in as '+s.subject,'good');KF.nav(landing(s.roles));}
 catch(e){$('#su-err').textContent=(e.status===401?'Invalid credentials.':'Sign-in failed: '+e.message);}
}
async function devSignIn(subject){
 try{const s=await KF.login(subject);toast('Signed in as '+s.subject,'good');KF.nav(landing(s.roles));}
 catch(e){$('#su-err').textContent='Dev sign-in failed: '+e.message;}
}

$('#signin-form').addEventListener('submit',e=>{e.preventDefault();
 const u=$('#su-user').value.trim();if(!u){$('#su-err').textContent='Enter your email.';return}
 passwordSignIn(u,$('#su-pass').value);});

(async()=>{
 KF.initBar({});
 // An SSO redirect lands back here with the token in the fragment.
 const sso=await KF.ssoFromHash();
 if(sso&&sso.token){KF.nav(landing(sso.roles));return}
 if(KF.session){KF.nav('/');return}
 const cfg=await KF.authConfig();
 if(cfg.sso&&cfg.sso.enabled){const b=$('#sso-btn');b.hidden=false;b.textContent='Sign in with '+(cfg.sso.label||'SSO');
  b.onclick=()=>{window.location=KF.base()+'/api/auth/sso/login';};}
 if(cfg.dev_login){$('#demo-picker').hidden=false;
  $('#demo-chips').innerHTML=ROLES.map(r=>'<button type="button" class="btn sm" data-s="'+esc(r.subject)+'">'+esc(r.label)+'</button>').join('');
  $$('#demo-chips button').forEach(b=>b.onclick=()=>devSignIn(b.dataset.s));}
})();
