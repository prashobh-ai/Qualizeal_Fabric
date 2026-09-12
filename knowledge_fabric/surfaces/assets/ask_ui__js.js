
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
// the question key — the same normalisation the engine and the bake use.
function norm(q){return String(q||'').toLowerCase().replace(/[^a-z0-9\s]/g,' ').replace(/\s+/g,' ').trim()}

// ===================== the ask queue (T45) ============================
// On the static Workspace the engine stamps `queue:{eligible,hash,path,repo}`
// on a Level 2/3 or gap answer that has no baked file. "Get full answer" opens
// an `ask` issue (the ask.yml form, pre-filled); the page then polls
// answers/<hash>.json every 20 s for 5 minutes and swaps the bubble when the
// queue's answer lands. The live server never stamps `queue`, so no button.
const QUEUE_POLL_MS=20000, QUEUE_POLL_FOR_MS=5*60*1000;
let QUEUE=[];   // {question,hash,at,status:'pending'|'answered',model,cost_usd} — per browser
function loadQueue(){try{QUEUE=JSON.parse(localStorage.getItem('kf.queue')||'[]')}catch(e){QUEUE=[]}}
function saveQueue(){try{localStorage.setItem('kf.queue',JSON.stringify(QUEUE.slice(0,50)))}catch(e){}}
function issueUrl(a){const q=a.queue||{};const question=q.question||'';
 const body={question,subject:(KF.session&&KF.session.subject)||'',context:{hash:q.hash||''}};
 return 'https://github.com/'+(q.repo||'prashobh-ai/QualiZeal_Fabric')+'/issues/new?template=ask.yml&labels=ask'+
  '&title='+encodeURIComponent(question)+'&question='+encodeURIComponent(question)+'&body='+encodeURIComponent(JSON.stringify(body))}
function queueBar(a,i){if(!a.queue||!a.queue.eligible||a.baked)return '';
 const pending=QUEUE.find(x=>x.hash===a.queue.hash&&x.status==='pending');
 return '<div class="queue-bar" data-i="'+i+'"><a class="btn sm primary qbtn" href="'+esc(issueUrl(a))+'" target="_blank" rel="noopener">Get full answer</a>'+
  '<span class="muted small qstatus">'+(pending?'queued — waiting for the full answer…':'Queue this question for the full agent answer (about a minute).')+'</span></div>'}
function wireQueue(){const t=curThread();if(!t)return;
 $$('#messages .queue-bar .qbtn').forEach(b=>b.onclick=()=>{const turn=t.turns[+b.closest('.queue-bar').dataset.i];if(turn)startPoll(turn)})}
function startPoll(turn){const q=turn.a.queue;if(!q||!q.hash)return;
 if(!QUEUE.some(x=>x.hash===q.hash))QUEUE.unshift({question:q.question,hash:q.hash,at:Date.now(),status:'pending'});
 saveQueue();renderUsage();
 const bar=$('#messages .queue-bar[data-i="'+turn._i+'"] .qstatus');if(bar)bar.textContent='queued — waiting for the full answer…';
 const t0=Date.now();
 const tick=async()=>{try{const r=await fetch((KF.base()||'')+'/'+q.path,{cache:'no-store'});
   if(r.ok){const j=await r.json();if(j&&j.kind){applyFull(turn,j);return}}}catch(e){}
  if(Date.now()-t0<QUEUE_POLL_FOR_MS)setTimeout(tick,QUEUE_POLL_MS);
  else{const el=$('#messages .queue-bar[data-i="'+turn._i+'"] .qstatus');if(el)el.textContent='still queued — check back later or open the issue.'}};
 setTimeout(tick,QUEUE_POLL_MS)}
function applyFull(turn,j){const a=Object.assign({},j);
 a.model_name=j.model||a.model_name||'';a.cost=Number(j.cost_usd!=null?j.cost_usd:a.cost)||0;
 a.baked={source:j.source||'queue',asked_at:j.asked_at||'',model:j.model||'',cost_usd:Number(j.cost_usd)||0,steps:(j.steps||[]).length};
 a.why=a.why||{level_name:'baked',explain:'Served from the queue answer.',reasons:[],signals:{},retrieved:(a.citations||[]).length};
 a.role_view=turn.a.role_view;a._ms=turn.a._ms;turn.a=a;
 const row=QUEUE.find(x=>x.hash===j.question_hash||norm(x.question)===norm(j.question||''));
 if(row){row.status='answered';row.model=a.model_name;row.cost_usd=a.cost;}
 saveQueue();saveThreads();renderMessages();selectAnswer(turn);toast('Full answer arrived.','good')}

// ===================== threads (per-session) =========================
let THREADS=[], CUR=null;
let LAST_A=null, SPEAKING=false, SPEECH_SECS=0; // voice: last answer shown + read-aloud state
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
   '<div class="msg user">'+esc(tn.q)+'</div>'+
   (tn.a&&tn.a.understood_as&&norm(tn.a.understood_as)!==norm(tn.q)
     ?'<div class="understood">understood as: '+esc(tn.a.understood_as)+'</div>':'')+
   aiBlock(tn.a,i)+'</div>').join('');
 $$('#messages .msg.ai').forEach(el=>el.onclick=()=>selectAnswer(t.turns[+el.dataset.i]));
 wireCites();wireFeedback();wireClarify();wireQueue();wireExplain();box.scrollTop=box.scrollHeight}
// T81 — the Explain affordance buttons fire POST /api/explain on click.
function wireExplain(){$$('#messages .explain-btn').forEach(b=>b.onclick=e=>{e.stopPropagation();runExplain(b)})}
// a reader clicks one of the clarify's offered questions -> ask it straight away.
function wireClarify(){$$('#messages .clarify-chips .chip').forEach(c=>c.onclick=e=>{e.stopPropagation();
 $('#question').value=c.dataset.cq;autosize();$('#question').focus();ask()})}
function aiBlock(a,i){const lw=levelWord((a.why||{}).level_name);
 const badges='<span class="pill '+(KIND_CLS[a.kind]||'')+'">'+esc(a.kind)+'</span>'+
  (a.kind==='answer'?'<span class="pill '+lw.cls+'">'+esc(lw.word)+'</span>':'')+
  '<span class="pill">'+esc((a.lang||'en').toUpperCase())+'</span>'+
  (a.cache_hit?'<span class="pill good">cached</span>':'');
 let body;
 if(a.kind==='answer'){const quote=lw.cls==='lv-quote';
  body='<div class="answer-text'+(quote?' quote':'')+'">'+renderAnswer(a)+'</div>';}
 else{const reason=(a.why||{}).explain||(a.clarify_back||'');
  const sugg=(a.kind==='clarify'&&(a.suggestions||[]).length)?
   '<div class="clarify-chips">'+a.suggestions.map(s=>'<span class="chip" data-cq="'+esc(s)+'">'+esc(s)+'</span>').join('')+'</div>':'';
  body='<div class="decline">'+esc(a.kind==='clarify'?(a.clarify_back||DECLINE):DECLINE)+
   (reason&&a.kind!=='clarify'?'<div class="why">'+esc(reason)+'</div>':'')+sugg+'</div>';}
 const fb='<div class="fbbar" data-i="'+i+'"><span class="muted small">Was this helpful?</span>'+
  '<button class="fbbtn up" title="Helpful">&#128077;</button>'+
  '<button class="fbbtn down" title="Not helpful — flag for the curators">&#128078;</button>'+
  '<span class="fbmsg muted small"></span></div>';
 const baked=a.baked?'<span class="pill info" title="'+esc(a.baked.asked_at||'')+'">'+(a.baked.source==='queue'?'full answer':'baked')+'</span>':'';
 const gov=a.kind==='answer'?govLine(a):'';
 const explain=a.kind==='answer'?explainBar(a,i):'';
 return '<div class="msg ai" data-i="'+i+'"><div class="kwrap">'+badges+baked+'</div>'+stepsHtml(a)+body+gov+explain+roleLens(a)+queueBar(a,i)+fb+'</div>'}
// T85 — the governance line under every answer: source kind, authority, and
// freshness; amber + a "show newer sources" nudge when the source is stale.
function govLine(a){const g=a.governance;if(!g)return '';
 return '<div class="govline'+(g.stale?' stale':'')+'">'+
  '<span class="pill">'+esc(g.source_kind||'source')+'</span>'+
  '<span class="muted small">'+esc(g.authority||'cited')+'</span>'+
  '<span class="fresh muted small">'+esc(g.freshness||'')+'</span>'+
  (g.stale?'<span class="pill warn">stale</span>':'')+'</div>'}
// T81 — the Explain affordance: persona-appropriate offers (Why? / Show working
// / Break down …) that fire POST /api/explain as a separate ledgered step and
// append the narrative to the turn.
function explainBar(a,i){const ex=a.explain;if(!ex||!ex.available||!(ex.offers||[]).length)return '';
 const btns=ex.offers.map(o=>'<button class="btn sm explain-btn" data-tr="'+esc(ex.trace_id)+'" data-i="'+i+'">'+esc(o)+'</button>').join('');
 return '<div class="explainbar" data-i="'+i+'">'+btns+'<div class="explain-out" hidden></div></div>'}
async function runExplain(btn){const bar=btn.closest('.explainbar');const out=bar.querySelector('.explain-out');
 const tr=btn.dataset.tr;bar.querySelectorAll('.explain-btn').forEach(b=>b.disabled=true);
 out.hidden=false;out.innerHTML='<span class="muted small">working…</span>';
 try{const r=await api('/api/explain',{method:'POST',body:{trace_id:tr}});
  if(r.error){out.innerHTML='<span class="muted small">'+esc(r.error)+'</span>'}
  else{out.innerHTML='<div class="explanation">'+esc(r.explanation||'').replace(/\n/g,'<br>')+'</div>'+
   '<div class="muted small">explained by '+esc(r.model_name||'')+(r.cost?' &middot; '+money(r.cost):'')+'</div>';
   const t=curThread&&curThread();if(t&&t.turns&&t.turns[+bar.dataset.i])t.turns[+bar.dataset.i].explained=true;}}
 catch(e){out.innerHTML='<span class="muted small">explain failed</span>'}
 finally{bar.querySelectorAll('.explain-btn').forEach(b=>b.disabled=false)}}
// T47 — the agent's tool steps (why.steps on an agent-run answer): one line per
// tool checked, "Checked <tool> · <n> results". KF.streamStep(step) appends the
// same line live while the answer is in flight (the streaming route belongs to
// another track; baked answers render from why.steps).
function stepCount(s){if(typeof s.results==='number')return s.results;if(Array.isArray(s.results))return s.results.length;
 for(const k of ['count','n','hits','rows'])if(typeof s[k]==='number')return s[k];
 if(Array.isArray(s.citations))return s.citations.length;if(Array.isArray(s.result))return s.result.length;return null}
function stepLine(s,live){s=s||{};const tool=s.tool||s.name||s.step||'tool';const n=stepCount(s);const extra=s.error?' · '+esc(s.error):(s.note?' · '+esc(s.note):'');
 return '<div class="st'+(live?' live':'')+'" title="'+esc(s.query||s.args?JSON.stringify(s.query||s.args):'')+'">Checked <b>'+esc(tool)+'</b>'+extra+
  '<span class="cnt">'+(n==null?'':num(n)+' result'+(n===1?'':'s'))+'</span></div>'}
function stepsHtml(a){const steps=((a||{}).why||{}).steps;if(!Array.isArray(steps)||!steps.length)return '';
 return '<div class="steps">'+steps.map(s=>stepLine(s)).join('')+'</div>'}
KF.streamStep=function(step){let box=$('#live-steps .steps');
 if(!box){const last=$$('#messages .msg.ai').pop();if(!last)return null;box=last.querySelector('.steps');
  if(!box){box=document.createElement('div');box.className='steps';last.insertBefore(box,last.querySelector('.kwrap')?last.querySelector('.kwrap').nextSibling:last.firstChild)}}
 const wait=box.querySelector('.st.wait');if(wait)wait.remove();
 box.insertAdjacentHTML('beforeend',stepLine(step,true));const m=$('#messages');if(m)m.scrollTop=m.scrollHeight;return box}
function liveTurn(q){const box=$('#messages');if(box.querySelector('.empty-chat'))box.innerHTML='';
 box.insertAdjacentHTML('beforeend','<div class="turn" id="live-turn"><div class="msg user">'+esc(q)+'</div>'+
  '<div class="msg ai live" id="live-steps"><div class="steps"><div class="st wait live">Checking the fabric…</div></div></div></div>');box.scrollTop=box.scrollHeight}
function dropLiveTurn(){const t=$('#live-turn');if(t)t.remove()}
// T27 — the designation/persona lens. Same grounded answer, framed for the
// reader's org role, decided by the SIGNED-IN identity (the designation the
// admin captured), never picked here. A reader with no designation sees the
// clean answer (no strip); a developer/tester/delivery/exec see a persona strip;
// a curator the governance frame; an admin the operations frame.
const LENS_TAG={builder:'Developer view',quality:'Quality view',delivery:'Delivery view',
 executive:'Executive view',curation:'Curator view',operations:'Admin view'};
function metaChip(k,v){return '<span class="rm"><i>'+esc(k)+'</i> '+esc(String(v))+'</span>'}
function roleLens(a){const rv=a.role_view;if(!rv||rv.lens==='answer')return '';
 const tag=LENS_TAG[rv.lens]||'View';
 if(rv.lens==='curation'){const meta=[metaChip('Grounding',pct(rv.grounding)),metaChip('Sources',rv.sources),
   rv.authoritative?metaChip('Authority','✓'):''].join('');
  return '<div class="rlens curation"><span class="rtag">'+tag+'</span><span class="rnote">'+esc(rv.note||'')+'</span>'+
   '<div class="rmeta">'+meta+'</div>'+(rv.gap_hint?'<div class="rgap">'+esc(rv.gap_hint)+'</div>':'')+'</div>'}
 if(rv.lens==='operations'){const meta=[metaChip('Level',rv.level),metaChip('Model',rv.model||'—'),
   metaChip('Cost','$'+Number(rv.cost||0).toFixed(4)),rv.cache_hit?metaChip('Cache','hit'):'',
   metaChip('Tokens',(rv.tokens_in||0)+'/'+(rv.tokens_out||0))].join('');
  return '<div class="rlens operations"><span class="rtag">'+tag+'</span><span class="rnote">'+esc(rv.note||'')+'</span>'+
   '<div class="rmeta">'+meta+'</div></div>'}
 // persona strips (builder / quality / delivery / executive): the framing note
 // plus how the answer was pitched (depth + what it emphasised).
 const DEPTH={headline:'headline',brief:'brief',full:'full detail'};
 const EMPH={code:'implementation',test:'tests & coverage',authority:'authoritative source'};
 const meta=[metaChip('Pitched',DEPTH[rv.depth]||rv.depth),
   rv.emphasis&&rv.emphasis!=='none'?metaChip('Emphasis',EMPH[rv.emphasis]||rv.emphasis):''].join('');
 return '<div class="rlens '+esc(rv.lens)+'"><span class="rtag">'+tag+'</span><span class="rnote">'+esc(rv.note||'')+'</span>'+
   '<div class="rmeta">'+meta+'</div></div>'}
// Render an answer body: prose with inline citation chips, plus fenced code
// blocks (```lang … ```) rendered verbatim for code answers (T25). A code
// citation links straight to the exact lines on GitHub; a document citation
// opens the passage viewer (L2.2).
function renderAnswer(a){const cs=a.citations||[];
 const parts=String(a.answer_text||'').split('```');
 let html='';
 for(let i=0;i<parts.length;i++){
  if(i%2===1){ // fenced code segment; first line may name the language
   const seg=parts[i], nl=seg.indexOf('\n');
   const code=nl>=0?seg.slice(nl+1):seg;
   html+='<pre class="codeblock"><code>'+esc(code.replace(/\s+$/,''))+'</code></pre>';
  }else{ html+=chipify(parts[i],cs); }
 }
 return html}
function chipify(text,cs){return esc(text).replace(/\[(\d+)\]/g,(m,n)=>{const c=cs[+n-1];if(!c)return '';
  const url=((c.coordinate||{}).locator||{}).url||'';
  const label=esc(c.document_title)+' &middot; '+esc(c.coordinate_render);
  if(url)return '<a class="chipcite gh" href="'+esc(url)+'" target="_blank" rel="noopener" title="Open on GitHub">'+label+' &#8599;</a>';
  return '<span class="chipcite" data-cite="'+esc(n)+'">'+label+'</span>'})}
function wireCites(){const t=curThread();if(!t)return;
 $$('#messages span.chipcite').forEach(el=>el.onclick=ev=>{ev.stopPropagation();
  const turn=t.turns[+el.closest('.msg.ai').dataset.i];openPage((turn.a.citations||[])[+el.dataset.cite-1])})}
// L6 — a reader flags an answer; 👎 records negative feedback for the curators.
function wireFeedback(){const t=curThread();if(!t)return;
 $$('#messages .fbbar').forEach(bar=>{const turn=t.turns[+bar.dataset.i];if(!turn)return;
  const done=v=>{bar.querySelector('.fbmsg').textContent=v==='down'?'Thanks — flagged for the curators.':'Thanks for the feedback.';
   bar.querySelectorAll('.fbbtn').forEach(b=>b.disabled=true)};
  bar.querySelector('.up').onclick=e=>{e.stopPropagation();done('up')};
  bar.querySelector('.down').onclick=e=>{e.stopPropagation();
   api('/feedback',{method:'POST',body:{question:turn.q,trace_id:turn.a.trajectory_id,
    level:(turn.a.why||{}).level_name||'',verdict:'down'}}).catch(()=>{});done('down')}})}

// ===================== the card (L2.3) ===============================
function bars(sig){const order=['retrieval','semantic','coverage','agreement','resolvable'];
 return '<span class="bars" title="grounding signals: '+order.join(', ')+'">'+
  order.map(k=>'<i class="'+(((sig||{})[k]||0)>=0.5?'on':'')+'" title="'+k+' '+pct((sig||{})[k])+'"></i>').join('')+'</span>'}
function reasonWord(a){const r=((a.why||{}).reasons||[]).map(x=>x.code);
 return r.indexOf('confidence_fail')>=0?'Yes — escalated after a confidence check':'No'}
function modelLabel(a){const m=a.model_name||'';
 // T35: the id from the provider's response, or "No model needed" when the
 // extractive core answered. Only the explicit test double reads "demo model".
 if(!m||/^none|extractive|off$/i.test(m))return 'No model needed';
 return /mock|echo|demo/i.test(m)?'demo model':esc(m)}
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
 // T44/T45 — an answer served from answers/<hash>.json shows where it came
 // from and the model + cost recorded in the file.
 if(a.baked)rows.push(['Full answer','<span class="pill info">'+esc(a.baked.source==='queue'?'ask queue':'bake')+'</span> '+
   esc(a.baked.model||'no model')+' &middot; '+money(a.baked.cost_usd)+(a.baked.steps?' &middot; '+a.baked.steps+' step(s)':'')]);
 rows.push(['Moved levels',esc(reasonWord(a))]);
 // --- under Details ---
 const det=[];
 det.push(['Graph',(gx.activated!=null?gx.activated:0)+' lit &middot; '+(gx.relationships!=null?gx.relationships:(gx.edges||0))+' links &middot; '+(gx.hops!=null?gx.hops:0)+' hop(s) &middot; '+cited+' docs cited']);
 det.push(['Sources',found+' found &middot; '+cited+' cited']);
 det.push(['Trust','<span class="trust-num">'+trust+'</span> '+bars(w.signals)+' <span class="muted small" title="grounding score">g '+pct(a.grounding_score)+'</span>']);
 det.push(['Language','<span class="pill">'+esc((a.lang||'en').toUpperCase())+'</span>']);
 det.push(['Tokens',num(a.tokens_in)+' in &middot; '+num(a.tokens_out)+' out &middot; cache read —']);
 det.push(['Cost',money(a.cost)+' &middot; top '+money(topCost)+' &middot; saved '+money(a.cost_saved)]);
 det.push(['Cache',a.cache_hit?'<span class="pill good">hit</span>':'<span class="pill">miss</span>']);
 det.push(['Timing',a._ms!=null?ms(a._ms):'—']);
 if(a.timing)det.push(['Phases',phaseBar(a.timing)]);
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
// T56 — the phase / active-idle bar: where the answer's wall-clock went
// (retrieve is idle wall time; summarise/translate are active model work).
const PHASE_COLOR={retrieve:'#0096FF',summarise:'#7048E8',agent_steps:'#F53E5A',translate:'#0CA678',image:'#F59F00'};
function phaseBar(t){const pm=t.phase_ms||{};const total=Number(t.total_ms)||0;
 if(!total)return '<span class="muted small">—</span>';
 const order=['retrieve','summarise','agent_steps','translate','image'];
 const segs=order.filter(k=>pm[k]).map(k=>'<i title="'+k+' '+Math.round(pm[k])+' ms" style="width:'+
   Math.max(2,pm[k]/total*100).toFixed(1)+'%;background:'+(PHASE_COLOR[k]||'#7C8DA1')+'"></i>').join('');
 const act=Number(t.active_ms)||0,idle=Number(t.idle_ms)||0;
 return '<div class="phasebar">'+segs+'</div>'+
  '<div class="muted small" style="margin-top:3px">active '+Math.round(act)+' ms &middot; idle '+
   Math.round(idle)+' ms'+(t.tool_calls?' &middot; '+t.tool_calls+' tool call(s)':'')+'</div>';}
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
function selectAnswer(turn){const a=turn.a;LAST_A=a; // read-aloud speaks the answer in view
 $$('#messages .msg.ai').forEach(el=>el.classList.remove('sel'));
 const el=$('#messages .msg.ai[data-i="'+turn._i+'"]');if(el)el.classList.add('sel');
 card(a);loadGalaxy(a.trajectory_id,a);loadUsage()}
let GALAXY=null;  // the mounted KFGalaxy handle for the current answer (T51)
async function loadGalaxy(trace_id,a){const box=$('#galaxy'),st=$('#galaxy-stats');
 if(!trace_id){box.innerHTML='<div class="gx-empty">Ask a question to light up the graph.</div>';st.innerHTML='';return}
 try{const g=await api('/api/galaxy?trace_id='+encodeURIComponent(trace_id));
  CARD_GX[trace_id]=g.stats||{};if(a)card(a);
  renderGalaxy(g);
  const s=g.stats||{};st.innerHTML='<span><b>'+(s.activated||0)+'</b> lit</span><span><b>'+(s.halo||0)+
   '</b> halo</span><span><b>'+(s.relationships!=null?s.relationships:(s.edges||0))+
   '</b> links</span><span><b>'+(s.nodes||0)+'</b> concepts</span>';}
 catch(e){box.innerHTML='<div class="gx-empty">Galaxy unavailable for this answer.</div>';st.innerHTML=''}}
// The real-physics galaxy (T51): mount the vendored vis-network view when it is
// present (the Workspace <head> pulls it same-origin), flash the activated
// nodes, and open a concept side-sheet on click. Falls back to the inline SVG
// force layout when the library is absent (e.g. a stripped bake).
function renderGalaxy(g){const box=$('#galaxy');
 if(window.KFGalaxy&&typeof vis!=='undefined'){
  box.style.height=box.style.height||'300px';
  try{
   GALAXY=window.KFGalaxy.mount(box,g,{onNode:openNode});
   const lit=(g.activated_ids||[]);
   if(lit.length)setTimeout(function(){try{GALAXY.flash(lit)}catch(e){}},120);
   return;
  }catch(e){/* fall through to the SVG renderer */}
 }
 renderGalaxySVG(g);}
// One concept's side-sheet from /api/galaxy/node — its type, doc count and the
// passages that mention it, reusing the page drawer.
async function openNode(id){if(!id)return;const d=$('#page-drawer');
 try{const n=await api('/api/galaxy/node?id='+encodeURIComponent(id));if(!n||!n.id)return;
  d.innerHTML='<button class="btn sm right" id="page-close">Close</button>'+
   '<h3>'+esc(n.name||'Concept')+'</h3>'+
   '<div class="pagemeta"><span class="pill">'+esc(n.type||'Concept')+'</span> &middot; '+(n.docs||0)+' document(s)</div>'+
   (n.passages||[]).map(function(p){return '<div class="pagedoc" style="margin-top:8px">'+esc(p.text||'')+'</div>'}).join('')+
   ((n.passages||[]).length?'':'<p class="muted small" style="margin-top:10px">No passages mention this concept directly.</p>');
  d.classList.remove('hidden');$('#page-close').onclick=function(){d.classList.add('hidden')};}
 catch(e){/* a node with no detail simply does not open a sheet */}}
function renderGalaxySVG(g){const box=$('#galaxy');const nodes=(g.nodes||[]).slice(0,60);
 if(!nodes.length){box.innerHTML='<div class="gx-empty">No graph relationships were used for this answer.</div>';return}
 // the new payload marks activation as an id list + from/to edges; project it
 // onto the fields this inline renderer expects so the fallback still lights up.
 const lit={};(g.activated_ids||[]).forEach(id=>lit[id]=1);
 const idx={};nodes.forEach((n,i)=>idx[n.id]=i);
 const edges=(g.edges||[]).map(e=>({src:e.from,dst:e.to})).filter(e=>idx[e.src]!=null&&idx[e.dst]!=null);
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
 edges.forEach(e=>{const a=P[idx[e.src]],b=P[idx[e.dst]];const act=lit[e.src]&&lit[e.dst];
  svg+='<line class="gx-edge'+(act?' act':'')+'" x1="'+X(a.x).toFixed(1)+'" y1="'+Y(a.y).toFixed(1)+
   '" x2="'+X(b.x).toFixed(1)+'" y2="'+Y(b.y).toFixed(1)+'" stroke-width="'+(act?1.2:0.7)+
   '" stroke-opacity="'+(act?0.9:0.04)+'"/>'});
 // degree per node, so only the most-connected activated nodes get a label
 // (a compact galaxy stays legible instead of a wall of overlapping text).
 const deg={};edges.forEach(e=>{deg[e.src]=(deg[e.src]||0)+1;deg[e.dst]=(deg[e.dst]||0)+1});
 const labelled=new Set(nodes.filter(n=>lit[n.id]).sort((a,b)=>(deg[b.id]||0)-(deg[a.id]||0)).slice(0,9).map(n=>n.id));
 nodes.forEach((n,i)=>{const p=P[i];svg+='<circle class="gx-node'+(lit[n.id]?' act flash':'')+'" cx="'+X(p.x).toFixed(1)+
   '" cy="'+Y(p.y).toFixed(1)+'" r="'+(lit[n.id]?4.2:2.4)+'"><title>'+esc(n.label)+'</title></circle>';
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
 html+='<div class="kv"><span>Speech seconds</span><b><span id="speech-secs">'+(SPEECH_SECS?SPEECH_SECS+'s':'—')+'</span> <span class="muted small">(with voice)</span></b></div>';
 // T45 — the reader's queued questions: the server's list (answers baked by
 // the queue for this subject) merged with what this browser queued.
 const seen={},queued=[];
 (USAGE.queued||[]).concat(QUEUE).forEach(x=>{const k=x.hash||norm(x.question);if(!k||seen[k])return;seen[k]=1;queued.push(x)});
 if(queued.length){html+='<div class="kv" style="margin-top:10px"><span>Queued questions</span><b>'+queued.length+'</b></div><ul class="queue-list">'+
  queued.slice(0,8).map(x=>'<li><span class="pill '+(x.status==='answered'?'good':'warn')+'">'+esc(x.status||'answered')+'</span> '+esc(x.question||'')+
   (x.model?' <span class="muted small">'+esc(x.model)+' &middot; '+money(x.cost_usd)+'</span>':'')+'</li>').join('')+'</ul>';}
 box.innerHTML=html;
 $$('#usage-body .win button').forEach(bt=>bt.onclick=()=>{USE_WIN=bt.dataset.w;renderUsage()})}

// ===================== ask ==========================================
async function samples(){if(!$('#samples'))return;let qs=[],known=[],persona='';
 // T82 — a reader's Home leads with the questions the fabric answers INSTANTLY
 // for their persona (the known-question registry), then the corpus suggestions.
 if(KF.session){try{const j=await api('/api/suggestions');
   qs=(j.suggestions||[]).map(s=>s.question).slice(0,6);
   known=(j.known||[]).map(s=>s.question).slice(0,6);persona=j.persona||''}catch(e){}}
 if(!qs.length&&!known.length)qs=(KF.DIR.questions&&KF.DIR.questions['qualizeal'])||[];
 const chip=q=>'<span class="chip" data-q="'+esc(q)+'">'+esc(q)+'</span>';
 const group=(label,list)=>list.length?'<div class="samples-group"><div class="samples-label">'+esc(label)+
   '</div><div class="samples-row">'+list.map(chip).join('')+'</div></div>':'';
 $('#samples').innerHTML=group('Answered instantly'+(persona?' · '+persona:''),known)+
   group(known.length?'More from your corpus':'Try asking',qs);
 $$('#samples .chip').forEach(c=>c.onclick=()=>{$('#question').value=c.dataset.q;$('#question').focus()})}
async function ask(){const q=$('#question').value.trim();if(!q)return;
 if(!KF.session){gate({status:401,message:''},'asker');return}
 if(!curThread())newThread();
 // conversation context so follow-ups ("when was it made?") resolve the pronoun
 // to the topic in view instead of dropping to "outside the knowledge base".
 // rich two-turn context (T26): each turn carries its subject/kind/cited docs
 // and any clarify options, so a follow-up ("what about its pricing", "and for
 // testers?", "the second one") resolves — or asks back — server- and client-side.
 const th=curThread();const turns=(th?th.turns:[]).slice(-2).map(tn=>({
   question:tn.q,kind:(tn.a&&tn.a.kind)||'answer',
   answer_docs:((tn.a&&tn.a.citations)||[]).map(c=>c.document_title).filter(Boolean),
   options:(tn.a&&tn.a.suggestions)||[]}));
 const ctx={turns,history:turns.map(t=>t.question)};
 const btn=$('#ask-btn');btn.disabled=true;$('#ask-status').textContent='thinking…';const t0=performance.now();
 liveTurn(q);  // T47: the in-flight bubble KF.streamStep appends "Checked <tool>" lines to
 try{const a=await api('/ask',{method:'POST',body:{question:q,context:ctx}});gate(null);
  a._ms=performance.now()-t0;dropLiveTurn();
  const t=curThread();const turn={q,a};t.turns.push(turn);turn._i=t.turns.length-1;
  if(t.turns.length===1)t.title=q.slice(0,48);
  t.at=Date.now();saveThreads();renderThreads();renderMessages();
  turn._i=t.turns.length-1;selectAnswer(turn);
  $('#ask-status').textContent=Math.round(a._ms)+' ms · '+(a.kind||'');$('#question').value='';autosize()}
 catch(e){dropLiveTurn();gate(e,'asker');$('#ask-status').textContent='failed';toast(e.message,'bad')}
 finally{btn.disabled=false}}

// ===================== corpus strip =================================
function animateNumber(el,target,msdur){target=Number(target)||0;const from=Number(String(el.textContent).replace(/[^0-9]/g,''))||0;
 const t0=performance.now();const dur=msdur||600;
 (function step(t){const p=Math.min(1,(t-t0)/dur);el.textContent=num(Math.round(from+(target-from)*(1-Math.pow(1-p,3))));if(p<1)requestAnimationFrame(step)})(performance.now())}
// T47 — ten tiles: the five corpus counts plus repositories, Jira projects,
// Confluence spaces, tables and images (from the fabric-data files).
const TILE_KEYS=['documents','passages','entities','relationships','domains',
 'repositories','jira_projects','confluence_spaces','tables','images'];
function renderTiles(c){c=c||{};TILE_KEYS.forEach(k=>{const el=$('#tile-'+k);if(el)animateNumber(el,c[k]||0)})}
async function corpusStrip(){if(!KF.session)return;
 try{renderTiles(await api('/api/corpus'))}catch(e){}}

// ===================== composer wiring ==============================
function autosize(){const t=$('#question');t.style.height='auto';t.style.height=Math.min(150,t.scrollHeight)+'px'}
$('#ask-btn').onclick=ask;
$('#question').addEventListener('input',autosize);
$('#question').addEventListener('keydown',e=>{if(e.key==='Enter'&&!e.shiftKey){e.preventDefault();ask()}});
$('#new-chat').onclick=newThread;

// ===================== voice: read answers aloud + dictate questions =
// Browser-native Web Speech API — no server and no key, so it works on the
// static showcase and the live product alike. Buttons stay disabled on
// browsers that lack the API, with a title that says why.
const LANG_BCP={en:'en-US',fr:'fr-FR',es:'es-ES',ja:'ja-JP'};
function spokenText(a){if(!a)return '';
 if(a.kind==='answer')return String(a.answer_text||'').replace(/\[\d+\]/g,'');   // drop [1] markers
 if(a.kind==='clarify')return a.clarify_back||DECLINE;return DECLINE}
function voiceLang(a){const sel=($('#answer-lang')||{}).value;
 if(sel&&sel!=='auto')return LANG_BCP[sel]||'en-US';
 return LANG_BCP[(a&&a.lang)||'en']||navigator.language||'en-US'}
function paintSpeech(){const el=$('#speech-secs');if(el)el.textContent=SPEECH_SECS?SPEECH_SECS+'s':'—'}

function setupReadAloud(){const btn=$('#read-aloud');if(!btn)return;
 if(!('speechSynthesis'in window)){btn.title='Read-aloud needs a browser with speech synthesis (e.g. Chrome).';return}
 btn.disabled=false;btn.title='Read the answer aloud';
 btn.onclick=()=>{
  if(SPEAKING){window.speechSynthesis.cancel();SPEAKING=false;btn.classList.remove('on');return}
  const txt=spokenText(LAST_A);if(!txt){toast('Ask something first — then I can read it aloud.','warn');return}
  const u=new SpeechSynthesisUtterance(txt);u.lang=voiceLang(LAST_A);u.rate=1.02;const t0=performance.now();
  u.onend=u.onerror=()=>{SPEAKING=false;btn.classList.remove('on');
   SPEECH_SECS+=Math.max(1,Math.round((performance.now()-t0)/1000));paintSpeech()};
  window.speechSynthesis.cancel();window.speechSynthesis.speak(u);SPEAKING=true;btn.classList.add('on')}}

function setupMic(){const btn=$('#mic-btn');if(!btn)return;
 const SR=window.SpeechRecognition||window.webkitSpeechRecognition;
 if(!SR){btn.title='Voice input needs a browser with speech recognition (e.g. Chrome).';return}
 btn.disabled=false;btn.title='Dictate your question';let rec=null,listening=false;
 btn.onclick=()=>{
  if(listening&&rec){rec.stop();return}
  rec=new SR();rec.lang=voiceLang(LAST_A);rec.interimResults=true;rec.maxAlternatives=1;let final='';
  rec.onstart=()=>{listening=true;btn.classList.add('on');$('#ask-status').textContent='listening…'};
  rec.onresult=e=>{let interim='';for(let i=e.resultIndex;i<e.results.length;i++){
    const r=e.results[i];if(r.isFinal)final+=r[0].transcript;else interim+=r[0].transcript}
   $('#question').value=(final+interim).trim();autosize()};
  rec.onerror=e=>{listening=false;btn.classList.remove('on');
   $('#ask-status').textContent=e.error==='not-allowed'?'microphone blocked':'voice error'};
  rec.onend=()=>{listening=false;btn.classList.remove('on');
   const q=$('#question').value.trim();$('#ask-status').textContent='ready';if(q)ask()};
  try{rec.start()}catch(e){/* a start already in flight */}}}

// ===================== session lifecycle ============================
// T52 — the honest provider badge: which model answers (the pinned provider,
// or the open-source fallback when it is unavailable, or the extractive core).
async function loadProvider(){const el=$('#provider-badge');if(!el)return;
 try{const p=await api('/api/provider');if(!p||!p.label){el.classList.remove('on');return}
  el.innerHTML='<span class="dot" style="background:'+esc(p.dot||'#5A6B7C')+'"></span>'+esc(p.label);
  el.classList.add('on');}
 catch(e){el.classList.remove('on')}}
function boot(){loadThreads();loadQueue();if(!THREADS.length){CUR=null}else{CUR=THREADS[0].id}
 renderThreads();renderMessages();
 if(KF.session){corpusStrip();loadUsage();loadProvider()}else{gate({status:401,message:''},'asker')}}
window.KF_ON_SESSION=s=>{gate(null);if(s){corpusStrip();loadUsage();samples();loadProvider();$('#ask-status').textContent='ready for '+s.subject}
 else{$('#usage-body').innerHTML='<div class="placeholder">Sign in to see your usage.</div>'}};
KF.initBar({preferRole:'asker'});boot();setupReadAloud();setupMic();
