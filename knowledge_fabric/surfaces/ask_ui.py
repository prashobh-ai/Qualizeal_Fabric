"""Workspace (L2) — the signed-in chat console, served at ``/`` and ``/ask``.

A three-column product surface (F3, made concrete):

* **Threads** (left, 300) — the reader's conversations, kept per-session; a
  new chat, click to re-open, the first question names the thread.
* **Conversation** (centre) — a slim corpus strip (numbers, labels under), the
  message stream (question bubble + grounded answer with inline citation chips
  that open the page viewer; declines read as a plain sentence), and the
  composer (mic placeholder until voice, answer-language pill, read-aloud
  toggle, Send).
* **Right rail** (380) — the compact answer galaxy (activation from the
  retrieved passages) with a "Why did the AI say this?" Explain overlay; **the
  card** (one row per fact — Answered by, Why, Model, Moved levels, then the
  rest under Details); and **My usage** at the bottom (questions · answered ·
  declined, tokens, cost, the level split for today / 7 d / 30 d, and the
  budget bar).

The reader only ever sees the level as a word (L1.5). Zero external
dependencies: inline CSS/JS/SVG only (``ui_common.shell``).
"""
from __future__ import annotations

from .ui_common import shell

__all__ = ["ASK_HTML"]

_CSS = r"""
/* the Workspace is full-bleed: break out of the centred content column. */
main{max-width:none;padding:0;display:flex;flex-direction:column}
#kf-gate:not(.hidden){margin:16px 24px}
.ws{flex:1;min-height:0;display:grid;grid-template-columns:300px minmax(0,1fr) 380px}
.ws>*{min-height:0}
@media(max-width:1180px){.ws{grid-template-columns:0 minmax(0,1fr) 340px}.threads{display:none}}
@media(max-width:900px){.ws{grid-template-columns:minmax(0,1fr)}.rail{display:none}}

/* ---- threads (left) ---- */
.threads{border-right:1px solid var(--line);padding:12px 10px;display:flex;flex-direction:column;gap:6px;overflow:auto;background:var(--surface)}
.threads .new{margin-bottom:6px}
.thread{padding:8px 10px;border-radius:9px;border:1px solid transparent;cursor:pointer}
.thread:hover{background:var(--panel2)}
.thread.active{background:var(--panel2);border-color:var(--line)}
.thread .t{font-weight:600;font-size:13px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.thread .m{font-size:11px;color:var(--mut);margin-top:2px}
.threads .empty{padding:10px 6px}

/* ---- conversation (centre) ---- */
.conv{display:flex;flex-direction:column;min-height:0;overflow:hidden}
.strip{display:flex;gap:6px;padding:9px 16px;border-bottom:1px solid var(--line);background:var(--surface)}
.strip .s{flex:1;text-align:center;padding:2px 4px}
.strip .s .n{font-size:18px;font-weight:800;font-variant-numeric:tabular-nums;line-height:1.1}
.strip .s .l{font-size:10px;color:var(--mut);text-transform:uppercase;letter-spacing:.4px;margin-top:1px}
.strip .s[data-key=documents] .n{color:var(--qz-blue)}
.strip .s[data-key=passages] .n{color:var(--qz-blue-deep)}
.strip .s[data-key=entities] .n{color:var(--qz-purple)}
.strip .s[data-key=relationships] .n{color:var(--qz-coral)}
.strip .s[data-key=domains] .n{color:var(--qz-green)}
.messages{flex:1;overflow:auto;padding:18px 18px 8px;display:flex;flex-direction:column;gap:16px}
.messages .empty-chat{margin:auto;text-align:center;color:var(--mut);max-width:440px}
.messages .empty-chat h2{color:var(--qz-ink);font-size:18px;margin:0 0 6px}
.turn{display:flex;flex-direction:column;gap:10px}
.msg.user{align-self:flex-end;max-width:82%;background:var(--qz-blue);color:#fff;padding:9px 13px;border-radius:13px 13px 3px 13px;font-size:14px;line-height:1.45}
.msg.ai{align-self:stretch;border:1px solid var(--line);border-radius:12px;padding:12px 14px;background:var(--surface);cursor:pointer}
.msg.ai.sel{border-color:var(--qz-blue);box-shadow:0 0 0 3px var(--qz-blue-tint)}
.msg.ai .kwrap{display:flex;align-items:center;gap:8px;margin-bottom:6px}
.answer-text{font-size:15px;line-height:1.66}
.answer-text.quote{border-left:3px solid var(--qz-blue);padding:6px 12px;background:var(--panel);border-radius:0 8px 8px 0;font-style:italic;color:var(--qz-ink)}
.chipcite{display:inline-flex;align-items:center;gap:4px;background:var(--panel2);border:1px solid var(--line);border-radius:20px;
 padding:0 8px;font-size:12px;cursor:pointer;color:var(--qz-blue-deep);margin:0 2px;line-height:1.7;white-space:nowrap;font-weight:600}
.chipcite:hover{border-color:var(--qz-blue)}
.decline{border-left:3px solid var(--qz-amber);background:#FCF7EC;border-radius:0 8px 8px 0;padding:10px 12px;font-size:14px;color:var(--qz-ink)}
.decline .why{color:var(--mut);font-size:13px;margin-top:6px}

/* ---- composer ---- */
.composer{border-top:1px solid var(--line);padding:11px 16px 13px;background:var(--surface)}
.composer .box{display:flex;gap:8px;align-items:flex-end;border:1px solid var(--line);border-radius:12px;padding:6px 8px;background:var(--surface)}
.composer .box:focus-within{border-color:var(--qz-blue);box-shadow:0 0 0 3px var(--qz-blue-tint)}
.composer textarea{flex:1;border:none;box-shadow:none;min-height:26px;max-height:150px;padding:6px 4px;resize:none;background:transparent}
.composer textarea:focus{outline:none;box-shadow:none;border:none}
.iconbtn{border:1px solid var(--line);background:var(--surface);border-radius:9px;width:34px;height:34px;display:inline-flex;align-items:center;justify-content:center;color:var(--mut);cursor:pointer;flex:none}
.iconbtn[disabled]{opacity:.5;cursor:not-allowed}
.iconbtn.on{color:var(--qz-blue);border-color:var(--qz-blue);background:var(--qz-blue-tint)}
.composer .tools{display:flex;gap:8px;align-items:center;margin-top:8px}
.composer .langpill{font-size:12px}
.composer .langpill select{padding:4px 8px;font-size:12px}
.composer .grow{flex:1}
.composer .hint{font-size:11px;color:var(--soft)}

/* ---- right rail ---- */
.rail{border-left:1px solid var(--line);display:flex;flex-direction:column;overflow:auto;background:var(--surface)}
.rail section{padding:13px 14px;border-bottom:1px solid var(--line)}
.rail h4{margin:0 0 9px;font-size:12px;text-transform:uppercase;letter-spacing:.5px;color:var(--mut);font-weight:700;display:flex;align-items:center;justify-content:space-between;gap:8px}
.rail .placeholder{color:var(--soft);font-size:13px}
.gx{height:212px;border-radius:12px;position:relative;overflow:hidden}
.gx svg{width:100%;height:100%;display:block}
.gx .gx-edge{stroke:#4a5b86}
.gx .gx-edge.act{stroke:#7fd0ff}
.gx .gx-node{fill:#3a4a76}
.gx .gx-node.act{fill:#0096FF}
.gx .gx-node.flash{animation:gxflash 1.1s ease-out 1}
.gx text{fill:#dfe8ff;font-size:9px;font-weight:600}
@keyframes gxflash{0%{r:2}45%{r:7}100%{r:4.2}}
.gx-empty{position:absolute;inset:0;display:flex;align-items:center;justify-content:center;color:#9fb0d8;font-size:12px;text-align:center;padding:0 22px}
.gx-stats{display:flex;gap:10px;margin-top:8px;font-size:11px;color:var(--mut);flex-wrap:wrap}
.gx-stats b{color:var(--qz-ink)}
.card-rows .r{display:flex;justify-content:space-between;gap:12px;padding:6px 0;border-bottom:1px dashed var(--line);font-size:13px;align-items:baseline}
.card-rows .r:last-child{border-bottom:none}
.card-rows .r .k{color:var(--mut);flex:none}
.card-rows .r .v{text-align:right;font-weight:600;word-break:break-word}
.card-rows details{margin-top:6px}
.card-rows details>summary{margin:8px 0 2px}
.bars{display:inline-flex;gap:3px;vertical-align:middle}
.bars i{display:inline-block;width:9px;height:13px;border-radius:2px;background:var(--line)}
.bars i.on{background:var(--qz-blue)}
.trust-num{font-weight:800;color:var(--qz-ink)}
.trace-id{font-family:ui-monospace,Menlo,monospace;font-size:11px;color:var(--mut)}
.usage .win{display:flex;gap:6px;margin-bottom:10px}
.usage .win button{flex:1;font-size:12px;padding:5px 4px}
.usage .win button.on{background:var(--qz-blue);border-color:var(--qz-blue);color:#fff}
.usage .g3{display:grid;grid-template-columns:1fr 1fr 1fr;gap:8px;margin-bottom:10px}
.usage .g3 .u{background:var(--panel);border:1px solid var(--line);border-radius:10px;padding:8px;text-align:center}
.usage .g3 .u .n{font-size:18px;font-weight:800;font-variant-numeric:tabular-nums}
.usage .g3 .u .l{font-size:10px;color:var(--mut);text-transform:uppercase;letter-spacing:.4px}
.usage .split{display:flex;height:10px;border-radius:5px;overflow:hidden;margin:6px 0 4px;background:var(--panel)}
.usage .split i{display:block;height:100%}
.usage .legend{display:flex;flex-wrap:wrap;gap:8px;font-size:11px;color:var(--mut)}
.usage .legend .k{display:inline-flex;align-items:center;gap:4px}
.usage .legend .dot{width:8px;height:8px;border-radius:2px;display:inline-block}
.trk{height:8px;border-radius:5px;background:var(--panel);overflow:hidden;margin-top:4px}
.trk i{display:block;height:100%;background:var(--qz-blue)}
.kv{display:flex;justify-content:space-between;font-size:12px;color:var(--mut);margin-top:8px}
.kv b{color:var(--qz-ink)}

/* reasoning timeline (kept from Stage 2, shown under Details) */
.timeline{list-style:none;margin:8px 0 0;padding:0 0 0 16px;border-left:2px solid var(--line)}
.timeline li{position:relative;margin:0 0 12px;padding-left:12px}
.timeline li:before{content:"";position:absolute;left:-23px;top:6px;width:9px;height:9px;border-radius:50%;background:var(--accent);border:2px solid var(--surface)}
.timeline li.skipped:before{background:var(--mut)}.timeline li.cond-true:before{background:var(--good)}.timeline li.cond-false:before{background:var(--bad)}
.timeline .q{font-weight:600;font-size:13px}.timeline .a{font-size:12px;color:var(--fg);margin-top:3px;white-space:pre-wrap}

/* page viewer + explain overlay reuse the shared .drawer */
.pagedoc{margin-top:10px;border:1px solid var(--line);border-radius:10px;padding:12px 14px;background:var(--panel);font-size:14px;line-height:1.6}
.pagedoc mark{background:#FFF2A8;padding:0 2px;border-radius:3px}
.pagemeta{font-size:12px;color:var(--mut);margin-bottom:8px}
.explain-flow{display:flex;flex-direction:column;gap:0}
.enode{border:1px solid var(--line);border-radius:10px;padding:10px 12px;background:var(--surface)}
.enode .et{font-size:11px;text-transform:uppercase;letter-spacing:.5px;color:var(--qz-blue-deep);font-weight:700}
.enode .ev{font-size:14px;margin-top:2px;color:var(--qz-ink)}
.earrow{align-self:center;color:var(--qz-soft);font-size:16px;line-height:1.2}
.samples{display:flex;gap:6px;flex-wrap:wrap;margin-top:10px;justify-content:center}
.chip{background:var(--panel);border:1px solid var(--line);border-radius:20px;padding:4px 11px;font-size:12px;cursor:pointer;color:var(--fg)}
.chip:hover{border-color:var(--accent)}
"""

_BODY = """
<div class="ws" id="ws">
  <aside class="threads" id="threads" aria-label="Conversations">
    <button class="btn primary new" id="new-chat">+ New chat</button>
    <div id="thread-list"></div>
  </aside>

  <section class="conv" id="conv">
    <div class="strip" id="corpus-strip" aria-label="Corpus at a glance">
      <div class="s" data-key="documents"><div class="n" id="tile-documents">0</div><div class="l">Documents</div></div>
      <div class="s" data-key="passages"><div class="n" id="tile-passages">0</div><div class="l">Passages</div></div>
      <div class="s" data-key="entities"><div class="n" id="tile-entities">0</div><div class="l">Entities</div></div>
      <div class="s" data-key="relationships"><div class="n" id="tile-relationships">0</div><div class="l">Relationships</div></div>
      <div class="s" data-key="domains"><div class="n" id="tile-domains">0</div><div class="l">Domains</div></div>
    </div>

    <div class="messages" id="messages"></div>

    <div class="composer">
      <div class="box">
        <button class="iconbtn" id="mic-btn" type="button" disabled title="Voice arrives with read-aloud (a later step)">&#127908;</button>
        <textarea id="question" rows="1" placeholder="Ask the knowledge fabric… try 'and' for multistep, 'if … then …' for conditional, 'compare A and B'."></textarea>
        <button class="btn primary" id="ask-btn" type="button">Send</button>
      </div>
      <div class="tools">
        <span class="langpill" title="Answers match the question's language automatically (EN · FR · ES · JA).">
          <label class="hint" for="answer-lang">Answer language</label>
          <select id="answer-lang">
            <option value="auto">Auto-detect</option>
            <option value="en">English</option><option value="fr">French</option>
            <option value="es">Spanish</option><option value="ja">Japanese</option>
          </select>
        </span>
        <button class="iconbtn" id="read-aloud" type="button" disabled title="Read-aloud arrives with voice (a later step)">&#128266;</button>
        <span class="grow"></span>
        <span class="hint" id="ask-status"></span>
      </div>
    </div>
  </section>

  <aside class="rail" id="rail" aria-label="Answer detail">
    <section id="galaxy-section">
      <h4>Answer galaxy <button class="btn sm" id="explain-btn" disabled>Why did the AI say this?</button></h4>
      <div class="gx galaxy" id="galaxy"><div class="gx-empty" id="galaxy-empty">Ask a question to light up the graph.</div></div>
      <div class="gx-stats" id="galaxy-stats"></div>
    </section>
    <section id="card-section">
      <h4>The card</h4>
      <div class="card-rows" id="answer-card"><div class="placeholder">The answer's reasoning, model, trust and cost appear here.</div></div>
    </section>
    <section id="usage-section" class="usage">
      <h4>My usage <span class="trace-id" id="usage-subject"></span></h4>
      <div id="usage-body"><div class="placeholder">Sign in to see your questions, tokens and cost.</div></div>
    </section>
  </aside>
</div>

<div class="drawer hidden" id="page-drawer"></div>
<div class="drawer hidden" id="explain-drawer"></div>
"""

_JS = r"""
const {$,$$,esc,num,pct,money,ms,toast,gate,api}=KF;

// ---- reader-facing level words (never a number or an internal code, L1.5) ---
const LEVEL_WORDS={
 lookup:{word:'Look it up',cls:'lv-look'},
 fast:{word:'Quote it',cls:'lv-quote'},
 reason:{word:'Summarise it',cls:'lv-sum'},
 escalation:{word:'Reason about it',cls:'lv-reason'},
 clarify:{word:'Needs a clearer question',cls:''},
 gap:{word:'Outside the knowledge base',cls:''}};
function levelWord(name){const k=String(name||'');
 if(k.indexOf('reasoning')===0)return {word:'Reason about it',cls:'lv-reason'};
 return LEVEL_WORDS[k]||{word:'—',cls:''}}
const KIND_CLS={answer:'good',clarify:'warn',gap:'bad'};
const CPLX_CLS={simple:'good',medium:'warn',complex:'violet'};
const DECLINE="There isn't enough evidence in the fabric to answer that.";

// ===================== threads (per-session) =========================
let THREADS=[], CUR=null;
function loadThreads(){try{THREADS=JSON.parse(sessionStorage.getItem('kf.threads')||'[]')}catch(e){THREADS=[]}}
function saveThreads(){try{sessionStorage.setItem('kf.threads',JSON.stringify(THREADS.slice(0,40)))}catch(e){}}
function newThread(){const t={id:'t'+Date.now(),title:'New chat',turns:[],at:Date.now()};THREADS.unshift(t);CUR=t.id;saveThreads();renderThreads();renderMessages();$('#question').focus()}
function curThread(){return THREADS.find(t=>t.id===CUR)}
function openThread(id){CUR=id;renderThreads();renderMessages();const t=curThread();
 if(t&&t.turns.length)selectAnswer(t.turns[t.turns.length-1])}
function renderThreads(){const box=$('#thread-list');
 if(!THREADS.length){box.innerHTML='<div class="empty">No conversations yet.</div>';return}
 box.innerHTML=THREADS.map(t=>'<div class="thread'+(t.id===CUR?' active':'')+'" data-id="'+t.id+'">'+
  '<div class="t">'+esc(t.title)+'</div><div class="m">'+t.turns.length+' message'+(t.turns.length===1?'':'s')+'</div></div>').join('');
 $$('#thread-list .thread').forEach(el=>el.onclick=()=>openThread(el.dataset.id))}

// ===================== conversation ==================================
function renderMessages(){const box=$('#messages');const t=curThread();
 if(!t||!t.turns.length){box.innerHTML='<div class="empty-chat"><h2>Ask the knowledge fabric</h2>'+
   '<p class="muted">Every answer is grounded in your documents, routed to the right level, and fully explained on the right.</p>'+
   '<div class="samples" id="samples"></div></div>';samples();return}
 box.innerHTML=t.turns.map((tn,i)=>'<div class="turn" data-i="'+i+'">'+
   '<div class="msg user">'+esc(tn.q)+'</div>'+aiBlock(tn.a,i)+'</div>').join('');
 $$('#messages .msg.ai').forEach(el=>el.onclick=()=>selectAnswer(t.turns[+el.dataset.i]));
 wireCites();box.scrollTop=box.scrollHeight}
function aiBlock(a,i){const lw=levelWord((a.why||{}).level_name);
 const badges='<span class="pill '+(KIND_CLS[a.kind]||'')+'">'+esc(a.kind)+'</span>'+
  (a.kind==='answer'?'<span class="pill '+lw.cls+'">'+esc(lw.word)+'</span>':'')+
  '<span class="pill">'+esc((a.lang||'en').toUpperCase())+'</span>'+
  (a.cache_hit?'<span class="pill good">cached</span>':'');
 let body;
 if(a.kind==='answer'){const quote=lw.cls==='lv-quote';
  body='<div class="answer-text'+(quote?' quote':'')+'">'+withChips(a)+'</div>';}
 else{const reason=(a.why||{}).explain||(a.clarify_back||'');
  body='<div class="decline">'+esc(a.kind==='clarify'?(a.clarify_back||DECLINE):DECLINE)+
   (reason&&a.kind!=='clarify'?'<div class="why">'+esc(reason)+'</div>':'')+'</div>';}
 return '<div class="msg ai" data-i="'+i+'"><div class="kwrap">'+badges+'</div>'+body+'</div>'}
// inline citation chips: [n] -> "Title · p.14" (opens the page viewer, L2.2).
function withChips(a){const cs=a.citations||[];
 return esc(a.answer_text||'').replace(/\[(\d+)\]/g,(m,n)=>{const c=cs[+n-1];if(!c)return '';
  return '<span class="chipcite" data-cite="'+esc(n)+'">'+esc(c.document_title)+' &middot; '+esc(c.coordinate_render)+'</span>'})}
function wireCites(){const t=curThread();if(!t)return;
 $$('#messages .chipcite').forEach(el=>el.onclick=ev=>{ev.stopPropagation();
  const turn=t.turns[+el.closest('.msg.ai').dataset.i];openPage((turn.a.citations||[])[+el.dataset.cite-1])})}

// ===================== the card (L2.3) ===============================
function bars(sig){const order=['retrieval','semantic','coverage','agreement','resolvable'];
 return '<span class="bars" title="grounding signals: '+order.join(', ')+'">'+
  order.map(k=>'<i class="'+(((sig||{})[k]||0)>=0.5?'on':'')+'" title="'+k+' '+pct((sig||{})[k])+'"></i>').join('')+'</span>'}
function reasonWord(a){const r=((a.why||{}).reasons||[]).map(x=>x.code);
 return r.indexOf('confidence_fail')>=0?'Yes — escalated after a confidence check':'No'}
function modelLabel(a){const m=a.model_name||'';
 return (!m||/mock|echo|demo|off|none/i.test(m))?'demo model':esc(m)}
let CARD_GX={};
function card(a){const box=$('#answer-card');const w=a.why||{};const lw=levelWord(w.level_name);
 const trust=Math.round((Number(a.confidence)||0)*100);
 const found=(w.retrieved!=null?w.retrieved:(a.citations||[]).length);
 const cited=(a.citations||[]).length;
 const gx=CARD_GX[a.trajectory_id]||{};
 const topCost=(Number(a.cost)||0)+(Number(a.cost_saved)||0);
 const auth=a.authoritative_source;
 const rows=[];
 rows.push(['Answered by','<span class="pill '+lw.cls+'">'+esc(lw.word)+'</span>']);
 rows.push(['Why','<span style="font-weight:500">'+esc(w.explain||'—')+'</span>']);
 rows.push(['Model',modelLabel(a)]);
 rows.push(['Moved levels',esc(reasonWord(a))]);
 // --- under Details ---
 const det=[];
 det.push(['Relationships',(gx.relationships!=null?gx.relationships:0)+' &middot; '+(gx.hops!=null?gx.hops:0)+' hop(s) &middot; '+(gx.documents!=null?gx.documents:cited)+' docs']);
 det.push(['Sources',found+' found &middot; '+cited+' cited']);
 det.push(['Trust','<span class="trust-num">'+trust+'</span> '+bars(w.signals)+' <span class="muted small" title="grounding score">g '+pct(a.grounding_score)+'</span>']);
 det.push(['Language','<span class="pill">'+esc((a.lang||'en').toUpperCase())+'</span>']);
 det.push(['Tokens',num(a.tokens_in)+' in &middot; '+num(a.tokens_out)+' out &middot; cache read —']);
 det.push(['Cost',money(a.cost)+' &middot; top '+money(topCost)+' &middot; saved '+money(a.cost_saved)]);
 det.push(['Cache',a.cache_hit?'<span class="pill good">hit</span>':'<span class="pill">miss</span>']);
 det.push(['Timing',a._ms!=null?ms(a._ms):'—']);
 det.push(['Complexity','<span class="pill '+(CPLX_CLS[a.complexity]||'')+'">'+esc(a.complexity||'n/a')+'</span> &middot; dataset v'+esc(a.dataset_version||0)]);
 let authHtml='<span class="muted">none determined</span>';
 if(auth){authHtml='<span class="pill good">'+esc(auth.source||'authoritative')+'</span> '+esc(auth.document_title||'');
  if((auth.conflicts||[]).length)authHtml+=' <span class="pill warn">'+auth.conflicts.length+' conflict(s)</span>';}
 det.push(['Authoritative source',authHtml]);
 det.push(['Trace','<a href="#" id="trace-link">show trace id</a> <span class="trace-id hidden" id="trace-val">'+esc(a.trajectory_id||'')+'</span>']);
 const rowHtml=r=>'<div class="r"><span class="k">'+r[0]+'</span><span class="v">'+r[1]+'</span></div>';
 box.innerHTML=rows.map(rowHtml).join('')+
  '<details><summary>Details</summary>'+det.map(rowHtml).join('')+reasoningHtml(a)+'</details>';
 const tl=$('#trace-link');if(tl)tl.onclick=e=>{e.preventDefault();$('#trace-val').classList.toggle('hidden')};
 $('#explain-btn').disabled=!(a.kind==='answer');
 $('#explain-btn').onclick=()=>openExplain(a,gx)}
function reasoningHtml(a){const r=a.reasoning;if(!r||!(r.steps||[]).length)return '';
 return '<div class="r"><span class="k">Reasoning</span><span class="v">'+esc(r.mode||'multistep')+'</span></div>'+
  '<ol class="timeline">'+(r.steps||[]).map(s=>{const cls=s.skipped?'skipped':s.condition===true?'cond-true':s.condition===false?'cond-false':'';
   return '<li class="'+cls+'"><div class="q">'+esc(s.question||s.id||'')+'</div>'+
    (s.skipped?'<div class="muted small">'+esc(s.reason||'branch not taken')+'</div>':'<div class="a">'+esc(s.answer_text||'')+'</div>')+'</li>'}).join('')+'</ol>'}

// ===================== page viewer (L2.2) ============================
function openPage(c){if(!c)return;const d=$('#page-drawer');
 const snip=esc(c.snippet||'');
 d.innerHTML='<button class="btn sm right" id="page-close">Close</button>'+
  '<h3>'+esc(c.document_title||'Document')+'</h3>'+
  '<div class="pagemeta">'+esc(c.coordinate_render||'')+' &middot; passage '+esc(c.passage_id||'')+'</div>'+
  '<div class="pagedoc"><mark>'+snip+'</mark></div>'+
  '<p class="muted small" style="margin-top:10px">The full page renders here once document conversion lands; today the cited passage is shown highlighted.</p>';
 d.classList.remove('hidden');$('#page-close').onclick=()=>d.classList.add('hidden')}

// ===================== Explain overlay (L2.4) =======================
function openExplain(a,gx){const d=$('#explain-drawer');const w=a.why||{};const lw=levelWord(w.level_name);
 const c0=(a.citations||[])[0]||{};
 const nodes=[
  ['Decision','Answered by “'+lw.word+'” — '+(w.explain||'')],
  ['Sources',((w.retrieved!=null?w.retrieved:(a.citations||[]).length))+' passages found, '+(a.citations||[]).length+' cited'],
  ['Evidence','Trust '+Math.round((a.confidence||0)*100)+' / 100 across five grounding signals'],
  ['Document',(a.authoritative_source&&a.authoritative_source.document_title)||c0.document_title||'—'],
  ['Page',c0.coordinate_render||'—']];
 d.innerHTML='<button class="btn sm right" id="ex-close">Close</button><h3>Why did the AI say this?</h3>'+
  '<p class="muted small">Decision → Sources → Evidence → Document → Page</p><div class="explain-flow">'+
  nodes.map((n,i)=>'<div class="enode"><div class="et">'+esc(n[0])+'</div><div class="ev">'+esc(n[1])+'</div></div>'+
   (i<nodes.length-1?'<div class="earrow">&#8595;</div>':'')).join('')+'</div>';
 d.classList.remove('hidden');$('#ex-close').onclick=()=>d.classList.add('hidden')}

// ===================== galaxy (L2.4) ================================
function selectAnswer(turn){const a=turn.a;
 $$('#messages .msg.ai').forEach(el=>el.classList.remove('sel'));
 const el=$('#messages .msg.ai[data-i="'+turn._i+'"]');if(el)el.classList.add('sel');
 card(a);loadGalaxy(a.trajectory_id,a);loadUsage()}
async function loadGalaxy(trace_id,a){const box=$('#galaxy'),st=$('#galaxy-stats');
 if(!trace_id){box.innerHTML='<div class="gx-empty">Ask a question to light up the graph.</div>';st.innerHTML='';return}
 try{const g=await api('/api/galaxy?trace_id='+encodeURIComponent(trace_id));
  CARD_GX[trace_id]=g.stats||{};if(a)card(a);
  renderGalaxy(g);
  const s=g.stats||{};st.innerHTML='<span><b>'+(s.activated||0)+'</b> lit</span><span><b>'+(s.relationships||0)+
   '</b> links</span><span><b>'+(s.hops||0)+'</b> hop(s)</span><span><b>'+(s.passages||0)+'</b> passages</span>';}
 catch(e){box.innerHTML='<div class="gx-empty">Galaxy unavailable for this answer.</div>';st.innerHTML=''}}
function renderGalaxy(g){const box=$('#galaxy');const nodes=(g.nodes||[]).slice(0,60);
 if(!nodes.length){box.innerHTML='<div class="gx-empty">No graph relationships were used for this answer.</div>';return}
 const idx={};nodes.forEach((n,i)=>idx[n.id]=i);
 const edges=(g.edges||[]).filter(e=>idx[e.src]!=null&&idx[e.dst]!=null);
 // deterministic seed layout on a circle, then a short force settle.
 const N=nodes.length,P=nodes.map((n,i)=>({x:Math.cos(i/N*6.283)*100,y:Math.sin(i/N*6.283)*100}));
 for(let it=0;it<90;it++){const fx=new Array(N).fill(0),fy=new Array(N).fill(0);
  for(let i=0;i<N;i++)for(let j=i+1;j<N;j++){let dx=P[i].x-P[j].x,dy=P[i].y-P[j].y;let d2=dx*dx+dy*dy||0.01;let f=380/d2;
   let d=Math.sqrt(d2);dx/=d;dy/=d;fx[i]+=dx*f;fy[i]+=dy*f;fx[j]-=dx*f;fy[j]-=dy*f}
  edges.forEach(e=>{const i=idx[e.src],j=idx[e.dst];let dx=P[j].x-P[i].x,dy=P[j].y-P[i].y;let d=Math.sqrt(dx*dx+dy*dy)||0.01;
   let f=(d-46)*0.04;dx/=d;dy/=d;fx[i]+=dx*f;fy[i]+=dy*f;fx[j]-=dx*f;fy[j]-=dy*f});
  for(let i=0;i<N;i++){P[i].x+=Math.max(-8,Math.min(8,fx[i]))-P[i].x*0.008;P[i].y+=Math.max(-8,Math.min(8,fy[i]))-P[i].y*0.008}}
 let minx=1e9,miny=1e9,maxx=-1e9,maxy=-1e9;
 P.forEach(p=>{minx=Math.min(minx,p.x);miny=Math.min(miny,p.y);maxx=Math.max(maxx,p.x);maxy=Math.max(maxy,p.y)});
 const pad=18,vw=(maxx-minx)+pad*2,vh=(maxy-miny)+pad*2;
 const X=x=>x-minx+pad,Y=y=>y-miny+pad;
 let svg='<svg viewBox="0 0 '+vw.toFixed(0)+' '+vh.toFixed(0)+'" preserveAspectRatio="xMidYMid meet">';
 edges.forEach(e=>{const a=P[idx[e.src]],b=P[idx[e.dst]];
  svg+='<line class="gx-edge'+(e.activated?' act':'')+'" x1="'+X(a.x).toFixed(1)+'" y1="'+Y(a.y).toFixed(1)+
   '" x2="'+X(b.x).toFixed(1)+'" y2="'+Y(b.y).toFixed(1)+'" stroke-width="'+(e.activated?1.2:0.7)+
   '" stroke-opacity="'+(e.activated?0.9:0.04)+'"/>'});
 // degree per node, so only the most-connected activated nodes get a label
 // (a compact galaxy stays legible instead of a wall of overlapping text).
 const deg={};edges.forEach(e=>{deg[e.src]=(deg[e.src]||0)+1;deg[e.dst]=(deg[e.dst]||0)+1});
 const labelled=new Set(nodes.filter(n=>n.activated).sort((a,b)=>(deg[b.id]||0)-(deg[a.id]||0)).slice(0,9).map(n=>n.id));
 nodes.forEach((n,i)=>{const p=P[i];svg+='<circle class="gx-node'+(n.activated?' act flash':'')+'" cx="'+X(p.x).toFixed(1)+
   '" cy="'+Y(p.y).toFixed(1)+'" r="'+(n.activated?4.2:2.4)+'"><title>'+esc(n.label)+'</title></circle>';
  if(labelled.has(n.id))svg+='<text x="'+(X(p.x)+5).toFixed(1)+'" y="'+(Y(p.y)+3).toFixed(1)+'">'+esc((n.label||'').slice(0,16))+'</text>'});
 box.innerHTML=svg+'</svg>'}

// ===================== my usage (L2.5) ==============================
let USAGE=null, USE_WIN='today';
const WIN_LABEL={today:'Today',['7d']:'7 days',['30d']:'30 days'};
const LV_COLOR={lookup:'#0CA678',fast:'#0096FF',reason:'#7048E8',escalation:'#F53E5A'};
async function loadUsage(){if(!KF.session)return;
 try{USAGE=await api('/api/usage');$('#usage-subject').textContent=esc(USAGE.subject||'');renderUsage()}
 catch(e){/* rail stays quiet on usage errors */}}
function renderUsage(){const box=$('#usage-body');if(!USAGE){return}
 const w=(USAGE.windows||{})[USE_WIN]||{};
 const wins=['today','7d','30d'];
 let html='<div class="win">'+wins.map(k=>'<button class="btn sm'+(k===USE_WIN?' on':'')+'" data-w="'+k+'">'+WIN_LABEL[k]+'</button>').join('')+'</div>';
 html+='<div class="g3"><div class="u"><div class="n">'+num(w.questions)+'</div><div class="l">Questions</div></div>'+
  '<div class="u"><div class="n">'+num(w.answered)+'</div><div class="l">Answered</div></div>'+
  '<div class="u"><div class="n">'+num(w.declined)+'</div><div class="l">Declined</div></div></div>';
 const bl=w.by_level||{};const tot=Object.values(bl).reduce((s,v)=>s+v,0)||0;
 if(tot){html+='<div class="split">'+Object.keys(bl).map(k=>'<i style="width:'+(bl[k]/tot*100).toFixed(1)+'%;background:'+(LV_COLOR[k]||'#7C8DA1')+'"></i>').join('')+'</div>'+
   '<div class="legend">'+Object.keys(bl).map(k=>'<span class="k"><span class="dot" style="background:'+(LV_COLOR[k]||'#7C8DA1')+'"></span>'+esc(levelWord(k).word)+' '+bl[k]+'</span>').join('')+'</div>';}
 html+='<div class="kv"><span>Tokens</span><b>'+num((w.tokens_in||0)+(w.tokens_out||0))+'</b></div>'+
  '<div class="kv"><span>Cost</span><b>'+money(w.cost)+'</b></div>'+
  '<div class="kv"><span>Saved by cache</span><b>'+money(w.cost_saved)+'</b></div>';
 const b=USAGE.budget;
 if(b&&isFinite(b.cap)){const used=b.cap?Math.min(1,b.spent/b.cap):0;
  html+='<div class="kv" style="margin-top:10px"><span>Fabric budget</span><b>'+money(b.spent)+' / '+money(b.cap)+'</b></div>'+
   '<div class="trk"><i style="width:'+(used*100).toFixed(1)+'%"></i></div>';}
 html+='<div class="kv"><span>Speech seconds</span><b>— <span class="muted small">(with voice)</span></b></div>';
 box.innerHTML=html;
 $$('#usage-body .win button').forEach(bt=>bt.onclick=()=>{USE_WIN=bt.dataset.w;renderUsage()})}

// ===================== ask ==========================================
async function samples(){if(!$('#samples'))return;let qs=[];
 if(KF.session){try{const j=await api('/api/suggestions');qs=(j.suggestions||[]).map(s=>s.question).slice(0,6)}catch(e){}}
 if(!qs.length)qs=(KF.DIR.questions&&KF.DIR.questions['qualizeal'])||[];
 $('#samples').innerHTML=qs.map(q=>'<span class="chip" data-q="'+esc(q)+'">'+esc(q)+'</span>').join('');
 $$('#samples .chip').forEach(c=>c.onclick=()=>{$('#question').value=c.dataset.q;$('#question').focus()})}
async function ask(){const q=$('#question').value.trim();if(!q)return;
 if(!KF.session){gate({status:401,message:''},'asker');return}
 if(!curThread())newThread();
 const btn=$('#ask-btn');btn.disabled=true;$('#ask-status').textContent='thinking…';const t0=performance.now();
 try{const a=await api('/ask',{method:'POST',body:{question:q}});gate(null);
  a._ms=performance.now()-t0;
  const t=curThread();const turn={q,a};t.turns.push(turn);turn._i=t.turns.length-1;
  if(t.turns.length===1)t.title=q.slice(0,48);
  t.at=Date.now();saveThreads();renderThreads();renderMessages();
  turn._i=t.turns.length-1;selectAnswer(turn);
  $('#ask-status').textContent=Math.round(a._ms)+' ms · '+(a.kind||'');$('#question').value='';autosize()}
 catch(e){gate(e,'asker');$('#ask-status').textContent='failed';toast(e.message,'bad')}
 finally{btn.disabled=false}}

// ===================== corpus strip =================================
function animateNumber(el,target,msdur){target=Number(target)||0;const from=Number(String(el.textContent).replace(/[^0-9]/g,''))||0;
 const t0=performance.now();const dur=msdur||600;
 (function step(t){const p=Math.min(1,(t-t0)/dur);el.textContent=num(Math.round(from+(target-from)*(1-Math.pow(1-p,3))));if(p<1)requestAnimationFrame(step)})(performance.now())}
async function corpusStrip(){if(!KF.session)return;
 try{const c=await api('/api/corpus');['documents','passages','entities','relationships','domains'].forEach(k=>{const el=$('#tile-'+k);if(el)animateNumber(el,c[k]||0)})}catch(e){}}

// ===================== composer wiring ==============================
function autosize(){const t=$('#question');t.style.height='auto';t.style.height=Math.min(150,t.scrollHeight)+'px'}
$('#ask-btn').onclick=ask;
$('#question').addEventListener('input',autosize);
$('#question').addEventListener('keydown',e=>{if(e.key==='Enter'&&!e.shiftKey){e.preventDefault();ask()}});
$('#new-chat').onclick=newThread;

// ===================== session lifecycle ============================
function boot(){loadThreads();if(!THREADS.length){CUR=null}else{CUR=THREADS[0].id}
 renderThreads();renderMessages();
 if(KF.session){corpusStrip();loadUsage()}else{gate({status:401,message:''},'asker')}}
window.KF_ON_SESSION=s=>{gate(null);if(s){corpusStrip();loadUsage();samples();$('#ask-status').textContent='ready for '+s.subject}
 else{$('#usage-body').innerHTML='<div class="placeholder">Sign in to see your usage.</div>'}};
KF.initBar({preferRole:'asker'});boot();
"""

ASK_HTML = shell("Workspace", "", _BODY, _JS, "Workspace", _CSS)
