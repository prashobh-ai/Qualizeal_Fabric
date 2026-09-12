
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
function renderUsers(users){
 $('#users-rows').innerHTML=(users||[]).map(u=>'<tr><td><b>'+esc(u.subject)+'</b></td><td class="small">'+(u.designation?esc(u.designation):'<span class="muted">—</span>')+'</td><td>'+(u.roles||[]).map(r=>'<span class="pill '+({admin:'violet',curator:'info',asker:'good',agent:'warn'}[r]||'')+'">'+esc(r)+'</span>').join(' ')+'</td><td class="mono">'+esc((u.scopes||[]).join(', '))+'</td><td>'+(u.subject==='admin'?'':'<button class="btn sm danger del-user" data-s="'+esc(u.subject)+'">Remove</button>')+'</td></tr>').join('')||'<tr><td colspan="5" class="empty">no users</td></tr>';
 KF.$$('#users-rows .del-user').forEach(b=>b.onclick=()=>delUser(b.dataset.s))}
async function loadUsers(){try{const d=await api('/admin/users');renderUsers(d.users)}catch(e){}}
async function addUser(){const subject=$('#nu-subject').value.trim();if(!subject){toast('Enter a user id','warn');return}
 const role=$('#nu-role').value;const scopes=($('#nu-restricted').checked||role!=='asker')?['public','restricted']:['public'];
 const designation=$('#nu-designation').value.trim();
 try{const d=await api('/admin/users',{method:'POST',body:{subject,roles:[role],scopes,designation}});renderUsers(d.users);
  $('#nu-subject').value='';$('#nu-designation').value='';toast('Added '+subject,'good');loadAudit()}catch(e){toast(e.message,'bad')}}
async function delUser(subject){if(!confirm('Remove user '+subject+'?'))return;
 try{const d=await api('/admin/users',{method:'POST',body:{subject,action:'delete'}});renderUsers(d.users);
  toast('Removed '+subject,'good');loadAudit()}catch(e){toast(e.message,'bad')}}
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

// ---------------------------------------------------------------- models (T35/T36)
function fmtRow(cells){return '<tr>'+cells.map(c=>'<td>'+c+'</td>').join('')+'</tr>'}
function usd(x){return '$'+(Number(x)||0).toFixed(4)}
// T55/T56 — the token-meter overview chips: efficiency, active-vs-idle, cost
// by phase, burn rate and the provider quota. Everything reconciles with the
// ledger totals above; absent telemetry simply renders nothing.
function renderModelsTelemetry(tel){const box=$('#models-telemetry');if(!box)return;
 if(!tel||tel.error||!tel.totals){box.innerHTML=tel&&tel.error?'<span class="muted small">telemetry unavailable</span>':'';return}
 const eff=tel.efficiency||{},burn=tel.burn_rate||{},quota=tel.provider_quota||{};
 const phases=(tel.cost_breakdown||[]).filter(p=>p.calls||p.cost_usd);
 const chips=[];
 if(eff.efficiency!=null)chips.push('<span class="pill" title="cited output tokens / total tokens">efficiency '+Math.round((eff.efficiency||0)*100)+'%</span>');
 if(quota.provider)chips.push('<span class="pill" title="provider quota">'+esc(quota.provider)+' · '+esc(quota.limit||'')+'</span>');
 if(burn.cost_per_hour!=null)chips.push('<span class="pill">burn '+usd(burn.cost_per_hour)+'/h · ~'+usd(burn.projected_per_day)+'/day</span>');
 phases.forEach(p=>chips.push('<span class="pill" title="'+esc(p.calls||0)+' calls · '+esc(p.tokens||0)+' tokens">'+esc(p.phase)+' '+usd(p.cost_usd)+'</span>'));
 box.innerHTML=chips.join(' ')||'<span class="muted small">no model activity in this window</span>'}
async function loadModels(){try{const days=$('#models-days')?$('#models-days').value:'7';const d=await api('/admin/models?days='+encodeURIComponent(days));
 const p=d.provider||{};const c=d.consumption||{};const t=c.totals||{};
 const prov=p.provider?('<span class="pill good">'+esc(p.provider)+'</span> <span class="pill">key …'+esc(String(p.key_fingerprint||'').slice(-4))+'</span> <span class="pill">small '+esc(p.model_small)+'</span> <span class="pill">large '+esc(p.model_large)+'</span> <span class="muted small">verified '+esc(p.verified_at||'')+' · ping '+esc(((p.ping_usage||{}).input_tokens||0)+'/'+((p.ping_usage||{}).output_tokens||0))+' tokens</span>')
  :('<span class="pill '+(d.key_present?'warn':'bad')+'">'+(d.key_present?'key present — run doctor --require anthropic':'no ANTHROPIC_API_KEY')+'</span>');
 const badge=d.provider_badge||{};const badgeHtml=badge.label?('<span class="pill" title="the model that answers now"><span style="display:inline-block;width:8px;height:8px;border-radius:50%;background:'+esc(badge.dot||'#5A6B7C')+';margin-right:5px"></span>'+esc(badge.label)+'</span> '):'';
 $('#provider-card').innerHTML=badgeHtml+prov+' <span class="pill">mode '+esc(d.mode||'')+'</span> <span class="muted small">allowed: '+esc((d.allowed_models||[]).join(', '))+'</span>';
 $('#models-totals').innerHTML=['<span class="pill">'+esc(t.calls||0)+' calls</span>','<span class="pill">in '+esc(t.input_tokens||0)+'</span>','<span class="pill">out '+esc(t.output_tokens||0)+'</span>','<span class="pill">cache read '+esc(t.cache_read||0)+'</span>','<span class="pill">cache write '+esc(t.cache_write||0)+'</span>','<span class="pill good">'+usd(t.cost_usd)+'</span>'].join(' ');
 renderModelsTelemetry(d.telemetry||{});
 const tbl=(id,obj,extra)=>{const b=$(id+' tbody');const ks=Object.keys(obj||{});b.innerHTML=ks.length?ks.map(k=>{const v=obj[k];return fmtRow([esc(k),esc(v.calls),esc(v.input_tokens),esc(v.output_tokens),esc(v.cache_read)].concat(extra?[esc(v.cache_write)]:[]).concat([usd(v.cost_usd)]))}).join(''):'<tr><td colspan="7" class="empty">—</td></tr>'};
 tbl('#models-purpose',c.by_purpose);tbl('#models-model',c.by_model);tbl('#models-day',c.by_day,true);
 $('#models-prices').innerHTML=Object.keys(c.prices||{}).map(m=>{const v=c.prices[m];return '<span class="pill" title="cache read '+esc(v.cache_read)+' · cache write '+esc(v.cache_write)+'">'+esc(m)+' · in '+esc(v.input)+' · out '+esc(v.output)+'</span>'}).join(' ')||'<span class="muted small">—</span>';
 $('#models-calls tbody').innerHTML=(c.last_calls||[]).map(r=>fmtRow([esc((r.ts||'').replace('T',' ').slice(0,19)),'<span class="pill">'+esc(r.purpose)+'</span>',esc(r.model),esc(r.workflow),esc(r.input_tokens),esc(r.output_tokens),esc(r.cache_read_input_tokens),esc(r.latency_ms)+'ms',usd(r.cost_usd),'<span class="mono small">'+esc(String(r.request_id||'').slice(0,18))+'</span>'])).join('')||'<tr><td colspan="10" class="empty">no API calls recorded</td></tr>'}catch(e){}}

// ---------------------------------------------------------------- sources (T47)
// GitHub / Jira / Confluence cards from GET /admin/sources: last run + next run
// (refresh scheduler), counts (facts.json), the live rate limit when known.
const SRC_LABEL={github:'GitHub',jira:'Jira',confluence:'Confluence'};
const SRC_COUNTS={github:['repositories','commits','prs','issues'],jira:['projects','issues'],confluence:['spaces','pages']};
function srcStatus(s){const st=String(s.last_status||'');if(st.startsWith('error'))return '<span class="pill bad" title="'+esc(st)+'">error</span>';
 if(st.startsWith('skipped'))return '<span class="pill warn">'+esc(st)+'</span>';
 if(s.last_run)return '<span class="pill good">'+esc(st||'ok')+'</span>';return '<span class="pill">never run</span>'}
function srcCard(name,s){s=s||{};const counts=s.counts||{};
 return '<div class="conn-card'+(s.enabled?'':' off')+'" data-source="'+esc(name)+'"><h4>'+esc(SRC_LABEL[name]||name)+'<span style="margin-left:auto">'+srcStatus(s)+'</span></h4>'+
  '<div class="hl"><span>last run</span><span class="mono">'+(s.last_run?esc(ago(s.last_run)):'—')+'</span>'+
  '<span>next run</span><span class="mono">'+(s.next_run?esc(when(s.next_run)):'not scheduled')+'</span>'+
  (SRC_COUNTS[name]||Object.keys(counts)).map(k=>'<span>'+esc(k)+'</span><span class="mono">'+num(counts[k])+'</span>').join('')+
  (name==='github'?'<span>rate limit remaining</span><span class="mono">'+(s.rate_limit_remaining==null?'unknown — live connector not attached':num(s.rate_limit_remaining))+'</span>':'')+
  '<span>facts as of</span><span class="mono">'+esc(s.as_of||'—')+'</span></div></div>'}
async function loadSources(){try{const d=await api('/admin/sources');$('#sources-cards').innerHTML=['github','jira','confluence'].map(n=>srcCard(n,d[n])).join('')}
 catch(e){$('#sources-cards').innerHTML='<div class="empty">'+esc(e.message)+'</div>'}}

// ---------------------------------------------------------------- T83 coverage matrix
const COV_COLOR={green:'#0CA678',amber:'#E8A23A',coral:'#F53E5A',na:'#C7CDD4'};
let COVERAGE=null;
function renderCoverage(rep){COVERAGE=rep;const personas=rep.personas||[];
 const head='<tr><th>Data type</th>'+personas.map(p=>'<th class="small">'+esc(p)+'</th>').join('')+'</tr>';
 const rows=(rep.matrix||[]).map(m=>{
   const tds=personas.map(p=>{const c=(m.cells||{})[p];
     if(!c)return '<td></td>';
     const col=COV_COLOR[c.status]||'#C7CDD4';const t=c.status+' — '+(c.passed||0)+'/'+(c.n||0);
     return '<td style="text-align:center"><span class="cov-cell" title="'+esc(t)+'" data-dt="'+esc(m.data_type)+'" data-p="'+esc(p)+'" style="background:'+col+'">'+(c.status==='na'?'·':(c.status[0].toUpperCase()))+'</span></td>'}).join('');
   return '<tr><td>'+esc(m.label)+(m.held?'':' <span class="muted small">(not held)</span>')+'</td>'+tds+'</tr>'}).join('');
 $('#coverage-table').querySelector('thead').innerHTML=head;
 $('#coverage-rows').innerHTML=rows||'<tr><td class="empty">No coverage data.</td></tr>';
 const s=rep.summary||{};$('#coverage-summary').textContent=(s.green||0)+' green · '+(s.amber||0)+' amber · '+(s.coral||0)+' coral · '+(s.na||0)+' n/a'+(rep.error?(' · '+rep.error):'');
 const v=$('#coverage-verdict');v.textContent=rep.passed?'gate: pass':'gate: '+((rep.coral_held||[]).length)+' coral';v.className='pill '+(rep.passed?'good':'bad');
 KF.$$('#coverage-rows .cov-cell').forEach(el=>el.onclick=()=>showCoverageCell(el.dataset.dt,el.dataset.p))}
function showCoverageCell(dt,persona){if(!COVERAGE)return;
 const m=(COVERAGE.matrix||[]).find(x=>x.data_type===dt);const c=m&&(m.cells||{})[persona];
 if(!c){$('#coverage-detail').textContent='';return}
 $('#coverage-detail').innerHTML='<b>'+esc(m.label)+' · '+esc(persona)+'</b> — '+esc(c.status)+' ('+(c.passed||0)+'/'+(c.n||0)+')<ul class="queue">'+
   (c.questions||[]).map(q=>'<li><span class="pill '+(q.ok?'good':'bad')+'">'+(q.ok?'ok':q.kind)+'</span> '+esc(q.question)+(q.cited&&q.cited.length?' <span class="muted small">→ '+esc((q.cited||[]).join(', '))+'</span>':'')+'</li>').join('')+'</ul>'}
async function loadCoverage(){try{renderCoverage(await api('/admin/coverage'))}catch(e){if(e.status!==404)toast(e.message,'bad')}}

// ---------------------------------------------------------------- T86 service levels
function pctm(x){return (x==null)?'—':Math.round(x)+' ms'}
function shr(x){return (x==null)?'—':Math.round(x*100)+'%'}
function renderSLA(rep){const h=rep.headline||{};
 $('#sla-headline').className='';
 $('#sla-headline').innerHTML='<div class="pill good" style="font-size:13px">'+esc(h.sla_line||'')+'</div>'+
   '<div class="muted small" style="margin-top:6px">'+esc(h.reading_line||'')+'</div>'+
   '<div class="row" style="margin-top:8px;gap:14px;flex-wrap:wrap">'+
   '<span class="kv"><span>Answers</span><b>'+num(rep.n_answers||0)+'</b></span>'+
   '<span class="kv"><span>Fast path</span><b>'+shr(h.fast_share)+'</b></span>'+
   '<span class="kv"><span>Agent</span><b>'+shr(h.agent_share)+'</b></span>'+
   '<span class="kv"><span>Explain rate</span><b>'+shr(h.explain_rate)+'</b></span>'+
   '<span class="kv"><span>Cost/answer</span><b>$'+Number(h.cost_per_answer||0).toFixed(5)+'</b></span></div>';
 const pr=(rep.by_persona||[]).map(r=>'<tr><td>'+esc(r.persona)+'</td><td>'+num(r.n)+'</td><td>'+pctm(r.p50_ms)+'</td><td>'+pctm(r.p95_ms)+'</td><td>'+shr(r.fast_share)+'</td><td>'+shr(r.agent_share)+'</td><td>'+shr(r.explain_rate)+'</td><td>$'+Number(r.cost_per_answer||0).toFixed(5)+'</td></tr>').join('');
 $('#sla-persona').querySelector('tbody').innerHTML=pr||'<tr><td colspan="8" class="empty">No answers recorded yet.</td></tr>';
 const dr=(rep.by_data_type||[]).map(r=>'<tr><td>'+esc(r.data_type)+'</td><td>'+num(r.n)+'</td><td>'+pctm(r.p50_ms)+'</td><td>'+pctm(r.p95_ms)+'</td><td>'+shr(r.fast_share)+'</td><td>'+shr(r.agent_share)+'</td><td>$'+Number(r.cost_per_answer||0).toFixed(5)+'</td></tr>').join('');
 $('#sla-datatype').querySelector('tbody').innerHTML=dr||'<tr><td colspan="7" class="empty">No answers recorded yet.</td></tr>'}
async function loadSLA(){try{renderSLA(await api('/admin/service-levels'))}catch(e){if(e.status!==404)toast(e.message,'bad')}}

// ---------------------------------------------------------------- boot
async function loadAll(){try{await loadConnectors()}catch(e){return}
 await Promise.all([loadRuns(),loadUsers(),loadAuthority(),loadAudit(),loadModels(),loadSources(),loadCoverage(),loadSLA()]);
 if(await loadRuns())schedulePoll()}
window.KF_ON_SESSION=s=>{if(s)loadAll();else{$('#connectors').innerHTML='';gate({status:401,message:''},'admin')}};
KF.initBar({preferRole:'admin'});
$('#connectors-refresh').onclick=loadAll;$('#run-due-btn').onclick=runDue;
$('#upload-add').onclick=addToBatch;$('#upload-btn').onclick=upload;$('#delete-btn').onclick=bulkDelete;
$('#budget-btn').onclick=setBudget;$('#authority-btn').onclick=setAuthority;$('#audit-refresh').onclick=loadAudit;$('#doctor-btn').onclick=doctor;$('#models-refresh').onclick=loadModels;$('#models-days').onchange=loadModels;
$('#add-user-btn').onclick=addUser;
if(KF.session)loadAll();else gate({status:401,message:''},'admin');
