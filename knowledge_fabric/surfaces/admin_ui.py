"""Admin console (Section G) — served at ``/admin``.

The admin owns the *plumbing*: connectors and their permissions, continuous
refresh, bulk data operations, budgets, users, audit and cloud readiness.
Every panel is wired to an admin endpoint of ``surfaces/http_api.py``:

* connector cards        ``GET/POST /admin/connectors`` (enable/disable toggle,
                         allow-list, refresh interval, "Sync now" → ``POST /admin/sync``,
                         health badge with freshness / last status / errors / SLA breach);
* pipeline runs          ``GET /admin/runs`` polled every 2 s while a run is active
                         (or right after Sync/Upload), the 7 pipeline stages
                         (detect → convert → chunk → extract → graph → embed → health)
                         plus the sync/tombstone/ingest bookkeeping steps;
* run-due               ``POST /admin/refresh/run-due``;
* bulk upload            ``POST /admin/upload`` (batch builder or raw JSON);
* bulk delete            ``POST /admin/bulk-delete`` (ids / source / uri prefix, with confirm);
* budgets                ``POST /admin/budget``;
* users & roles          ``GET /admin/users``;
* source authority       ``GET/POST /admin/authority``;
* audit tail             ``GET /admin/audit``;
* AWS readiness          ``GET /admin/doctor?target=aws|local``.

401/403 are rendered as a role message by the shared runtime. Zero external
dependencies: inline CSS/JS/SVG only.
"""
from __future__ import annotations

from .ui_common import card, shell

__all__ = ["ADMIN_HTML"]

_CSS = r"""
.conn{display:grid;grid-template-columns:repeat(auto-fit,minmax(300px,1fr));gap:14px}
.conn-card{background:var(--panel);border:1px solid var(--line);border-radius:14px;padding:14px}
.conn-card.breach{border-color:var(--bad);box-shadow:0 0 0 1px rgba(240,106,106,.35) inset}
.conn-card.off{opacity:.75}
.conn-card h4{margin:0;font-size:15px;display:flex;align-items:center;gap:8px}
.conn-card .hl{display:grid;grid-template-columns:1fr 1fr;gap:4px 10px;font-size:12px;margin:10px 0}
.conn-card .hl span:nth-child(odd){color:var(--mut)}
.conn-card label{font-size:12px;color:var(--mut)}
.conn-card input[type=text]{width:100%}
.stages{display:flex;gap:4px;flex-wrap:wrap}
.stage{padding:2px 8px;border-radius:6px;font-size:11px;font-weight:600;background:#182449;color:var(--mut);border:1px solid var(--line);white-space:nowrap}
.stage.ok{color:#b8f5d8;border-color:rgba(62,207,142,.5)}.stage.error{color:#ffb3b3;border-color:var(--bad)}
.stage.skipped{color:var(--mut);border-style:dashed}.stage.pending{opacity:.55}
.run{border-top:1px solid var(--line);padding:8px 0}
.run:first-child{border-top:0}
.run .meta{display:flex;gap:10px;align-items:center;flex-wrap:wrap;font-size:12px;color:var(--mut);margin-bottom:6px}
.batch li{font-size:12px}
.report{max-height:420px}
.ok{color:var(--good)}.fail{color:var(--bad)}
"""

_CONNECTORS = """
<div class="section-title">Connectors &amp; permissions
  <span class="muted small">enable / disable · allow-list · refresh interval · health</span>
  <span style="margin-left:auto" class="row"><button class="btn sm" id="run-due-btn" title="run every due schedule now">Run due refreshes</button>
  <button class="btn sm" id="connectors-refresh">Refresh</button></span></div>
<div class="conn" id="connectors"><div class="empty">Sign in as an admin to load the connectors.</div></div>
"""

_RUNS = card('Pipeline runs <span class="dot" id="runs-live"></span>',
             '<div id="runs" class="empty">—</div>', "runs-panel",
             right='<span class="muted small" id="runs-status">polls every 2 s while a run is active</span>')

_UPLOAD = card("Bulk upload",
               '<div class="col">'
               '<div class="row"><input id="upload-filename" placeholder="filename" style="flex:1">'
               '<select id="upload-acl"><option value="public">public</option><option value="restricted">restricted</option></select>'
               '<button class="btn sm" id="upload-add" type="button">Add to batch</button></div>'
               '<textarea id="upload-text" placeholder="document text for the file above"></textarea>'
               '<ol class="batch" id="upload-batch"></ol>'
               '<details><summary>…or paste a JSON array of {filename, text, acl?}</summary>'
               '<textarea id="upload-json" placeholder=\'[{"filename":"qa/a.md","text":"# A"}]\'></textarea></details>'
               '<div class="row"><button class="btn primary" id="upload-btn">Upload batch</button><span class="muted small" id="upload-status"></span></div>'
               '</div>', "bulk-upload")

_DELETE = card("Bulk delete",
               '<div class="col">'
               '<textarea id="delete-ids" placeholder="document ids, one per line or comma-separated"></textarea>'
               '<div class="row"><input id="delete-source" placeholder="…or every document of a source (e.g. jira)" style="flex:1">'
               '<input id="delete-prefix" placeholder="…or by uri prefix (e.g. jira://REL/)" style="flex:1"></div>'
               '<div class="row"><button class="btn danger" id="delete-btn">Delete matching documents</button>'
               '<span class="muted small" id="delete-status">Tombstones documents, removes passages from retrieval, bumps the dataset version.</span></div>'
               '</div>', "bulk-delete")

_BUDGET = card("Budget",
               '<div class="row"><label class="muted small">Cap (USD)</label><input type="number" id="budget-cap" step="0.5" min="0" value="5">'
               '<button class="btn primary sm" id="budget-btn">Set cap</button></div>'
               '<div class="small" style="margin-top:8px">Spent: <b id="budget-spent">—</b> <span class="muted" id="budget-note">enforced before every model call</span></div>',
               "budget")

_USERS = card("Users &amp; roles",
              '<div class="tablewrap"><table id="users-table"><thead><tr><th>Subject</th><th>Roles</th><th>Scopes</th></tr></thead>'
              '<tbody id="users-rows"><tr><td colspan="3" class="empty">—</td></tr></tbody></table></div>', "users")

_AUTHORITY = card("Source authority",
                  '<div id="authority-ranks" class="row"></div>'
                  '<div class="row" style="margin-top:8px"><select id="authority-source"></select>'
                  '<input type="number" id="authority-rank" min="1" max="9" value="1" style="width:70px">'
                  '<button class="btn sm" id="authority-btn">Set rank</button></div>', "authority-editor")

_AUDIT = card("Audit tail",
              '<div class="tablewrap"><table id="audit-table"><thead><tr><th>When</th><th>Subject</th><th>Action</th><th>Resource</th><th>Decision</th></tr></thead>'
              '<tbody id="audit-rows"><tr><td colspan="5" class="empty">—</td></tr></tbody></table></div>', "audit-tail",
              right='<button class="btn sm" id="audit-refresh">Refresh</button>')

_AWS = card("AWS readiness",
            '<div class="row"><label class="muted small">Target</label><select id="doctor-target"><option value="aws">aws</option><option value="local">local</option></select>'
            '<button class="btn sm primary" id="doctor-btn">Run doctor</button><span id="doctor-exit"></span></div>'
            '<div class="row" id="doctor-selection" style="margin:8px 0"></div>'
            '<pre class="report" id="doctor-report">Run the readiness doctor to see env vars, adapters, IaC and secrets checks.</pre>',
            "aws-panel")

_BODY = (_CONNECTORS
         + f'<div style="margin-top:14px">{_RUNS}</div>'
         + f'<div class="grid two" style="margin-top:14px">{_UPLOAD}{_DELETE}</div>'
         + f'<div class="grid" style="grid-template-columns:repeat(auto-fit,minmax(300px,1fr));margin-top:14px">{_BUDGET}{_USERS}{_AUTHORITY}</div>'
         + f'<div class="grid two" style="margin-top:14px">{_AUDIT}{_AWS}</div>')

_JS = r"""
const {$,esc,num,toast,gate,api,ms,when,ago}=KF;
const PIPELINE=['detect','convert','chunk','extract','graph','embed','health'];
let BATCH=[],POLL=null,FORCE=0,SOURCES=[];

// ---------------------------------------------------------------- connectors
function healthBadge(h){if(!h||h.last_run==null&&!h.interval_s)return '<span class="pill">never synced</span>';
 if(h.sla_breach)return '<span class="pill bad">SLA BREACH</span>';
 if((h.last_status||'').startsWith('error'))return '<span class="pill bad">error</span>';
 if(h.error_count>0)return '<span class="pill warn">'+h.error_count+' error(s)</span>';
 return '<span class="pill good">healthy</span>'}

function connCard(c){const h=c.health||{};const sched=h.interval_s||'';
 return '<div class="conn-card'+(h.sla_breach?' breach':'')+(c.enabled?'':' off')+'" data-source="'+esc(c.source)+'">'+
  '<h4>'+esc(c.source)+(c.registered?'':' <span class="pill warn" title="configured but no connector module registered">unregistered</span>')+
   '<span style="margin-left:auto">'+healthBadge(h)+'</span>'+
   '<label class="switch" title="enable / disable"><input type="checkbox" data-role="enabled" '+(c.enabled?'checked':'')+'><i></i></label></h4>'+
  '<div class="hl"><span>freshness</span><span class="mono">'+(h.freshness_minutes==null?'—':h.freshness_minutes+' min')+'</span>'+
   '<span>last status</span><span class="mono">'+esc(h.last_status||'—')+'</span>'+
   '<span>items</span><span class="mono">'+num(h.items)+'</span><span>errors</span><span class="mono">'+num(h.error_count)+'</span>'+
   '<span>next run</span><span class="mono">'+(h.next_run?esc(when(h.next_run)):'not scheduled')+'</span>'+
   '<span>scopes</span><span class="mono">'+esc((c.scopes||[]).join(', ')||'—')+'</span></div>'+
  '<label>Allow-list (comma separated projects / repos / paths; empty = everything the scopes permit)</label>'+
  '<input type="text" data-role="allow" value="'+esc((c.allow||[]).join(', '))+'">'+
  '<div class="row" style="margin-top:8px"><label>Refresh every</label><input type="number" data-role="interval" min="30" step="30" value="'+esc(sched)+'" placeholder="seconds"><span class="muted small">s</span>'+
   '<button class="btn sm" data-act="save">Save</button><button class="btn sm primary" data-act="sync" '+(c.enabled?'':'disabled')+'>Sync now</button></div>'+
  '</div>'}

function renderConnectors(list){SOURCES=list.map(c=>c.source);
 $('#connectors').innerHTML=list.map(connCard).join('')||'<div class="empty">no connectors registered</div>';
 KF.$$('#connectors .conn-card').forEach(card=>{const source=card.dataset.source;
  card.querySelector('[data-role=enabled]').onchange=e=>saveConnector(source,{enabled:e.target.checked});
  card.querySelector('[data-act=save]').onclick=()=>{const allow=card.querySelector('[data-role=allow]').value.split(',').map(s=>s.trim()).filter(Boolean);
   const iv=card.querySelector('[data-role=interval]').value;const body={allow,enabled:card.querySelector('[data-role=enabled]').checked};if(iv)body.interval_s=+iv;saveConnector(source,body)};
  card.querySelector('[data-act=sync]').onclick=()=>syncNow(source)});
 const sel=$('#authority-source');const cur=sel.value;sel.innerHTML='';SOURCES.forEach(s=>{const o=document.createElement('option');o.value=o.text=s;sel.add(o)});if(cur)sel.value=cur}

async function loadConnectors(){try{const d=await api('/admin/connectors');gate(null);renderConnectors(d.connectors||[])}
 catch(e){gate(e,'admin');if(e.status!==401&&e.status!==403)toast(e.message,'bad');throw e}}

async function saveConnector(source,body){try{const out=await api('/admin/connectors',{method:'POST',body:Object.assign({source},body)});
 toast(source+' saved · '+(out.connector.enabled?'enabled':'disabled')+(body.interval_s?' · every '+body.interval_s+' s':''),'good');await loadConnectors()}catch(e){toast(e.message,'bad')}}

async function syncNow(source){try{$('#runs-status').textContent='syncing '+source+'…';FORCE=4;schedulePoll();
 const out=await api('/admin/sync',{method:'POST',body:{source}});
 toast(source+': pulled '+num(out.pulled)+', ingested '+num(out.ingested)+', tombstoned '+num(out.tombstoned)+' · '+out.status,out.status==='ok'?'good':'warn');
 await Promise.all([loadConnectors(),loadRuns(),loadAudit()])}catch(e){toast(e.message,'bad');$('#runs-status').textContent=e.message}}

async function runDue(){try{const out=await api('/admin/refresh/run-due',{method:'POST',body:{}});FORCE=3;schedulePoll();
 toast((out.ran||[]).length+' due source(s) refreshed','good');await Promise.all([loadConnectors(),loadRuns()])}catch(e){toast(e.message,'bad')}}

// ---------------------------------------------------------------- pipeline runs
function stageChips(run){const byName={};(run.stages||[]).forEach(s=>byName[s.name]=s);
 const chip=(name,s)=>'<span class="stage '+(s?esc(s.status):'pending')+'" title="'+(s?esc(s.count+' item(s) · '+ms(s.ms)+(s.detail?' · '+s.detail:'')):'not reached')+'">'+esc(name)+(s?' · '+num(s.count):'')+'</span>';
 const extras=(run.stages||[]).filter(s=>PIPELINE.indexOf(s.name)<0);
 return '<div class="stages">'+PIPELINE.map(n=>chip(n,byName[n])).join('')+(extras.length?'<span class="muted small" style="margin:0 4px">|</span>'+extras.map(s=>chip(s.name,s)).join(''):'')+'</div>'}

function runRow(r){const st=r.status==='running'?'<span class="pill info">running</span>':r.status==='ok'?'<span class="pill good">ok</span>':'<span class="pill bad">'+esc(r.status)+'</span>';
 return '<div class="run"><div class="meta">'+st+'<b>'+esc(r.source)+'</b><span class="mono">'+esc(r.id)+'</span><span>'+num(r.items)+' item(s)</span><span>'+(r.duration_ms==null?'…':ms(r.duration_ms))+'</span><span>'+esc(ago(r.started_at))+'</span></div>'+stageChips(r)+'</div>'}

async function loadRuns(){try{const d=await api('/admin/runs?limit=12');const runs=d.runs||[];
 $('#runs').className=runs.length?'':'empty';$('#runs').innerHTML=runs.map(runRow).join('')||'No pipeline runs yet — press "Sync now" on a connector or upload a batch.';
 const active=runs.some(r=>r.status==='running');$('#runs-live').className='dot'+(active||FORCE>0?' live':'');
 if(!active&&FORCE<=0)$('#runs-status').textContent=runs.length?'last run '+ago(runs[0].started_at)+' · idle':'idle';
 else $('#runs-status').textContent=active?'run in progress — polling every 2 s':'polling…';
 return active}catch(e){return false}}

function schedulePoll(){if(POLL)return;POLL=setInterval(async()=>{const active=await loadRuns();if(FORCE>0)FORCE--;if(!active&&FORCE<=0){clearInterval(POLL);POLL=null;$('#runs-live').className='dot'}},2000)}

// ---------------------------------------------------------------- bulk upload / delete
function renderBatch(){$('#upload-batch').innerHTML=BATCH.map((f,i)=>'<li><b>'+esc(f.filename)+'</b> <span class="muted">'+f.text.length+' chars · '+esc(f.acl.join(','))+'</span> <a href="#" data-i="'+i+'">remove</a></li>').join('');
 KF.$$('#upload-batch a').forEach(a=>a.onclick=e=>{e.preventDefault();BATCH.splice(+a.dataset.i,1);renderBatch()})}
function addToBatch(){const filename=$('#upload-filename').value.trim(),text=$('#upload-text').value;if(!filename||!text.trim()){toast('filename and text are required','warn');return}
 BATCH.push({filename,text,acl:[$('#upload-acl').value]});$('#upload-filename').value='';$('#upload-text').value='';renderBatch()}
async function upload(){let files=BATCH.slice();const raw=$('#upload-json').value.trim();
 if(raw){try{const arr=JSON.parse(raw);if(!Array.isArray(arr))throw new Error('JSON must be an array');files=files.concat(arr)}catch(e){toast('Invalid JSON: '+e.message,'bad');return}}
 if(!files.length){toast('Nothing to upload — add files to the batch first','warn');return}
 $('#upload-btn').disabled=true;$('#upload-status').textContent='uploading '+files.length+' file(s)…';FORCE=4;schedulePoll();
 try{const out=await api('/admin/upload',{method:'POST',body:{files}});
  $('#upload-status').textContent='uploaded '+out.uploaded+' · ingested '+out.ingested+(out.noops?' · '+out.noops+' unchanged':'')+' · dataset v'+out.dataset_version+' · run '+out.run_id;
  toast('Bulk upload done · dataset v'+out.dataset_version,'good');BATCH=[];renderBatch();$('#upload-json').value='';await Promise.all([loadRuns(),loadAudit()])}
 catch(e){$('#upload-status').textContent=e.message;toast(e.message,'bad')}finally{$('#upload-btn').disabled=false}}

async function bulkDelete(){const ids=$('#delete-ids').value.split(/[\s,]+/).map(s=>s.trim()).filter(Boolean);const source=$('#delete-source').value.trim();const prefix=$('#delete-prefix').value.trim();
 if(!ids.length&&!source&&!prefix){toast('Give document ids, a source or a uri prefix','warn');return}
 const what=[ids.length?ids.length+' id(s)':'',source?'every "'+source+'" document':'',prefix?'uri prefix "'+prefix+'"':''].filter(Boolean).join(' + ');
 if(!confirm('Bulk delete '+what+'?\nThis tombstones the documents, removes their passages from retrieval and bumps the dataset version.'))return;
 try{const body={};if(ids.length)body.document_ids=ids;if(source)body.source=source;if(prefix)body.uri_prefix=prefix;
  const out=await api('/admin/bulk-delete',{method:'POST',body});$('#delete-status').textContent='deleted '+out.deleted+' document(s) · dataset v'+out.dataset_version;
  toast('Deleted '+out.deleted+' document(s)','good');$('#delete-ids').value='';await loadAudit()}catch(e){toast(e.message,'bad')}}

// ---------------------------------------------------------------- budget / users / authority / audit / doctor
async function setBudget(){try{const out=await api('/admin/budget',{method:'POST',body:{cap:+$('#budget-cap').value}});
 $('#budget-spent').textContent=KF.money(out.spent);$('#budget-note').textContent='cap $'+Number(out.cap).toFixed(2)+' for '+out.tenant;toast('Budget cap set to $'+out.cap,'good')}catch(e){toast(e.message,'bad')}}
async function loadUsers(){try{const d=await api('/admin/users');
 $('#users-rows').innerHTML=(d.users||[]).map(u=>'<tr><td><b>'+esc(u.subject)+'</b></td><td>'+(u.roles||[]).map(r=>'<span class="pill '+({admin:'violet',curator:'info',asker:'good',agent:'warn'}[r]||'')+'">'+esc(r)+'</span>').join(' ')+'</td><td class="mono">'+esc((u.scopes||[]).join(', '))+'</td></tr>').join('')||'<tr><td colspan="3" class="empty">no users</td></tr>'}catch(e){}}
async function loadAuthority(){try{const d=await api('/admin/authority');
 $('#authority-ranks').innerHTML=(d.ranks||[]).map(r=>'<span class="pill '+(r.rank===1?'good':'')+'" title="weight '+esc(r.weight)+'">'+esc(r.source)+' · rank '+esc(r.rank)+(r.overridden?' *':'')+'</span>').join('')}catch(e){}}
async function setAuthority(){try{await api('/admin/authority',{method:'POST',body:{source:$('#authority-source').value,rank:+$('#authority-rank').value}});toast('Authority rank saved','good');await Promise.all([loadAuthority(),loadAudit()])}catch(e){toast(e.message,'bad')}}
async function loadAudit(){try{const d=await api('/admin/audit?limit=40');
 $('#audit-rows').innerHTML=(d.audit||[]).map(a=>'<tr><td class="mono small">'+esc(when(a.at))+'</td><td>'+esc(a.subject)+(a.is_agent?' <span class="pill warn">agent</span>':'')+'</td><td><span class="pill">'+esc(a.action)+'</span></td><td class="mono small">'+esc(a.resource)+'</td><td class="small">'+esc(String(a.decision||'').slice(0,120))+'</td></tr>').join('')||'<tr><td colspan="5" class="empty">no audit entries</td></tr>'}catch(e){}}
async function doctor(){$('#doctor-btn').disabled=true;$('#doctor-report').textContent='running doctor…';$('#doctor-exit').innerHTML='';
 try{const d=await api('/admin/doctor?target='+encodeURIComponent($('#doctor-target').value));const sel=(d.selection||{}).selected||{};
  $('#doctor-selection').innerHTML=Object.keys(sel).map(k=>'<span class="pill">'+esc(k)+' → '+esc(sel[k])+'</span>').join('')+(d.selection&&d.selection.ready!=null?'<span class="pill '+(d.selection.ready?'good':'warn')+'">adapters '+(d.selection.ready?'ready':'not ready')+'</span>':'');
  $('#doctor-exit').innerHTML=d.exit_code==null?'<span class="pill warn">doctor script missing</span>':d.exit_code===0?'<span class="pill good">READY · exit 0</span>':'<span class="pill bad">BLOCKERS · exit '+esc(d.exit_code)+'</span>';
  $('#doctor-report').textContent=d.report||d.error||JSON.stringify(d,null,2)}
 catch(e){$('#doctor-report').textContent=e.message;toast(e.message,'bad')}finally{$('#doctor-btn').disabled=false}}

// ---------------------------------------------------------------- boot
async function loadAll(){try{await loadConnectors()}catch(e){return}
 await Promise.all([loadRuns(),loadUsers(),loadAuthority(),loadAudit()]);
 if(await loadRuns())schedulePoll()}
window.KF_ON_SESSION=s=>{if(s)loadAll();else{$('#connectors').innerHTML='';gate({status:401,message:''},'admin')}};
KF.initBar({preferRole:'admin'});
$('#connectors-refresh').onclick=loadAll;$('#run-due-btn').onclick=runDue;
$('#upload-add').onclick=addToBatch;$('#upload-btn').onclick=upload;$('#delete-btn').onclick=bulkDelete;
$('#budget-btn').onclick=setBudget;$('#authority-btn').onclick=setAuthority;$('#audit-refresh').onclick=loadAudit;$('#doctor-btn').onclick=doctor;
if(KF.session)loadAll();else gate({status:401,message:''},'admin');
"""

ADMIN_HTML = shell("Knowledge Fabric · Admin",
                   "Admin console · connectors, permissions, refresh, bulk data, budgets, users, audit, cloud readiness",
                   _BODY, _JS, "Admin", _CSS)
