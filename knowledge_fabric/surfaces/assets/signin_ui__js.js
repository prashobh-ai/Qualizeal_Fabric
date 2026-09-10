
const {$,$$,esc,api,toast}=KF;
const ROLES=[
 {subject:'asker.public',label:'Asker'},
 {subject:'asker.restricted',label:'Restricted asker'},
 {subject:'curator',label:'Curator'},
 {subject:'admin',label:'Admin'},
];
async function signIn(subject,password){
 try{const s=await KF.login(subject,password);toast('Signed in as '+s.subject,'good');
  const roles=s.roles||[];
  KF.nav(roles.indexOf('admin')>=0?'/admin':roles.indexOf('curator')>=0?'/curator':'/');}
 catch(e){$('#su-err').textContent='Sign-in failed: '+e.message;}
}
// Demo identity picker (showcase sign-in) — the corporate directory replaces
// this in the live product.
$('#demo-chips').innerHTML=ROLES.map(r=>'<button type="button" class="btn sm" data-s="'+esc(r.subject)+'">'+esc(r.label)+'</button>').join('');
$$('#demo-chips button').forEach(b=>b.onclick=()=>signIn(b.dataset.s));
$('#signin-form').addEventListener('submit',e=>{e.preventDefault();
 const u=$('#su-user').value.trim();if(!u){$('#su-err').textContent='Enter your user id.';return}
 // Anyone with a @qualizeal.com address signs in by default (single sign-on).
 // Elevated accounts also supply their password; the corporate directory
 // replaces this local check once connected.
 signIn(u,$('#su-pass').value);});
KF.initBar({});
if(KF.session){KF.nav('/');}
