
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
