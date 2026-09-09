"""Ask console (Section G) — served at ``/`` and ``/ask``.

One governed path for everyone: the page signs in through ``POST /login``
(tenant + demo subject), then sends ``POST /ask {question}`` with the bearer
token and renders the complete ``Answer`` dict (``contracts/types.py``) as an
*answer card*:

* the answer text with clickable ``[n]`` citation markers, kind badge
  (answer / clarify / gap) and the clarify-back question when present;
* a confidence meter plus the grounding score;
* citations — click a citation to expand its snippet and passage id;
* the why-card (selector level, tier, explanation, reason codes);
* MODEL USED, TOKENS IN / OUT, cost, cache hit + cost saved, language,
  complexity badge, dataset version, trajectory id;
* the authoritative-source badge with its reason and the conflicts the
  authority layer detected between cited sources;
* a collapsible "Reasoning steps" timeline whenever ``reasoning`` is non-null
  (multistep / conditional / compare plans with per-step grounding and
  condition outcome).

Zero external dependencies: inline CSS/JS/SVG only (``ui_common.shell``).
"""
from __future__ import annotations

from .ui_common import shell

__all__ = ["ASK_HTML"]

_CSS = r"""
.askbox{display:flex;gap:10px;align-items:flex-start}
.askbox textarea{min-height:64px;font-size:15px}
.samples{display:flex;gap:6px;flex-wrap:wrap;margin-top:10px}
.chip{background:#182449;border:1px solid var(--line);border-radius:20px;padding:3px 10px;font-size:12px;cursor:pointer;color:var(--fg)}
.chip:hover{border-color:var(--accent)}
.answer-text{font-size:16px;line-height:1.65;margin:8px 0 4px}
.answer-text sup.ref{color:var(--accent);cursor:pointer;font-weight:700;margin-left:2px}
.answer-text sup.ref:hover{text-decoration:underline}
.meter{display:flex;align-items:center;gap:12px}
.meter svg{flex:1;height:14px}
.cite{border:1px solid var(--line);border-radius:10px;padding:8px 10px;margin:6px 0;cursor:pointer;background:var(--panel2)}
.cite:hover{border-color:var(--accent)}
.cite .n{display:inline-block;min-width:22px;color:var(--accent);font-weight:700}
.cite .snippet{margin-top:8px;padding:8px 10px;background:#0a0f26;border-radius:8px;font-size:13px;white-space:pre-wrap}
.cite.hit{border-color:var(--warn)}
.facts{display:grid;grid-template-columns:repeat(auto-fit,minmax(140px,1fr));gap:10px}
.fact{background:var(--panel2);border:1px solid var(--line);border-radius:10px;padding:8px 10px}
.fact .l{color:var(--mut);font-size:11px;text-transform:uppercase;letter-spacing:.4px}
.fact .v{font-size:15px;font-weight:700;margin-top:2px;word-break:break-word}
.timeline{list-style:none;margin:10px 0 0;padding:0 0 0 18px;border-left:2px solid var(--line)}
.timeline li{position:relative;margin:0 0 14px;padding-left:12px}
.timeline li:before{content:"";position:absolute;left:-25px;top:6px;width:10px;height:10px;border-radius:50%;background:var(--accent);border:2px solid var(--panel)}
.timeline li.skipped:before{background:var(--mut)}.timeline li.cond-true:before{background:var(--good)}.timeline li.cond-false:before{background:var(--bad)}
.timeline .q{font-weight:600}.timeline .a{font-size:13px;color:var(--fg);margin-top:4px;white-space:pre-wrap}
.auth{display:flex;gap:10px;align-items:flex-start}
.auth svg{flex:none}
.conflict{border-left:3px solid var(--warn);padding:4px 10px;margin:6px 0;font-size:12px;background:rgba(240,180,41,.06);border-radius:0 8px 8px 0}
.hist{cursor:pointer;padding:6px 8px;border-radius:8px;border:1px solid transparent}
.hist:hover{border-color:var(--line);background:var(--panel2)}
"""

_BODY = """
<div class="card" id="ask-card">
  <h3>Ask the knowledge fabric <span class="right muted" id="ask-status"></span></h3>
  <form class="askbox" id="ask-form">
    <textarea id="question" placeholder="Ask a question… e.g. what must a release achieve before promotion? Try 'and' for multistep, 'if … then …' for conditional, 'compare A and B'."></textarea>
    <button class="btn primary" id="ask-btn" type="submit">Ask</button>
  </form>
  <div class="samples" id="samples"></div>
</div>

<div id="answer" class="hidden">
  <div class="card" id="answer-card" style="margin-top:14px">
    <h3>Answer
      <span class="right row" id="answer-badges"></span>
    </h3>
    <div class="answer-text" id="answer-text"></div>
    <div id="clarify-back" class="hidden gate"></div>
    <div class="meter" style="margin-top:12px">
      <span class="muted small" style="width:78px">Confidence</span>
      <div style="flex:1" id="confidence-meter"></div>
      <b id="confidence-value" style="width:52px;text-align:right"></b>
      <span class="muted small" id="grounding-value"></span>
    </div>
  </div>

  <div class="grid two" style="margin-top:14px">
    <div class="card" id="citations-card">
      <h3>Citations <span class="right muted" id="citations-count"></span></h3>
      <div id="citations"></div>
    </div>
    <div class="card" id="why-card">
      <h3>Why this model <span class="right" id="why-level"></span></h3>
      <div id="why-explain" class="small"></div>
      <div class="row" id="why-reasons" style="margin-top:8px"></div>
    </div>
  </div>

  <div class="card" style="margin-top:14px" id="facts-card">
    <h3>Run facts</h3>
    <div class="facts">
      <div class="fact"><div class="l">Model used</div><div class="v mono" id="model-used"></div></div>
      <div class="fact"><div class="l">Tokens in</div><div class="v" id="tokens-in"></div></div>
      <div class="fact"><div class="l">Tokens out</div><div class="v" id="tokens-out"></div></div>
      <div class="fact"><div class="l">Cost</div><div class="v mono" id="cost"></div></div>
      <div class="fact"><div class="l">Cache</div><div class="v" id="cache-hit"></div></div>
      <div class="fact"><div class="l">Cost saved</div><div class="v mono" id="cost-saved"></div></div>
      <div class="fact"><div class="l">Complexity</div><div class="v" id="complexity"></div></div>
      <div class="fact"><div class="l">Language</div><div class="v" id="language"></div></div>
      <div class="fact"><div class="l">Tier / level</div><div class="v" id="tier-level"></div></div>
      <div class="fact"><div class="l">Dataset version</div><div class="v" id="dataset-version"></div></div>
      <div class="fact"><div class="l">Trajectory</div><div class="v mono small" id="trajectory-id"></div></div>
    </div>
  </div>

  <div class="grid two" style="margin-top:14px">
    <div class="card" id="authority-card">
      <h3>Authoritative source</h3>
      <div id="authoritative-source"></div>
      <div id="conflicts"></div>
    </div>
    <div class="card" id="reasoning-card">
      <details id="reasoning" open>
        <summary>Reasoning steps <span class="pill" id="reasoning-mode"></span></summary>
        <div class="small muted" id="reasoning-explain" style="margin-top:6px"></div>
        <ol class="timeline" id="reasoning-steps"></ol>
      </details>
      <div class="empty" id="reasoning-empty">Single-step answer — no reasoning plan was needed.</div>
    </div>
  </div>
</div>

<div class="card" style="margin-top:14px" id="history-card">
  <h3>This session <span class="right muted small">click to re-open an answer</span></h3>
  <div id="history" class="empty">No questions asked yet.</div>
</div>
"""

_JS = r"""
const {$,esc,num,pct,money,toast,gate,api}=KF;
const HISTORY=[];
const SAMPLE_EXTRA=[
 'what must a release achieve before promotion and which requirement has a traceability gap?',
 'if a component has an open defect, then what happens to its dependent releases?',
 'compare the test strategy and the release runbook'];
const KIND_CLS={answer:'good',clarify:'warn',gap:'bad'};
const CPLX_CLS={simple:'good',medium:'warn',complex:'violet'};

function samples(){const t=(KF.session&&KF.session.tenant)||$('#kf-tenant').value;
 const qs=(KF.DIR.questions[t]||[]).concat(t==='q-quality'?SAMPLE_EXTRA:[]);
 $('#samples').innerHTML=qs.map(q=>'<span class="chip" data-q="'+esc(q)+'">'+esc(q)+'</span>').join('');
 KF.$$('#samples .chip').forEach(c=>c.onclick=()=>{$('#question').value=c.dataset.q;$('#question').focus()})}

function markers(text){return esc(text).replace(/\[(\d+)\]/g,(m,n)=>'<sup class="ref" data-n="'+n+'">['+n+']</sup>')}

function meter(conf){const v=Math.max(0,Math.min(1,Number(conf)||0));const col=v>=0.7?'var(--good)':v>=0.4?'var(--warn)':'var(--bad)';
 return '<svg viewBox="0 0 300 14" preserveAspectRatio="none" width="100%" height="14">'+
  '<rect x="0" y="2" width="300" height="10" rx="5" fill="#182449"/>'+
  '<rect x="0" y="2" width="'+(v*300).toFixed(1)+'" height="10" rx="5" fill="'+col+'"/>'+
  '<line x1="120" y1="0" x2="120" y2="14" stroke="var(--mut)" stroke-dasharray="2 2"/><line x1="210" y1="0" x2="210" y2="14" stroke="var(--mut)" stroke-dasharray="2 2"/></svg>'}

function citation(c,i){return '<div class="cite" data-i="'+i+'" id="cite-'+(i+1)+'"><span class="n">['+(i+1)+']</span>'+
 '<b>'+esc(c.document_title)+'</b> <span class="muted small mono">'+esc(c.coordinate_render)+'</span>'+
 '<div class="snippet hidden">'+esc(c.snippet)+'<div class="muted small mono" style="margin-top:6px">passage '+esc(c.passage_id)+' · doc '+esc(c.document_id)+'</div></div></div>'}

function whyCard(a){const w=a.why||{};
 $('#why-level').innerHTML='<span class="pill accent">L'+esc(a.level)+' · '+esc(w.level_name||'—')+'</span> <span class="pill">'+esc(a.tier||w.tier||'')+'</span>';
 $('#why-explain').textContent=w.explain||'No selector explanation was recorded.';
 $('#why-reasons').innerHTML=(w.reasons||[]).map(r=>'<span class="pill info" title="'+esc(r.detail||'')+'">'+esc(r.code)+(r.signal!=null?' · '+esc(r.signal):'')+'</span>').join('')||'<span class="empty">no reason codes</span>'}

function facts(a){$('#model-used').textContent=a.model_name||'—';$('#tokens-in').textContent=num(a.tokens_in);$('#tokens-out').textContent=num(a.tokens_out);
 $('#cost').textContent=money(a.cost);$('#cost-saved').textContent=money(a.cost_saved);
 $('#cache-hit').innerHTML=a.cache_hit?'<span class="pill good">HIT</span>':'<span class="pill">miss</span>';
 $('#complexity').innerHTML='<span class="pill '+(CPLX_CLS[a.complexity]||'')+'">'+esc(a.complexity||'n/a')+'</span>';
 $('#language').innerHTML='<span class="pill">'+esc((a.lang||'en').toUpperCase())+'</span>';
 $('#tier-level').textContent=(a.tier||'—')+' / L'+(a.level||0);
 $('#dataset-version').textContent='v'+(a.dataset_version||0);$('#trajectory-id').textContent=a.trajectory_id||'—'}

function shield(){return '<svg width="28" height="28" viewBox="0 0 24 24" aria-hidden="true"><path d="M12 2l8 3v6c0 5-3.4 9.4-8 11-4.6-1.6-8-6-8-11V5l8-3z" fill="#3ecf8e"/><path d="M8.5 12.2l2.4 2.4 4.8-5" fill="none" stroke="#0b1020" stroke-width="2" stroke-linecap="round"/></svg>'}

function authority(a){const s=a.authoritative_source;const titles={};(a.citations||[]).forEach(c=>titles[c.document_id]=c.document_title);
 const name=id=>titles[id]||id;
 if(!s){$('#authoritative-source').innerHTML='<div class="empty">No authoritative source determined for this answer.</div>';$('#conflicts').innerHTML='';return}
 $('#authoritative-source').innerHTML='<div class="auth">'+shield()+'<div><span class="pill good">AUTHORITATIVE</span> <b>'+esc(s.document_title)+'</b> '+
  '<span class="pill">'+esc(s.source)+'</span><div class="small muted" style="margin-top:4px">'+esc(s.reason||'')+'</div></div></div>';
 const cf=s.conflicts||[];
 $('#conflicts').innerHTML=cf.length?'<div class="small muted" style="margin-top:10px">'+cf.length+' source conflict(s) resolved by authority rank</div>'+
  cf.map(c=>'<div class="conflict"><b>'+esc(name(c.a))+'</b> vs <b>'+esc(name(c.b))+'</b> → preferred <b>'+esc(name(c.preferred))+'</b><div class="muted">'+esc(c.reason)+'</div></div>').join('')
  :'<div class="empty" style="margin-top:8px">No conflicts between cited sources.</div>'}

function reasoning(a){const r=a.reasoning;const card=$('#reasoning'),empty=$('#reasoning-empty');
 if(!r||!(r.steps||[]).length){card.classList.add('hidden');empty.classList.remove('hidden');return}
 card.classList.remove('hidden');empty.classList.add('hidden');
 $('#reasoning-mode').textContent=r.mode||'multistep';$('#reasoning-explain').textContent=r.explain||'';
 $('#reasoning-steps').innerHTML=(r.steps||[]).map(s=>{const cls=s.skipped?'skipped':s.condition===true?'cond-true':s.condition===false?'cond-false':'';
  const cond=s.condition==null?'':' <span class="pill '+(s.condition?'good':'bad')+'">condition '+(s.condition?'TRUE':'FALSE')+'</span>';
  return '<li class="'+cls+'"><span class="pill accent">'+esc(s.id)+'</span> <span class="pill">'+esc(s.kind)+'</span>'+cond+
   (s.skipped?' <span class="pill">skipped</span>':'')+' <span class="muted small">grounding '+pct(s.grounding)+'</span>'+
   '<div class="q">'+esc(s.question)+'</div>'+(s.skipped?'<div class="muted small">'+esc(s.reason||'branch not taken')+'</div>':'<div class="a">'+markers(s.answer_text||'')+'</div>')+'</li>'}).join('')}

function render(a){$('#answer').classList.remove('hidden');
 $('#answer-badges').innerHTML='<span class="pill '+(KIND_CLS[a.kind]||'')+'">'+esc(a.kind)+'</span>'+
  '<span class="pill '+(CPLX_CLS[a.complexity]||'')+'">'+esc(a.complexity||'n/a')+'</span><span class="pill">'+esc((a.lang||'en').toUpperCase())+'</span>'+
  (a.cache_hit?'<span class="pill good">cache hit · saved '+esc(money(a.cost_saved))+'</span>':'')+
  '<span class="pill info">dataset v'+esc(a.dataset_version||0)+'</span>';
 $('#answer-text').innerHTML=markers(a.answer_text||'');
 const cb=$('#clarify-back');if(a.clarify_back){cb.classList.remove('hidden');cb.innerHTML='<h4>Clarification needed</h4>'+esc(a.clarify_back)}else{cb.classList.add('hidden');cb.innerHTML=''}
 $('#confidence-meter').innerHTML=meter(a.confidence);$('#confidence-value').textContent=pct(a.confidence);
 $('#grounding-value').textContent='grounding '+pct(a.grounding_score);
 const cs=a.citations||[];$('#citations-count').textContent=cs.length+' passage(s)';
 $('#citations').innerHTML=cs.map(citation).join('')||'<div class="empty">No citations — the answer is not grounded in a passage.</div>';
 KF.$$('#citations .cite').forEach(el=>el.onclick=()=>el.querySelector('.snippet').classList.toggle('hidden'));
 KF.$$('#answer sup.ref').forEach(el=>el.onclick=()=>{const c=$('#cite-'+el.dataset.n);if(!c)return;c.querySelector('.snippet').classList.remove('hidden');
  KF.$$('.cite').forEach(x=>x.classList.remove('hit'));c.classList.add('hit');c.scrollIntoView({behavior:'smooth',block:'center'})});
 whyCard(a);facts(a);authority(a);reasoning(a)}

function history(){if(!HISTORY.length)return;
 $('#history').className='';$('#history').innerHTML=HISTORY.map((h,i)=>'<div class="hist" data-i="'+i+'"><span class="pill '+(KIND_CLS[h.a.kind]||'')+'">'+esc(h.a.kind)+'</span> '+esc(h.q)+
  ' <span class="muted small">· '+esc(h.a.model_name)+' · '+pct(h.a.confidence)+(h.a.cache_hit?' · cached':'')+'</span></div>').join('');
 KF.$$('#history .hist').forEach(el=>el.onclick=()=>render(HISTORY[+el.dataset.i].a))}

async function ask(ev){if(ev)ev.preventDefault();const q=$('#question').value.trim();if(!q)return;
 if(!KF.session){gate({status:401,message:''},'asker');return}
 const btn=$('#ask-btn');btn.disabled=true;$('#ask-status').textContent='thinking…';const t0=performance.now();
 try{const a=await api('/ask',{method:'POST',body:{question:q}});gate(null);
  $('#ask-status').textContent=Math.round(performance.now()-t0)+' ms · '+(a.kind||'')+' · '+(a.model_name||'');
  HISTORY.unshift({q,a});if(HISTORY.length>10)HISTORY.pop();render(a);history()}
 catch(e){gate(e,'asker');$('#ask-status').textContent='failed';toast(e.message,'bad')}
 finally{btn.disabled=false}}

window.KF_ON_SESSION=s=>{gate(null);samples();if(s)$('#ask-status').textContent='ready for '+s.subject};
KF.initBar({preferRole:'asker'});samples();
$('#ask-form').addEventListener('submit',ask);
$('#question').addEventListener('keydown',e=>{if((e.ctrlKey||e.metaKey)&&e.key==='Enter')ask(e)});
if(!KF.session)gate({status:401,message:''},'asker');
"""

ASK_HTML = shell("Knowledge Fabric · Ask",
                 "Ask · one governed path · every answer cited, costed and explained",
                 _BODY, _JS, "Ask", _CSS)
