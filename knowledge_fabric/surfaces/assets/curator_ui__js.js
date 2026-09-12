
const {$,esc,num,pct,toast,gate,api,bar,level,ago}=KF;
const SUG_CLS={keep:'good',review:'warn',delete:'bad'};
let DOCS=[],DATASET=0;

function tile(l,v,d,cls){return '<div class="card kpi" style="padding:12px"><div class="l">'+l+'</div><div class="v">'+v+'</div>'+(d?'<div class="d '+(cls||'muted')+'">'+d+'</div>':'')+'</div>'}
function kcls(x,good,warn){return {good:'',warn:'risk-medium',bad:'risk-high'}[level(x,good,warn)]}

function renderQuality(q){const s=q.suggestions||{};
 $('#quality-tiles').innerHTML=[
  tile('Coverage',pct(q.coverage),'ontology vocabulary present',kcls(q.coverage,0.7,0.4)),
  tile('Freshness',pct(q.freshness),'share of sources within SLA',kcls(q.freshness,0.9,0.6)),
  tile('Citation coverage',pct(q.citation_coverage),'docs cited at least once',kcls(q.citation_coverage,0.6,0.3)),
  tile('Traceability',pct(q.traceability),'passages with provenance',kcls(q.traceability,0.99,0.9)),
  tile('Connectedness',pct(q.connectedness),'graph density',kcls(q.connectedness,0.5,0.2)),
  tile('Readability',pct(q.readability_avg),'average across passages',kcls(q.readability_avg,0.6,0.35)),
  tile('Duplicate rate',pct(q.duplicate_rate),'cross-document duplicates',{good:'',warn:'risk-medium',bad:'risk-high'}[q.duplicate_rate<=0.05?'good':q.duplicate_rate<=0.2?'warn':'bad']),
  tile('Contradictions',num(q.contradictions),'conflict-flagged edges',q.contradictions?'risk-high':''),
  tile('Open gaps',num(q.gaps),'unanswered questions',q.gaps?'risk-medium':''),
  tile('Documents',num(q.documents),num(q.passages)+' passages'),
  tile('Suggestions','<span class="pill good">'+num(s.keep)+' keep</span> <span class="pill warn">'+num(s.review)+' review</span> <span class="pill bad">'+num(s.delete)+' delete</span>','')].join('');
 $('#quality-meta').textContent='snapshot '+ago(q.snapshot_at);
 const rr=q.risk_register||[];
 $('#risk-register').className=rr.length?'':'empty';
 $('#risk-register').innerHTML=rr.length?'<table><tr><th>Risk</th><th>Value</th><th>Severity</th></tr>'+rr.map(r=>'<tr><td>'+esc(r.risk)+'</td><td class="mono">'+esc(r.value)+'</td><td class="risk-'+esc(r.severity)+'"><b>'+esc(r.severity)+'</b></td></tr>').join('')+'</table>':'No open risks — the knowledge base is healthy.'}

function renderQueues(g){const fill=(id,list)=>{$('#'+id+'-count').textContent=(list||[]).length;
 $('#'+id+'-list').innerHTML=(list||[]).slice(0,8).map(x=>'<li>'+esc(x.item||x.question||JSON.stringify(x))+' <span class="muted small">'+esc(x.status||'')+'</span></li>').join('')||'<li class="muted small">none</li>'};
 fill('gaps',g.gaps);fill('contradictions',g.contradictions);fill('review',g.review_queue)}

function signals(d){const s=d.signals||{};return '<div class="signals">'+
 '<span class="pill" title="times cited by answered questions">cited '+num(s.citation_uses)+'</span>'+
 '<span class="pill" title="days since ingestion">'+Number(s.age_days||0).toFixed(0)+' d old</span>'+
 (s.duplicate_passages?'<span class="pill warn">'+num(s.duplicate_passages)+' dup</span>':'')+
 (s.contradiction_flags?'<span class="pill bad">'+num(s.contradiction_flags)+' contradiction</span>':'')+
 '<span class="pill" title="readability 0..1">read '+pct(s.readability)+'</span>'+
 '<span class="pill" title="ontology coverage contribution">cov '+pct(s.coverage_contribution)+'</span>'+
 (s.gap_hits?'<span class="pill warn">'+num(s.gap_hits)+' gap hit</span>':'')+'</div>'}

function docRow(d){const sc=Number(d.score)||0;const col=sc>=0.7?'var(--good)':sc>=0.4?'var(--warn)':'var(--bad)';
 return '<tr data-id="'+esc(d.document_id)+'">'+
  '<td><b>'+(d.authoritative?'<span class="star" title="authoritative">★ </span>':'')+esc(d.title)+'</b><div class="muted small mono">'+esc(d.uri)+'</div>'+signals(d)+'</td>'+
  '<td><span class="pill">'+esc(d.source)+'</span></td><td>'+num(d.passages)+'</td>'+
  '<td><div class="score">'+bar(sc,col)+'<b>'+sc.toFixed(2)+'</b></div></td>'+
  '<td><span class="pill '+(SUG_CLS[d.suggestion]||'')+'">'+esc(d.suggestion)+'</span><ul class="reasons">'+(d.reasons||[]).map(r=>'<li>'+esc(r)+'</li>').join('')+'</ul></td>'+
  '<td><div class="actions">'+
   '<button class="btn sm good" data-act="keep">Keep</button>'+
   '<button class="btn sm danger" data-act="delete">Delete</button>'+
   '<button class="btn sm" data-act="'+(d.authoritative?'not_authoritative':'authoritative')+'">'+(d.authoritative?'Unmark authoritative':'Mark authoritative')+'</button>'+
   '<button class="btn sm" data-act="history">History</button></div></td></tr>'}

function renderDocs(){const f=$('#doc-filter').value,s=$('#doc-search').value.toLowerCase();
 const rows=DOCS.filter(d=>(!f||d.suggestion===f)&&(!s||(d.title+' '+d.source+' '+d.uri).toLowerCase().includes(s)));
 $('#doc-rows').innerHTML=rows.map(docRow).join('')||'<tr><td colspan="6" class="empty">no documents match</td></tr>';
 $('#doc-shown').textContent=rows.length+' of '+DOCS.length+' shown';$('#doc-count').textContent=DOCS.length+' documents';$('#doc-dataset').textContent='dataset v'+DATASET;
 KF.$$('#doc-rows button').forEach(b=>b.onclick=()=>action(b.closest('tr').dataset.id,b.dataset.act))}

function renderAuthority(ranks){$('#authority-ranks').innerHTML=(ranks||[]).map(r=>'<span class="pill '+(r.rank===1?'good':'')+'" title="weight '+esc(r.weight)+'">'+esc(r.source)+' · rank '+esc(r.rank)+(r.overridden?' *':'')+'</span>').join('')||'<span class="empty">defaults</span>'}

// The full chat context a reader flagged: what they asked, what the AI answered,
// the sources it cited and the turns before it — so a curator can see what
// actually happened, not just the bare question.
function fbDetail(f){const cites=f.citations||[],ctx=f.context||[];
 if(!f.answer&&!cites.length&&!ctx.length&&!f.understood_as)return '';
 let h='<details class="fb-detail"><summary class="muted small">what happened in that chat</summary><div class="fb-body">';
 if(f.understood_as)h+='<div class="small"><b>Understood as:</b> '+esc(f.understood_as)+'</div>';
 if(f.answer)h+='<div class="fb-answer"><b>AI answered'+(f.persona?' ('+esc(f.persona)+')':'')+':</b><div class="small">'+esc(f.answer)+'</div></div>';
 if(cites.length)h+='<div class="fb-cites"><b>Cited:</b><ul class="queue">'+cites.map(c=>'<li><b>'+esc(c.title||'')+'</b> <span class="muted small mono">'+esc(c.where||'')+'</span>'+(c.snippet?'<div class="muted small">'+esc(c.snippet)+'</div>':'')+'</li>').join('')+'</ul></div>';
 if(cites.length===0&&(f.kind==='answer'||f.kind===''))h+='<div class="small muted">No sources were cited for this answer.</div>';
 if(ctx.length)h+='<div class="fb-ctx"><b>Earlier in the chat:</b><ul class="queue">'+ctx.map(t=>'<li><span class="muted small">Q:</span> '+esc(t.q||'')+(t.a?'<div class="muted small">A: '+esc(String(t.a).slice(0,200))+'</div>':'')+'</li>').join('')+'</ul></div>';
 return h+'</div></details>'}
function renderFeedback(rows){const fb=rows||[];
 $('#feedback-rows').innerHTML=fb.length?fb.map(f=>'<tr><td class="mono small">'+esc(ago(f.at))+'</td><td>'+esc(f.subject||'—')+'</td><td>'+esc(f.question||'')+fbDetail(f)+'</td><td>'+(f.level?'<span class="pill">'+esc(f.level)+'</span>':'')+(f.kind&&f.kind!=='answer'?' <span class="pill warn">'+esc(f.kind)+'</span>':'')+'</td><td class="small">'+esc(f.note||'')+'</td></tr>').join('')
  :'<tr><td colspan="5" class="empty">No negative feedback — readers are happy.</td></tr>'}
async function loadFeedback(){try{const d=await api('/curator/feedback');renderFeedback(d.feedback)}catch(e){}}
async function loadAll(){try{
 const [q,g,d]=await Promise.all([api('/curator/quality'),api('/curator/gaps'),api('/curator/documents')]);gate(null);
 renderQuality(q);renderQueues(g);DOCS=d.documents||[];DATASET=d.dataset_version||0;renderDocs();renderAuthority(d.authority);loadFeedback();loadFabricViews();loadGovernance();loadTimeline();loadGraph();loadRegistry()}
 catch(e){gate(e,'curator');if(e.status!==401&&e.status!==403)toast(e.message,'bad')}}

async function decide(doc_id,decision,extra){const doc=DOCS.find(x=>x.document_id===doc_id)||{title:doc_id};
 const reason=extra&&extra.reason!==undefined?extra.reason:(prompt('Reason for "'+decision+'" on '+doc.title+' (optional):','')||'');
 if(reason===null)return null;
 const body=Object.assign({document_id:doc_id,decision,reason},extra||{});
 const out=await api('/curator/decision',{method:'POST',body});toast(decision+' recorded for '+doc.title+' · dataset v'+out.dataset_version,'good');return out}

async function action(doc_id,act){const doc=DOCS.find(x=>x.document_id===doc_id)||{title:doc_id};
 try{
  if(act==='history')return openHistory(doc);
  if(act==='delete'&&!confirm('Delete "'+doc.title+'"? Its passages are removed from retrieval and the dataset version is bumped.'))return;
  await decide(doc_id,act);await loadAll()}
 catch(e){toast(e.message,'bad')}}

async function openHistory(doc){const p=$('#history-panel');p.classList.remove('hidden');$('#history-title').textContent=doc.title;$('#history-doc').textContent=doc.document_id;
 $('#versions-rows').innerHTML='<tr><td colspan="6" class="empty">loading…</td></tr>';
 try{const v=await api('/curator/versions?document_id='+encodeURIComponent(doc.document_id));const hist=v.history||[];const cur=Math.max(0,...hist.map(h=>h.version));
  $('#history-dataset').textContent='v'+v.dataset_version;
  $('#versions-rows').innerHTML=hist.map(h=>'<tr><td><b>v'+esc(h.version)+'</b>'+(h.version===cur?' <span class="pill good">current</span>':'')+'</td><td>'+esc(KF.when(h.created_at))+'</td><td>'+num(h.passages)+'</td><td class="mono">'+esc(h.source_version)+'</td><td class="mono">'+esc(String(h.content_hash||'').slice(0,12))+'…</td>'+
   '<td>'+(h.version!==cur?'<button class="btn sm danger" data-v="'+esc(h.version)+'">Rollback</button>':'')+'</td></tr>').join('')||'<tr><td colspan="6" class="empty">no recorded versions</td></tr>';
  $('#dataset-rows').innerHTML=(v.dataset_versions||[]).map(d=>'<tr><td>v'+esc(d.version)+'</td><td>'+esc(KF.when(d.created_at))+'</td><td>'+esc(d.reason)+'</td><td>'+num(d.doc_count)+'</td><td>'+num(d.passage_count)+'</td></tr>').join('');
  KF.$$('#versions-rows button').forEach(b=>b.onclick=async()=>{const to=+b.dataset.v;if(!confirm('Roll "'+doc.title+'" back to v'+to+'? The current passages are superseded and a new version is recorded.'))return;
   try{const out=await decide(doc.document_id,'rollback',{to_version:to,reason:'rollback to v'+to});if(out&&out.rollback)toast('Rolled back: new version v'+out.rollback.new_version+', '+out.rollback.reactivated+' passage(s) reactivated','good');await openHistory(doc);await loadAll()}catch(e){toast(e.message,'bad')}})}
 catch(e){$('#versions-rows').innerHTML='<tr><td colspan="6" class="empty">'+esc(e.message)+'</td></tr>'}}

async function addDoc(ev){ev.preventDefault();const filename=$('#add-filename').value.trim(),text=$('#add-text').value;if(!filename||!text.trim())return;
 $('#add-btn').disabled=true;$('#add-status').textContent='ingesting…';
 try{const out=await api('/curator/upload',{method:'POST',body:{files:[{filename,text,acl:[$('#add-acl').value]}]}});
  $('#add-status').textContent='uploaded '+out.uploaded+', ingested '+out.ingested+(out.noops?', '+out.noops+' unchanged':'')+' · dataset v'+out.dataset_version;
  toast('Document ingested · dataset v'+out.dataset_version,'good');$('#add-filename').value='';$('#add-text').value='';await loadAll()}
 catch(e){$('#add-status').textContent=e.message;toast(e.message,'bad')}
 finally{$('#add-btn').disabled=false}}

// ---------------------------------------------------------------- T47: repositories / insights / tables
// Everything below reads the fabric-data files through the curator routes
// (facts.json, capabilities.json, dependencies.json, analysis/<repo>/,
// tables/<doc>/<sheet>.sqlite). An empty fabric renders honest empty panels.
let REPOS=[],TABLES=[];
const LANG_COLORS=['#0096FF','#7048E8','#0CA678','#F53E5A','#E8A23A','#3B5BDB','#12B886','#868E96'];
const PERMISSIVE=/^(mit|apache|bsd|isc|psf|unlicense|cc0|zlib|mpl)/i, COPYLEFT=/(gpl|agpl|lgpl|eupl|cddl|ssl|sspl)/i;
function whenStr(s){if(!s)return '—';if(typeof s==='number')return KF.when(s);return String(s).replace('T',' ').replace(/(\.\d+)?(Z|[+-]\d\d:?\d\d)?$/,'').slice(0,16)}
function capPills(cs){return (cs||[]).map(c=>'<span class="pill violet">'+esc(c)+'</span>').join(' ')||'<span class="muted small">none detected</span>'}
function score01(s){if(s==null||s==='')return null;const v=Number(s);if(isNaN(v))return null;return v>1?Math.min(1,v/100):Math.max(0,v)}
function scoreBar(s){const v=score01(s);if(v==null)return '<span class="muted small">—</span>';const col=v>=0.7?'var(--good)':v>=0.4?'var(--warn)':'var(--bad)';return '<div class="score" title="enterprise readiness">'+bar(v,col)+'<b>'+Math.round(v*100)+'</b></div>'}
function licPill(l){const t=String(l||'').trim();if(!t)return '<span class="pill">unknown</span>';const cls=PERMISSIVE.test(t)?'good':COPYLEFT.test(t)?'warn':'';return '<span class="pill '+cls+'">'+esc(t)+'</span>'}
function mdLite(md){if(!md)return '<span class="muted small">not generated yet</span>';return esc(md).replace(/^#{1,6}\s+(.*)$/gm,'<b>$1</b>').replace(/^\s*[-*]\s+/gm,'• ')}
function repoRow(r){return '<tr data-repo="'+esc(r.repo)+'">'+
 '<td><b>'+esc(r.repo)+'</b>'+(r.description?'<div class="muted small">'+esc(String(r.description).slice(0,120))+'</div>':'')+'</td>'+
 '<td>'+(r.primary_language?'<span class="pill info">'+esc(r.primary_language)+'</span>':'<span class="muted">—</span>')+'</td>'+
 '<td>'+num(r.commits)+'</td><td>'+num(r.prs_merged)+'</td><td>'+num(r.contributors_count)+'</td><td>'+num(r.deployments_count)+'</td>'+
 '<td>'+capPills(r.capabilities)+'</td><td>'+scoreBar(r.enterprise_score)+'</td>'+
 '<td class="mono small">'+esc(whenStr(r.pushed_at))+'</td>'+
 '<td><div class="actions"><button class="btn sm" data-act="card"'+(r.has_card||r.has_architecture?'':' title="no analysis card yet — facts only"')+'>card</button>'+
 '<button class="btn sm danger" data-act="delete">Delete</button></div></td></tr>'}
function renderRepos(){$('#repo-count').textContent=REPOS.length+' repositories';
 $('#repo-rows').innerHTML=REPOS.map(repoRow).join('')||'<tr><td colspan="10" class="empty">No repositories analysed yet — facts.json is written by the GitHub analysis workflow.</td></tr>';
 KF.$$('#repo-rows button').forEach(b=>b.onclick=()=>{const repo=b.closest('tr').dataset.repo;b.dataset.act==='card'?openRepo(repo):deleteRepo(repo)})}
async function loadRepos(){try{const d=await api('/curator/repositories');REPOS=Array.isArray(d)?d:(d.repositories||[]);renderRepos()}catch(e){if(e.status!==404)toast(e.message,'bad')}}
function factTile(k,v){return '<div class="f"><div class="k">'+esc(k)+'</div><div class="v">'+v+'</div></div>'}
function langBar(bar){if(!(bar||[]).length)return '<span class="muted small">no language data</span>';
 return '<div class="langbar">'+bar.map((l,i)=>'<i style="width:'+(l.share*100).toFixed(1)+'%;background:'+LANG_COLORS[i%LANG_COLORS.length]+'" title="'+esc(l.name)+' '+(l.share*100).toFixed(1)+'%"></i>').join('')+'</div>'+
  '<div class="legend">'+bar.map((l,i)=>'<span><span class="sw" style="background:'+LANG_COLORS[i%LANG_COLORS.length]+'"></span>'+esc(l.name)+' '+(l.share*100).toFixed(1)+'%</span>').join('')+'</div>'}
function activity(list,empty){if(!(list||[]).length)return '<div class="muted small">'+esc(empty)+'</div>';
 return '<ul class="queue">'+list.map(x=>'<li><b>'+esc(x.title)+'</b> <span class="muted small mono">'+esc(x.uri||'')+'</span>'+(x.snippet?'<div class="muted small">'+esc(x.snippet)+'</div>':'')+'</li>').join('')+'</ul>'}
function renderRepoCard(d){const f=d.facts||{},pr=f.pull_requests||{},dep=f.deployments||{};
 let html='<div class="facts">'+factTile('Commits',num(f.commits))+factTile('PRs merged',num(pr.merged)+' <span class="muted small">/ '+num(pr.total)+'</span>')+
  factTile('Open PRs',num(pr.open))+factTile('Contributors',num(f.contributors_count))+factTile('Deployments',num(dep.count))+
  factTile('Releases',num((f.releases||[]).length))+factTile('Last push','<span class="small mono">'+esc(whenStr(f.pushed_at))+'</span>')+factTile('As of','<span class="small mono">'+esc(whenStr(f.as_of))+'</span>')+'</div>';
 if(f.description)html+='<p class="small" style="margin:10px 0 0">'+esc(f.description)+'</p>';
 html+='<h4>Languages</h4>'+langBar(d.languages_bar);
 html+='<h4>Capabilities</h4>'+((d.capabilities||[]).length?d.capabilities.map(c=>'<div class="cap-group"><span class="pill violet">'+esc(c.capability)+'</span> <span class="muted small">confidence '+pct(c.confidence)+'</span>'+
  '<ul class="evidence">'+(c.evidence||[]).slice(0,6).map(e=>'<li><code>'+esc(e.path)+(e.line!=null?':'+esc(e.line):'')+'</code> '+esc(String(e.snippet||'').slice(0,140))+'</li>').join('')+'</ul></div>').join(''):'<div class="muted small">no capabilities classified</div>');
 html+='<h4>Dependencies <span class="pill">'+num((d.dependencies||[]).length)+'</span></h4>'+((d.dependencies||[]).length?'<div class="tablewrap"><table><thead><tr><th>Name</th><th>Version</th><th>Ecosystem</th><th>Category</th><th>Licence</th></tr></thead><tbody>'+
  d.dependencies.map(x=>'<tr><td><b>'+esc(x.name)+'</b></td><td class="mono small">'+esc(x.version||'')+'</td><td class="small">'+esc(x.ecosystem||'')+'</td><td class="small">'+esc(x.category||'')+'</td><td>'+licPill(x.licence)+'</td></tr>').join('')+'</tbody></table></div>':'<div class="muted small">no manifests parsed</div>');
 html+='<h4>Architecture summary</h4><div class="md">'+mdLite(d.architecture_md)+'</div>';
 if(d.card_md)html+='<h4>Repository card</h4><div class="md">'+mdLite(d.card_md)+'</div>';
 html+='<h4>Contributors</h4>'+((d.contributors||[]).length?'<div class="row">'+d.contributors.slice(0,20).map(c=>'<span class="pill">'+esc(c.login)+' · '+num(c.contributions)+'</span>').join('')+'</div>':'<div class="muted small">—</div>');
 const note=d.note||'no pull-request passages ingested yet';
 html+='<h4>Recent pull requests</h4>'+activity(d.recent_prs,note)+'<h4>Recent commits</h4>'+activity(d.recent_commits,d.note||'no commit passages ingested yet');
 if((f.workflows||[]).length)html+='<h4>Workflows</h4><div class="row">'+f.workflows.map(w=>'<span class="pill '+(w.last_conclusion==='success'?'good':w.last_conclusion==='failure'?'bad':'')+'">'+esc(w.name)+(w.last_conclusion?' · '+esc(w.last_conclusion):'')+'</span>').join('')+'</div>';
 return html}
async function openRepo(repo){const pnl=$('#repo-panel');pnl.classList.remove('hidden');$('#repo-title').textContent=repo;$('#repo-body').innerHTML='<div class="empty">loading…</div>';
 try{const d=await api('/curator/repository?repo='+encodeURIComponent(repo));$('#repo-body').innerHTML=renderRepoCard(d)}
 catch(e){$('#repo-body').innerHTML='<div class="empty">'+esc(e.message)+'</div>'}}
async function deleteRepo(repo){if(!confirm('Delete every ingested document of "'+repo+'"? Its passages leave retrieval and the dataset version is bumped (facts.json is untouched until the next analysis run).'))return;
 try{const out=await api('/curator/repository/delete',{method:'POST',body:{repo}});toast('Removed '+num(out.deleted)+' document(s) of '+repo+' · dataset v'+out.dataset_version,'good');await loadAll()}catch(e){toast(e.message,'bad')}}
function renderInsights(d){const caps=d.capabilities||{},keys=Object.keys(caps).sort();
 const cb=$('#insights-capabilities');cb.className=keys.length?'':'empty';
 cb.innerHTML=keys.length?keys.map(k=>'<div class="cap-group"><span class="pill violet">'+esc(k)+'</span> '+caps[k].map(r=>'<span class="pill" title="confidence '+pct(r.confidence)+'">'+esc(r.repo)+' · '+pct(r.confidence)+'</span>').join(' ')+'</div>').join(''):'No capabilities classified yet.';
 const reuse=d.reuse||[];const rb=$('#insights-reuse');rb.className=reuse.length?'tablewrap':'empty';
 rb.innerHTML=reuse.length?'<table><thead><tr><th>Repository</th><th>Symbol</th><th>Path</th><th>Why</th></tr></thead><tbody>'+reuse.map(r=>'<tr><td>'+esc(r.repo)+'</td><td class="mono small">'+esc(r.symbol)+'</td><td class="mono small">'+esc(r.path)+'</td><td class="small">'+esc(r.why)+'</td></tr>').join('')+'</tbody></table>':'No reuse candidates yet.';
 $('#insights-meta').textContent=keys.length+' capabilities · '+reuse.length+' reuse candidates'}
async function loadInsights(){try{renderInsights(await api('/curator/insights'))}catch(e){if(e.status!==404)toast(e.message,'bad')}}
function renderTables(){$('#tables-count').textContent=TABLES.length+' sheets';const box=$('#tables-list');box.className=TABLES.length?'':'empty';
 box.innerHTML=TABLES.length?TABLES.map(t=>'<div class="sheet"><b>'+esc(t.doc_title)+'</b> <span class="pill info">'+esc(t.sheet)+'</span> <span class="muted small">'+num(t.rows)+' rows'+(t.available===false?' · sqlite not present':'')+'</span>'+
  '<div class="cols">'+(t.columns||[]).map(c=>'<span class="pill" title="'+esc(c.type||'')+'">'+esc(c.name)+(c.type?' <span class="muted">'+esc(c.type)+'</span>':'')+'</span>').join('')+'</div></div>').join(''):'No tables extracted yet — sheets are written under tables/<doc>/<sheet>.sqlite by the document workflow.';
 const sel=$('#tq-sheet');sel.innerHTML=TABLES.map((t,i)=>'<option value="'+i+'">'+esc(t.doc_title+' · '+t.sheet)+'</option>').join('');$('#tq-run').disabled=!TABLES.length}
async function loadTables(){try{const d=await api('/curator/tables');TABLES=Array.isArray(d)?d:(d.tables||[]);renderTables()}catch(e){if(e.status!==404)toast(e.message,'bad')}}
function renderRows(out){const cols=out.columns||[],rows=out.rows||[];if(!rows.length)return '<div class="empty">no rows</div>';
 const names=cols.map(c=>typeof c==='string'?c:c.name);const arr=rows.map(r=>Array.isArray(r)?r:names.map(n=>r[n]));
 return '<table><thead><tr>'+names.map(n=>'<th>'+esc(n)+'</th>').join('')+'</tr></thead><tbody>'+arr.map(r=>'<tr>'+r.map(v=>'<td class="small">'+esc(v==null?'':v)+'</td>').join('')+'</tr>').join('')+'</tbody></table>'}
async function runTableQuery(){const t=TABLES[+$('#tq-sheet').value];if(!t)return;const sql=$('#tq-sql').value.trim()||'SELECT * FROM t LIMIT 20';
 $('#tq-status').textContent='running…';$('#tq-run').disabled=true;
 try{const out=await api('/curator/tables/query',{method:'POST',body:{doc_id:t.doc_id,sheet:t.sheet,sql}});const rows=out.rows||[];
  if(!out.columns&&rows.length&&!Array.isArray(rows[0]))out.columns=Object.keys(rows[0]);
  if(!out.columns)out.columns=(t.columns||[]).map(c=>c.name);
  $('#tq-result').innerHTML=renderRows(out);$('#tq-status').textContent=rows.length+' row(s)'+(out.note?' · '+out.note:'')+(out.truncated?' · truncated':'')}
 catch(e){$('#tq-result').innerHTML='';$('#tq-status').textContent=e.message;toast(e.message,e.status===501?'warn':'bad')}
 finally{$('#tq-run').disabled=false}}
async function loadFabricViews(){await Promise.all([loadRepos(),loadInsights(),loadTables()])}

// ===================== T53 — curation modes + review queue =========
const MODE_LABEL={automated:'Automated',manual:'Manual review'};
// A real two-state switch: both options are shown at once and the active one is
// highlighted, so it reads as a switch rather than a label that flips on click.
function modeToggle(source,mode,label){
 const seg=m=>'<button type="button" class="seg'+(mode===m?' on':'')+'" data-source="'+esc(source)+
  '" data-mode="'+m+'"'+(mode===m?' aria-pressed="true"':'')+'>'+esc(MODE_LABEL[m])+'</button>';
 return '<span class="modeswitch'+(mode==='manual'?' manual':'')+'"><span class="ms-label">'+esc(label)+
  '</span><span class="ms-track">'+seg('automated')+seg('manual')+'</span></span>'}
function renderCuration(m,rq){const box=$('#curation-modes');const def=m.default||'automated';
 let html=modeToggle('*',def,'Default');
 Object.keys(m.sources||{}).sort().forEach(s=>{html+=modeToggle(s,m.sources[s],s)});
 box.className='row';box.style.flexWrap='wrap';box.innerHTML=html;
 KF.$$('#curation-modes .seg').forEach(p=>p.onclick=()=>{if(!p.classList.contains('on'))setMode(p.dataset.source,p.dataset.mode)});
 const items=(rq&&rq.items)||[];$('#review-queue-count').textContent=items.length;
 const rows=$('#review-queue-rows');
 if(!items.length){rows.innerHTML='<tr><td colspan="5" class="empty">Nothing in review — every source is on automated, or all items are decided.</td></tr>';return}
 rows.innerHTML=items.map(it=>{const sc=score01(it.score&&it.score.overall);
  return '<tr data-rid="'+esc(it.review_id)+'"><td>'+esc(it.title||it.document_id||'')+'</td>'+
   '<td class="small">'+esc(it.source||'—')+'</td>'+
   '<td>'+(sc==null?'<span class="muted small">—</span>':scoreBar(it.score.overall))+'</td>'+
   '<td class="small">'+esc(it.recommendation||'—')+'</td>'+
   '<td class="actions"><button class="btn sm primary" data-act="accept">Accept</button>'+
   '<button class="btn sm" data-act="reject">Reject</button></td></tr>'}).join('');
 KF.$$('#review-queue-rows button').forEach(b=>b.onclick=()=>reviewDecision(b.closest('tr').dataset.rid,b.dataset.act))}
async function setMode(source,mode){try{await api('/curator/curation-mode',{method:'POST',body:{source,mode}});
  toast((source==='*'?'Default':source)+' → '+(MODE_LABEL[mode]||mode),'good');loadGovernance()}
 catch(e){toast(e.message,'bad')}}
async function reviewDecision(rid,action){if(action==='reject'&&!confirm('Reject this review item? It stays out of answers.'))return;
 try{await api('/curator/review-decision',{method:'POST',body:{review_id:rid,action}});
  toast('Review '+action+'ed','good');loadGovernance();loadAll()}
 catch(e){toast(e.message,'bad')}}
async function loadGovernance(){try{const [m,rq]=await Promise.all([api('/curator/curation-modes'),api('/curator/review')]);renderCuration(m,rq)}catch(e){if(e.status!==404)toast(e.message,'bad')}}

// ===================== T54 — ingestion timeline ====================
const TL_ACTIONS=[['ingested','var(--qz-blue)'],['accepted','var(--good)'],['rejected','var(--bad)'],['deleted','var(--mut)'],['auto_kept','var(--qz-purple)']];
const MONTHS=['Jan','Feb','Mar','Apr','May','Jun','Jul','Aug','Sep','Oct','Nov','Dec'];
function renderTimeline(t){const sel=$('#timeline-year');const years=t.years||[];
 sel.innerHTML=years.map(y=>'<option value="'+y+'"'+(y===t.year?' selected':'')+'>'+y+'</option>').join('')||('<option>'+t.year+'</option>');
 const months=t.months||[];const max=Math.max(1,...months.map(m=>TL_ACTIONS.reduce((s,a)=>s+(m[a[0]]||0),0)));
 const box=$('#timeline-chart');const total=(t.rows||[]).length;
 if(!total){box.className='empty';box.innerHTML='No curation events recorded in '+esc(t.year)+'.';$('#timeline-meta').textContent='';return}
 box.className='';
 box.innerHTML='<div class="tl-bars">'+months.map((m,i)=>{const tot=TL_ACTIONS.reduce((s,a)=>s+(m[a[0]]||0),0);
   const segs=TL_ACTIONS.filter(a=>m[a[0]]).map(a=>'<i style="height:'+(m[a[0]]/max*100).toFixed(1)+'%;background:'+a[1]+'" title="'+a[0]+' '+m[a[0]]+'"></i>').join('');
   return '<div class="tl-col"><div class="tl-stack">'+segs+'</div><div class="tl-m">'+MONTHS[i]+'</div><div class="tl-n">'+(tot||'')+'</div></div>'}).join('')+'</div>'+
  '<div class="legend" style="margin-top:8px">'+TL_ACTIONS.map(a=>'<span class="k"><span class="sw" style="background:'+a[1]+'"></span>'+a[0].replace('_',' ')+'</span>').join('')+'</div>';
 $('#timeline-meta').textContent=total+' event(s) in '+t.year}
async function loadTimeline(year){try{const t=await api('/curator/timeline'+(year?'?year='+year:''));renderTimeline(t);
  $('#timeline-year').onchange=e=>loadTimeline(e.target.value)}
 catch(e){if(e.status!==404)toast(e.message,'bad')}}

// ===================== T57 — knowledge graph insights ==============
function renderGraph(g){g=g||{};
 let comm=g.communities||[];if(!Array.isArray(comm))comm=Object.values(comm);
 const named=comm.filter(c=>c&&(c.size||0)>1).sort((a,b)=>(b.size||0)-(a.size||0)).slice(0,8);
 $('#graph-comm-count').textContent=comm.length;
 const cb=$('#graph-communities');cb.className=named.length?'':'empty';
 cb.innerHTML=named.length?named.map(c=>'<div style="margin:3px 0"><span class="pill '+((c.flag)?'warn':'')+'" title="cohesion '+pct(c.cohesion)+'">'+
   esc((c.labels&&c.labels[0])||('community '+c.community))+'</span> <span class="muted small">'+(c.size||0)+' concepts &middot; cohesion '+pct(c.cohesion)+(c.flag?' &middot; '+esc(c.flag):'')+'</span></div>').join(''):'No sizeable communities yet.';
 const surp=g.surprising||[];$('#graph-surp-count').textContent=surp.length;
 const sb=$('#graph-surprising');sb.className=surp.length?'':'empty';
 sb.innerHTML=surp.length?surp.slice(0,8).map(s=>'<div class="small" style="margin:3px 0">'+esc(s.a_label||s.a)+' <span class="muted">&harr;</span> '+esc(s.b_label||s.b)+' <span class="muted small">('+pct(s.score||s.adamic_adar||0)+')</span></div>').join(''):'No surprising cross-domain links.';
 const gaps=g.gaps||[];$('#graph-gaps-count').textContent=gaps.length;
 const gb=$('#graph-gaps');gb.className=gaps.length?'':'empty';
 gb.innerHTML=gaps.length?gaps.slice(0,8).map(x=>'<div class="small" style="margin:3px 0"><span class="pill warn">'+esc(x.kind||'gap')+'</span> '+esc(x.label||'')+
   ((x.suggest_tags||[]).length?' <span class="muted small">tag: '+esc(x.suggest_tags.join(', '))+'</span>':'')+'</div>').join(''):'No knowledge gaps detected.'}
async function loadGraph(){try{const d=await api('/curator/insights');renderGraph(d.graph)}catch(e){/* graph is best-effort */}}

// ===================== T82 — known-question registry ==============
function renderRegistry(d){const entries=(d&&d.entries)||[];$('#registry-count').textContent=entries.length+' questions';
 const rows=$('#registry-rows');
 rows.innerHTML=entries.length?entries.map(e=>{const on=e.enabled!==false;
  return '<tr'+(on?'':' class="muted"')+'><td>'+esc(e.pattern||'')+'<div class="muted small">'+esc((e.examples||[])[0]||'')+'</div></td>'+
   '<td>'+(e.persona||[]).map(x=>'<span class="pill">'+esc(x)+'</span>').join(' ')+'</td>'+
   '<td>'+esc(e.answer_kind||'')+'</td><td class="mono small">'+esc(e.source||'')+'</td>'+
   '<td>'+esc(e.freshness_target_s!=null?e.freshness_target_s+'s':'')+'</td>'+
   '<td>'+(on?'<span class="pill good">enabled</span>':'<span class="pill">disabled</span>')+'</td>'+
   '<td><button class="btn sm" data-id="'+esc(e.id)+'" data-act="'+(on?'disable':'enable')+'">'+(on?'Disable':'Enable')+'</button></td></tr>'}).join('')
  :'<tr><td colspan="7" class="empty">No known questions yet.</td></tr>';
 KF.$$('#registry-rows button').forEach(b=>b.onclick=()=>toggleRegistry(b.dataset.id,b.dataset.act))}
async function loadRegistry(){try{renderRegistry(await api('/curator/registry'))}catch(e){if(e.status!==404)toast(e.message,'bad')}}
async function toggleRegistry(id,action){try{await api('/curator/registry',{method:'POST',body:{action,id}});toast('Known question '+action+'d','good');await loadRegistry()}catch(e){toast(e.message,'bad')}}
async function addKnown(ev){ev.preventDefault();
 const entry={id:$('#reg-id').value.trim(),pattern:$('#reg-pattern').value.trim(),
  persona:$('#reg-personas').value.split(',').map(s=>s.trim()).filter(Boolean),
  answer_kind:$('#reg-kind').value,source:$('#reg-source').value.trim()||'corpus',
  examples:$('#reg-examples').value.split(',').map(s=>s.trim()).filter(Boolean),
  freshness_target_s:Number($('#reg-fresh').value)||3};
 if(!entry.id||!entry.pattern){toast('id and pattern are required','bad');return}
 $('#reg-add-btn').disabled=true;$('#reg-status').textContent='saving…';
 try{const out=await api('/curator/registry',{method:'POST',body:{action:'upsert',entry}});
  $('#reg-status').textContent='saved '+((out.entry&&out.entry.id)||entry.id);toast('Known question saved','good');
  $('#reg-id').value='';$('#reg-pattern').value='';$('#reg-personas').value='';$('#reg-examples').value='';await loadRegistry()}
 catch(e){$('#reg-status').textContent=e.message;toast(e.message,'bad')}
 finally{$('#reg-add-btn').disabled=false}}

window.KF_ON_SESSION=s=>{if(s)loadAll();else{DOCS=[];renderDocs();gate({status:401,message:''},'curator')}};
KF.initBar({preferRole:'curator'});
$('#doc-filter').addEventListener('change',renderDocs);$('#doc-search').addEventListener('input',renderDocs);
$('#doc-refresh').onclick=loadAll;$('#history-close').onclick=()=>$('#history-panel').classList.add('hidden');
$('#add-doc-form').addEventListener('submit',addDoc);
$('#repo-refresh').onclick=loadFabricViews;$('#repo-close').onclick=()=>$('#repo-panel').classList.add('hidden');$('#tq-run').onclick=runTableQuery;
$('#registry-form').addEventListener('submit',addKnown);
if(KF.session)loadAll();else gate({status:401,message:''},'curator');
