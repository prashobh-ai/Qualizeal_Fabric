"""Curator console (Section G) — served at ``/curator``.

The curator's job is *relevance and trust*, not plumbing. The page therefore
shows only knowledge-base evaluation and per-document decisions:

* ``GET /curator/quality``   → data-quality KPI tiles + risk register;
* ``GET /curator/gaps``      → open gaps, contradictions and the low-confidence review queue;
* ``GET /curator/documents`` → the document table: score bar, suggestion badge
  (keep / review / delete) with the transparent reasons behind it, the signals
  (citations, age, duplicates, readability…), authoritative flag, and the
  actions Keep · Delete · Mark authoritative / Unmark · History;
* ``GET /curator/versions?document_id=`` → History drawer with the version
  list, a Rollback button per non-current version and the dataset versions;
* ``POST /curator/decision`` → keep | delete | authoritative | not_authoritative | rollback;
* ``POST /curator/upload``   → the single "Add document" form.

Deliberately absent: connector cards, permissions, bulk upload/delete,
budgets, users — those belong to the Admin console (``admin_ui.py``).
Zero external dependencies: inline CSS/JS/SVG only.
"""
from __future__ import annotations

from .ui_common import card, shell

__all__ = ["CURATOR_HTML"]

_CSS = r"""
.score{display:flex;align-items:center;gap:8px;min-width:120px}.score b{width:36px;text-align:right}
.reasons{font-size:12px;color:var(--mut)}
.reasons li{margin:0}
.signals{display:flex;gap:4px;flex-wrap:wrap;margin-top:4px}
.signals .pill{font-weight:500}
.actions{display:flex;gap:4px;flex-wrap:wrap}
.star{color:var(--warn);font-weight:700}
.risk-high{color:var(--bad)}.risk-medium{color:var(--warn)}.risk-low{color:var(--good)}
.filterbar{display:flex;gap:8px;align-items:center;flex-wrap:wrap;margin-bottom:10px}
.filterbar input{min-width:220px}
.queue li{margin:3px 0;font-size:13px}
"""

_QUALITY = card("Data quality", '<div class="grid kpis" id="quality-tiles"><div class="empty">Sign in as a curator to load the knowledge-base evaluation.</div></div>',
                "quality-card", right='<span class="muted small" id="quality-meta"></span>')

_RISK = card("Risk register", '<div id="risk-register" class="empty">—</div>', "risk-card")

_QUEUES = card("Curation queues",
               '<div class="grid" style="grid-template-columns:repeat(auto-fit,minmax(220px,1fr))">'
               '<div><b>Gaps</b> <span class="pill bad" id="gaps-count">0</span><ul class="queue" id="gaps-list"></ul></div>'
               '<div><b>Contradictions</b> <span class="pill warn" id="contradictions-count">0</span><ul class="queue" id="contradictions-list"></ul></div>'
               '<div><b>Low-confidence review</b> <span class="pill info" id="review-count">0</span><ul class="queue" id="review-list"></ul></div>'
               '</div>', "queues-card")

_DOCS = """
<div class="section-title">Documents <span class="pill" id="doc-count"></span><span class="pill info" id="doc-dataset"></span>
  <button class="btn sm" id="doc-refresh" style="margin-left:auto">Refresh</button></div>
<div class="card" id="documents-card">
  <div class="filterbar">
    <label class="muted small">Show</label>
    <select id="doc-filter"><option value="">all suggestions</option><option value="keep">keep</option><option value="review">review</option><option value="delete">delete</option></select>
    <input id="doc-search" placeholder="filter by title, source or uri">
    <span class="muted small" id="doc-shown"></span>
  </div>
  <div class="tablewrap"><table id="doc-table">
    <thead><tr><th>Document</th><th>Source</th><th>Passages</th><th>Score</th><th>Suggestion &amp; reasons</th><th>Actions</th></tr></thead>
    <tbody id="doc-rows"><tr><td colspan="6" class="empty">—</td></tr></tbody>
  </table></div>
</div>
"""

_ADD = card("Add a document",
            '<form id="add-doc-form" class="col">'
            '<div class="row"><input id="add-filename" placeholder="filename, e.g. qa/onboarding.md" style="flex:1" required>'
            '<select id="add-acl"><option value="public">public</option><option value="restricted">restricted</option></select></div>'
            '<textarea id="add-text" placeholder="Paste the document text (markdown, csv, transcript…)" required></textarea>'
            '<div class="row"><button class="btn primary" id="add-btn" type="submit">Ingest document</button>'
            '<span class="muted small" id="add-status">Runs the full 7-step pipeline and bumps the dataset version.</span></div>'
            '</form>', "add-card")

_AUTHORITY = card("Source authority ranks",
                  '<div class="muted small">Rank 1 is most authoritative; the answer path boosts cited passages by this weight (read-only here — admins change ranks).</div>'
                  '<div id="authority-ranks" class="row" style="margin-top:8px"></div>', "authority-card")

_DRAWER = """
<div class="drawer hidden" id="history-panel">
  <div class="row"><h3 style="margin:0">History <span class="muted small" id="history-title"></span></h3>
    <button class="btn sm" id="history-close" style="margin-left:auto">Close</button></div>
  <div class="muted small mono" id="history-doc"></div>
  <div class="tablewrap" style="margin-top:10px"><table id="versions-table">
    <thead><tr><th>Version</th><th>Created</th><th>Passages</th><th>Source version</th><th>Content hash</th><th></th></tr></thead>
    <tbody id="versions-rows"></tbody></table></div>
  <div class="section-title" style="margin-top:18px">Dataset versions <span class="pill info" id="history-dataset"></span></div>
  <div class="tablewrap"><table><thead><tr><th>v</th><th>Created</th><th>Reason</th><th>Docs</th><th>Passages</th></tr></thead>
    <tbody id="dataset-rows"></tbody></table></div>
</div>
"""

_FEEDBACK = card("User feedback to check",
                 '<div class="muted small">Answers readers flagged as unhelpful (&#128078;). Each one is a candidate '
                 'gap, a wrong route, or a document to fix.</div>'
                 '<div class="tablewrap" style="margin-top:8px"><table id="feedback-table">'
                 '<thead><tr><th>When</th><th>Reader</th><th>Question</th><th>Level</th><th>Note</th></tr></thead>'
                 '<tbody id="feedback-rows"><tr><td colspan="5" class="empty">No negative feedback — readers are happy.</td></tr></tbody>'
                 '</table></div>', "feedback-card")

_BODY = (f'<div class="grid" style="grid-template-columns:2fr 1fr">{_QUALITY}{_RISK}</div>'
         f'<div style="margin-top:14px">{_QUEUES}</div>'
         f'<div style="margin-top:14px">{_FEEDBACK}</div>'
         f'{_DOCS}'
         f'<div class="grid two" style="margin-top:14px">{_ADD}{_AUTHORITY}</div>'
         f'{_DRAWER}')

_JS = r"""
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

function renderFeedback(rows){const fb=rows||[];
 $('#feedback-rows').innerHTML=fb.length?fb.map(f=>'<tr><td class="mono small">'+esc(ago(f.at))+'</td><td>'+esc(f.subject||'—')+'</td><td>'+esc(f.question||'')+'</td><td>'+(f.level?'<span class="pill">'+esc(f.level)+'</span>':'')+'</td><td class="small">'+esc(f.note||'')+'</td></tr>').join('')
  :'<tr><td colspan="5" class="empty">No negative feedback — readers are happy.</td></tr>'}
async function loadFeedback(){try{const d=await api('/curator/feedback');renderFeedback(d.feedback)}catch(e){}}
async function loadAll(){try{
 const [q,g,d]=await Promise.all([api('/curator/quality'),api('/curator/gaps'),api('/curator/documents')]);gate(null);
 renderQuality(q);renderQueues(g);DOCS=d.documents||[];DATASET=d.dataset_version||0;renderDocs();renderAuthority(d.authority);loadFeedback()}
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

window.KF_ON_SESSION=s=>{if(s)loadAll();else{DOCS=[];renderDocs();gate({status:401,message:''},'curator')}};
KF.initBar({preferRole:'curator'});
$('#doc-filter').addEventListener('change',renderDocs);$('#doc-search').addEventListener('input',renderDocs);
$('#doc-refresh').onclick=loadAll;$('#history-close').onclick=()=>$('#history-panel').classList.add('hidden');
$('#add-doc-form').addEventListener('submit',addDoc);
if(KF.session)loadAll();else gate({status:401,message:''},'curator');
"""

CURATOR_HTML = shell("Curator", "", _BODY, _JS, "Curator", _CSS)
