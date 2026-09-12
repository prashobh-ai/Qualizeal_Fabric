/* QualiZeal Knowledge Fabric — static showcase engine.
 *
 * The showcase is the REAL product surfaces (Workspace, Admin, Curator,
 * Sign-in, Telemetry) served as static files on GitHub Pages. There is no
 * server, so this engine intercepts window.fetch and answers every API call
 * from a pre-baked snapshot.json — the actual JSON the real Python endpoints
 * returned at build time, per role. POST mutations (sign-in, ask, curator
 * decisions, add/remove user, feedback) update a client-side copy of that
 * state so the demo is genuinely interactive.
 *
 * Loaded BEFORE the shared runtime on every surface page. It also derives the
 * deploy base from the URL and sets window.KF_BASE / window.KF_ROUTES so the
 * one build serves any base path (Pages repo path, localhost, an AWS subpath).
 *
 * Serving order for POST /ask (T44/T45):
 *   1. a BAKED answer — answers/<hash>.json, hash = sha256(norm(question))[:16],
 *      fetched over the REAL network (the bake at ingest, or the ask queue);
 *   2. in-browser retrieval and facts — the snapshot's baked bank, then BM25
 *      over the exported index (Level 1/2 extractive);
 *   3. the ask queue — a Level 2/3 or gap answer with no baked file is stamped
 *      `queue: {eligible, hash, path}` so the Workspace offers "Get full
 *      answer" (an `ask` issue) and polls answers/<hash>.json for the result.
 * /answers/*.json and static assets pass through to the real fetch untouched.
 */
(function () {
  "use strict";
  var SURFACE = window.KF_SURFACE || ""; // 'workspace' | 'admin' | 'curator' | 'signin' | 'dashboard' | '' (landing)

  // ---- deploy base + folder routing ------------------------------------
  var pn = location.pathname.replace(/\/index\.html$/, "");
  var base = SURFACE ? pn.replace(new RegExp("/" + SURFACE + "/?$"), "") : pn.replace(/\/$/, "");
  window.KF_BASE = base;
  // the app's internal paths map onto the showcase's folder layout.
  window.KF_ROUTES = {
    "/": "/workspace", "/ask": "/workspace", "/signin": "/signin",
    "/admin": "/admin", "/curator": "/curator", "/dashboard": "/dashboard"
  };

  var realFetch = window.fetch.bind(window);
  var SNAP = null, STATE = null, TOKEN2SUBJECT = {}, SESSION_LOGINS = {};

  // Sign-in policy for the showcase (client-side; the corporate directory
  // replaces it in the live product). Elevated accounts need their password;
  // anyone with a QualiZeal address signs in by default (single sign-on).
  var SSO_DOMAIN = "@qualizeal.com";
  var SEED = {
    "admin@qualizeal.com": { pw: "kf@qz2026", roles: ["admin"], scopes: ["public", "restricted"], designation: "Platform Admin" },
    "curator@qualizeal.com": { pw: "kf@qz2026", roles: ["curator"], scopes: ["public", "restricted"], designation: "Knowledge Curator" }
  };
  // T27 — demo designations: signing in with one of these QualiZeal local-parts
  // (developer@qualizeal.com, cto@qualizeal.com, …) grants that org designation,
  // so the showcase can be signed in as each persona and watch the same question
  // come back pitched for it. In the live product the corporate directory
  // supplies the designation the admin captured at access-grant time.
  var DEMO_DESIGNATIONS = {
    developer: "Developer", dev: "Developer", engineer: "Software Engineer",
    architect: "Solution Architect", devops: "DevOps Engineer",
    tester: "QA Engineer", qa: "QA Engineer", qe: "QE Lead", sdet: "SDET",
    delivery: "Delivery Head", manager: "Engineering Manager", scrum: "Scrum Master",
    cto: "CTO", ceo: "CEO", director: "Director", vp: "VP Engineering"
  };
  var ready = realFetch(base + "/snapshot.json")
    .then(function (r) { return r.json(); })
    .then(function (s) { SNAP = s; initState(s); })
    .catch(function () { SNAP = {}; STATE = { get: {}, usage: {}, feedback: [] }; });

  function clone(o) { return o == null ? o : JSON.parse(JSON.stringify(o)); }

  function initState(s) {
    var baked = clone((s.get && s.get.admin && s.get.admin["/admin/users"]) || { users: [] });
    baked.users = baked.users || [];
    // Users an admin added persist per browser (no server on Pages), so a
    // promoted admin/curator can sign back in after a reload.
    var stored = loadUsers();
    if (stored) {
      stored.forEach(function (u) {
        if (u && u.subject && !baked.users.some(function (x) { return x.subject === u.subject; })) {
          baked.users.push(u);
        }
      });
    }
    STATE = {
      get: clone(s.get) || {},
      usage: clone(s.usage) || {},
      users: baked,
      feedback: loadFeedback()
    };
    if (STATE.get.admin) STATE.get.admin["/admin/users"] = STATE.users;
    Object.keys(s.login || {}).forEach(function (subj) {
      var l = s.login[subj]; if (l && l.token) TOKEN2SUBJECT[l.token] = subj;
    });
  }

  // ---- feedback (negative-feedback review queue) — per browser ----------
  function loadFeedback() {
    try { return JSON.parse(localStorage.getItem("kf.feedback") || "[]"); } catch (e) { return []; }
  }
  function saveFeedback() {
    try { localStorage.setItem("kf.feedback", JSON.stringify(STATE.feedback.slice(0, 200))); } catch (e) {}
  }

  // ---- admin-managed users (add / promote) — per browser ----------------
  function loadUsers() {
    try { return JSON.parse(localStorage.getItem("kf.users") || "null"); } catch (e) { return null; }
  }
  function saveUsers(box) {
    try { localStorage.setItem("kf.users", JSON.stringify((box.users || []).slice(0, 500))); } catch (e) {}
  }

  // ---- helpers ----------------------------------------------------------
  function subjectOf(token) {
    if (!token) return "";
    return TOKEN2SUBJECT[token] || (token.indexOf("demo-") === 0 ? token.slice(5) : "");
  }
  function loginOf(subject) { return SESSION_LOGINS[subject] || (SNAP.login || {})[subject] || null; }

  // Register a signed-in identity so downstream requests resolve its role/scope.
  function mint(subject, roles, scopes, designation) {
    var token = "demo-" + subject;
    var l = { token: token, subject: subject, roles: roles, scopes: scopes, designation: designation || "" };
    SESSION_LOGINS[subject] = l;
    TOKEN2SUBJECT[token] = subject;
    return l;
  }

  // Sign-in rules, in order: baked demo subjects (showcase quick picker) →
  // elevated seeded accounts (exact password) → admin-promoted users →
  // any QualiZeal address (single sign-on, asker) → unknown.
  function resolveLogin(rawSubject, password) {
    var subject = (rawSubject || "").trim().toLowerCase();
    password = password || "";
    if (!subject) return { code: 400 };
    if ((SNAP.login || {})[subject]) return { login: (SNAP.login)[subject] };
    if (SEED[subject]) {
      var seed = SEED[subject];
      return password === seed.pw ? { login: mint(subject, seed.roles, seed.scopes, seed.designation) } : { code: 401 };
    }
    var promoted = (STATE.users && STATE.users.users || []).filter(function (u) {
      return (u.subject || "").toLowerCase() === subject;
    })[0];
    if (promoted) return { login: mint(subject, promoted.roles || ["asker"], promoted.scopes || ["public"], promoted.designation || "") };
    if (subject.slice(-SSO_DOMAIN.length) === SSO_DOMAIN) {
      var local = subject.slice(0, subject.length - SSO_DOMAIN.length);
      return { login: mint(subject, ["asker"], ["public"], DEMO_DESIGNATIONS[local] || "") };
    }
    return { code: 404 };
  }
  function bucketOf(subject) {
    var l = loginOf(subject), roles = (l && l.roles) || [];
    return roles.indexOf("admin") >= 0 ? "admin" : roles.indexOf("curator") >= 0 ? "curator" : "asker";
  }
  // T27 — the signed-in identity's org designation (from login, else the baked
  // directory row), and the persona it maps to.
  function designationOf(subject) {
    var l = loginOf(subject);
    if (l && l.designation) return l.designation;
    var users = (STATE.users && STATE.users.users) || [];
    var row = users.filter(function (u) { return u.subject === subject; })[0];
    return (row && row.designation) || "";
  }
  function scopeKey(subject) {
    var l = loginOf(subject), sc = (l && l.scopes) || [];
    return sc.indexOf("restricted") >= 0 ? "restricted" : "public";
  }
  function respond(obj, status) {
    return new Response(JSON.stringify(obj == null ? {} : obj),
      { status: status || 200, headers: { "Content-Type": "application/json" } });
  }
  function norm(q) { return String(q || "").toLowerCase().replace(/[^a-z0-9\s]/g, " ").replace(/\s+/g, " ").trim(); }
  function toks(q) { return norm(q).split(" ").filter(Boolean); }
  // question-word stopwords: a fuzzy match must share a real content token, not
  // just "what/is/does" — otherwise "what is validate" wrongly matches "what is
  // QMentisAI" and serves an answer to a different question.
  var STOP = { what: 1, is: 1, are: 1, a: 1, an: 1, the: 1, of: 1, to: 1, do: 1, does: 1,
    did: 1, how: 1, who: 1, when: 1, where: 1, which: 1, and: 1, or: 1, for: 1, "in": 1, on: 1, me: 1, tell: 1, about: 1 };
  function content(ts) { return ts.filter(function (t) { return !STOP[t]; }); }

  // ---- answer selection (exact, then nearest bank question) -------------
  // Exact key, else nearest baked question by token overlap (Jaccard). Returns
  // a cloned answer or null — no side effects, so coreference can retry it.
  function lookup(question) {
    var answers = SNAP.answers || {};
    var a = answers[norm(question)];
    if (!a) {
      var qt = toks(question), qc = content(qt), best = null, bestScore = 0;
      Object.keys(answers).forEach(function (k) {
        var kt = k.split(" "), setk = {}; kt.forEach(function (t) { setk[t] = 1; });
        var inter = 0; qt.forEach(function (t) { if (setk[t]) inter++; });
        // require at least one shared CONTENT token, so stopword-only overlap
        // ("what is") can never carry a match to an unrelated question.
        var shareContent = qc.some(function (t) { return setk[t]; });
        var uni = kt.length + qt.length - inter || 1, sc = inter / uni;
        if (shareContent && sc > bestScore) { bestScore = sc; best = k; }
      });
      if (best && bestScore >= 0.34) a = answers[best];
    }
    return a ? clone(a) : null;
  }

  // ---- follow-up coreference ("it/that" -> the topic in view) -----------
  // "When was it made?" after "What is QMentisAI?" must not fall straight to a
  // gap. Resolve the pronoun to the conversation's sticky subject, retry, and
  // if there's still no baked answer, ASK BACK (clarify) instead of declining.
  // ---- T26: two-turn context window + coreference resolution ------------
  // A deterministic, model-free mirror of answer/context.py. A follow-up rarely
  // repeats its subject ("what about its pricing", "and for testers?", "the
  // second one"); this rewrites it from the last one or two turns before
  // retrieval, or asks back when the reference is genuinely ambiguous. The
  // server and this browser engine resolve identically, so the static showcase
  // and the live product behave the same.
  var CX_TOKEN = /[a-z0-9]+/g;
  var CX_STOP = { the:1,a:1,an:1,of:1,to:1,"in":1,on:1,"for":1,and:1,or:1,is:1,are:1,
    what:1,which:1,how:1,who:1,when:1,where:1,does:1,do:1,did:1,was:1,with:1,that:1,
    "this":1,it:1,as:1,by:1,at:1,from:1,about:1,me:1,my:1 };
  var PRONOUN = /\b(it'?s?|its|they|them|their|theirs|the same|there|this|that|these|those|one|the (?:product|tool|platform|service|solution|offering))\b/i;
  var CX_ORDINAL = { first:0,"1st":0,second:1,"2nd":1,third:2,"3rd":2,last:-1 };
  var CX_INTENT_NO_ENTITY = /\b(compare|comparison|difference|differ|integrate|migrate)\b/i;
  var CX_HAS_VERB = /\b(is|are|was|were|do|does|did|has|have|can|will|should|use|uses|work|works|cost|costs|support|supports|provide|provides|run|runs|make|made|help|helps|handle|handles|mean|means|need|needs)\b/i;
  function cxToks(s) { return (String(s || "").toLowerCase().match(CX_TOKEN) || []); }
  function cxContent(s) { return cxToks(s).filter(function (t) { return !CX_STOP[t]; }); }
  function reEsc(s) { return String(s).replace(/[.*+?^${}()|[\]\\]/g, "\\$&"); }
  // The distinctive subject the text names (a product/entity token titling one
  // document), or "". Whole-token match — never a substring, so "testers" does
  // not match a "test" subject — latest mention wins.
  function subjectInText(text) {
    var subs = SNAP.subjects || {}, pos = {}, found = "", at = -1;
    cxToks(text).forEach(function (t, i) { pos[t] = i; });
    Object.keys(subs).forEach(function (k) {
      var i = (k in pos) ? pos[k] : -1; if (i > at) { at = i; found = k; }
    });
    return found;
  }
  function cxSticky(turns) {
    var win = (turns || []).slice(-2);
    for (var d = 0; d < win.length; d++) {
      var t = win[win.length - 1 - d], s = t.subject || subjectInText(t.question);
      if (s) return s;
    }
    return "";
  }
  function cxRecent(turns, n) {
    n = n || 3; var seen = {}, out = [], subs = SNAP.subjects || {};
    for (var i = (turns || []).length - 1; i >= 0; i--) {
      var t = turns[i], s = t.subject || subjectInText(t.question);
      if (s && !seen[s]) { seen[s] = 1; out.push(subs[s] || s); }
      if (out.length >= n) break;
    }
    return out;
  }
  // Rewrite `question` from the two-turn window, or ask back. Returns
  // {question, understood_as, clarify:{chips,reason}|null}.
  function resolveCtx(question, turns) {
    var subs = SNAP.subjects || {}, bank = Object.keys(subs).map(function (k) { return subs[k]; });
    var q = String(question || "").trim();
    turns = (turns || []).slice(-2);
    var here = subjectInText(q), lastT = turns[turns.length - 1];

    // 1) clarify option — the previous turn asked back and this answers it.
    if (lastT && lastT.kind === "clarify" && (lastT.options || []).length) {
      var qc = {}; cxContent(q).forEach(function (t) { qc[t] = 1; });
      for (var oi = 0; oi < lastT.options.length; oi++) {
        var opt = lastT.options[oi], oc = cxContent(opt), inter = 0;
        oc.forEach(function (t) { if (qc[t]) inter++; });
        var overlap = inter / (oc.length || 1);
        if (q.toLowerCase() === opt.toLowerCase() || (oc.length && overlap >= 0.6)) {
          var orig = lastT.question || opt;
          var rw0 = PRONOUN.test(orig) ? orig.replace(PRONOUN, opt) : opt;
          return { question: rw0, understood_as: rw0, clarify: null };
        }
      }
    }

    // 5) comparative continuation — "what about <entity>": swap the new entity
    // into the previous question. Checked before the self-contained short-circuit.
    if (lastT && /^(what about|how about|and)\b/i.test(q) && here) {
      var prevSub = subjectInText(lastT.question);
      if (prevSub && prevSub !== here) {
        var rwc = lastT.question.replace(new RegExp(reEsc(subs[prevSub]), "i"), subs[here]);
        if (rwc.toLowerCase() !== lastT.question.toLowerCase())
          return { question: rwc, understood_as: rwc, clarify: null };
      }
    }

    // A question that already names its own subject is self-contained.
    if (here) return { question: q, understood_as: null, clarify: null };

    var hasPronoun = PRONOUN.test(q);
    var isEllipsis = !CX_HAS_VERB.test(q) &&
      (/^(and|what about|how about|in |for |with |on )\b/i.test(q) || cxContent(q).length <= 3);
    var ordinalKey = null;
    Object.keys(CX_ORDINAL).forEach(function (k) {
      if (ordinalKey === null && new RegExp("\\b" + k + "\\b", "i").test(q)) ordinalKey = k;
    });
    var subjectKey = cxSticky(turns), subject = subs[subjectKey] || subjectKey;

    // 4) ordinal — resolve to the matching document of the last answer.
    if (ordinalKey !== null && lastT && (lastT.answer_docs || []).length) {
      var docs = lastT.answer_docs, idx = CX_ORDINAL[ordinalKey];
      if (-docs.length <= idx && idx < docs.length) {
        var doc = docs[(idx + docs.length) % docs.length], rwo = q + " (" + doc + ")";
        return { question: rwo, understood_as: rwo, clarify: null };
      }
    }

    // 2/3) pronoun or ellipsis — need a subject to resolve to.
    if (hasPronoun || isEllipsis) {
      if (subject) {
        var rwp;
        if (hasPronoun) rwp = q.replace(PRONOUN, subject);
        else {
          var tail = q.replace(/^(and|what about|how about)\b/i, "").replace(/^[\s?]+|[\s?]+$/g, "");
          rwp = (subject + " " + tail).trim();
        }
        return { question: rwp, understood_as: rwp, clarify: null };
      }
      var chips = cxRecent(turns); if (!chips.length) chips = bank.slice(0, 3);
      if (chips.length)
        return { question: q, understood_as: null, clarify: { chips: chips.slice(0, 3), reason: "Which one do you mean?" } };
    }

    // intent with no entity ("compare", "difference") and no subject → ask back.
    if (CX_INTENT_NO_ENTITY.test(q) && !subject) {
      var chips2 = cxRecent(turns); if (!chips2.length) chips2 = bank.slice(0, 3);
      if (chips2.length)
        return { question: q, understood_as: null, clarify: { chips: chips2.slice(0, 3), reason: "Compare which two?" } };
    }

    return { question: q, understood_as: null, clarify: null };
  }
  // Ambiguous reference → ask back with 2–3 subject chips (T26).
  function clarifyChips(chips, reason) {
    return {
      kind: "clarify", answer_text: "", clarify_back: reason, citations: [], confidence: 0,
      grounding_score: 0, trajectory_id: "traj_demo_clarify", cost: 0, tokens: 0, tier: "none",
      level: 0, lang: "en", cache_hit: false, cost_saved: 0, tokens_in: 0, tokens_out: 0,
      model_name: "demo model", complexity: "simple", authoritative_source: null,
      dataset_version: 1, reasoning: null, suggestions: chips || [],
      why: { level_name: "clarify", explain: reason, reasons: [],
             signals: { retrieval: 0, semantic: 0.4, coverage: 0, agreement: 0, resolvable: 1 }, retrieved: 0 }
    };
  }
  function clarifyAnswer(subKey, question, history) {
    var disp = (SNAP.subjects || {})[subKey] || subKey;
    var last = norm((history || [])[(history || []).length - 1] || "");
    var offer = ((SNAP.related || {})[subKey] || []).filter(function (q) { return norm(q) !== last; }).slice(0, 3);
    var text = "You're asking about " + disp + ", but the fabric doesn't have that specific detail yet.";
    text += offer.length
      ? " I can answer: " + offer.map(function (x) { return "“" + x + "”"; }).join(", ") + "."
      : " Try asking what it does, who it's for, or how it's tested.";
    return {
      kind: "clarify", answer_text: "", clarify_back: text, citations: [], confidence: 0,
      grounding_score: 0, trajectory_id: "traj_demo_clarify", cost: 0, tokens: 0, tier: "none",
      level: 0, lang: "en", cache_hit: false, cost_saved: 0, tokens_in: 0, tokens_out: 0,
      model_name: "demo model", complexity: "simple", authoritative_source: null,
      dataset_version: 1, reasoning: null, suggestions: offer,
      why: { level_name: "clarify", explain: "Recognised the topic (" + disp + "); needs a more specific question.",
             reasons: [], signals: { retrieval: 0, semantic: 0.4, coverage: 0, agreement: 0, resolvable: 1 }, retrieved: 0 }
    };
  }
  // ---- T24: real BM25 retrieval in the browser --------------------------
  // Baked answers are a Level-0 cache; anything not baked is retrieved live
  // from the exported index (BM25 + a code identifier tier) and either composed
  // extractively or, for a discovery question, returned as a ranked asset list —
  // so an unbaked question never falls straight to a blind gap.
  var STOP = { the:1,a:1,an:1,of:1,to:1,in:1,on:1,for:1,and:1,or:1,is:1,are:1,what:1,which:1,
    how:1,who:1,when:1,where:1,does:1,do:1,did:1,was:1,were:1,be:1,with:1,that:1,this:1,it:1,
    as:1,by:1,at:1,from:1,our:1,we:1,you:1,i:1,can:1,will:1,should:1,must:1,may:1,me:1,my:1 };
  var DISCOVERY = /\b(has|have)\s+(anyone|we|someone)\b|\b(is|are)\s+there\b|\bdo\s+we\s+have\b|\bcan\s+i\s+(find|reuse|use|get)\b|\bwhere\s+(can|do)\s+i\s+find\b|\bfind\s+(me\s+)?(a|an|the|some|any)\b|\blook(ing)?\s+for\b|\bsearch\s+for\b|\breus(e|able)\b|\bexamples?\s+of\b|\bexisting\b|\bany\s+(code|script|module|library|example)\b/i;
  function tokenize(s) { return (String(s || "").toLowerCase().match(/[a-z0-9]+/g) || []); }
  var RIDX = null;
  function ridx() {
    if (RIDX) return RIDX;
    var ix = SNAP.index || {}, ps = (ix.passages || []).map(function (p) {
      var tk = tokenize(p.idx || p.text), tf = {};
      tk.forEach(function (t) { tf[t] = (tf[t] || 0) + 1; });
      return { p: p, tf: tf, dl: tk.length };
    });
    RIDX = { ps: ps, df: ix.df || {}, N: ix.N || ps.length || 1, avgdl: ix.avgdl || 1,
             docs: {} };
    (ix.docs || []).forEach(function (d) { RIDX.docs[d.id] = d; });
    return RIDX;
  }
  function bm25(qt) {
    var R = ridx(), k1 = 1.5, b = 0.75, out = [];
    for (var i = 0; i < R.ps.length; i++) {
      var e = R.ps[i], sc = 0;
      for (var j = 0; j < qt.length; j++) {
        var f = e.tf[qt[j]]; if (!f) continue;
        var dft = R.df[qt[j]] || 1, idf = Math.log(1 + (R.N - dft + 0.5) / (dft + 0.5));
        sc += idf * (f * (k1 + 1)) / (f + k1 * (1 - b + b * e.dl / (R.avgdl || 1)));
      }
      if (e.p.kind === "code" || e.p.kind === "test") {
        var hay = (e.p.symbol + " " + e.p.path).toLowerCase();
        for (var m = 0; m < qt.length; m++) if (qt[m].length >= 3 && hay.indexOf(qt[m]) >= 0) sc += 3;
      }
      if (sc > 0) out.push({ p: e.p, score: sc });
    }
    out.sort(function (a, b) { return b.score - a.score; });
    return out;
  }
  function docTitle(id) { var d = ridx().docs[id]; return d ? d.title : ""; }
  function citeFor(p) {
    var loc = { path: p.path, symbol: p.symbol, coord: p.coord };
    if (p.url) loc.url = p.url;
    return {
      document_id: p.doc, document_title: docTitle(p.doc) || p.path || "document",
      coordinate: { kind: p.url ? "symbol_line" : "page_paragraph", locator: loc },
      coordinate_render: p.coord || "", passage_id: "", snippet: (p.text || "").slice(0, 200)
    };
  }
  function bestSentence(text, qt) {
    var sents = String(text || "").split(/(?<=[.!?])\s+/).filter(function (s) { return s.trim().length > 24; });
    if (!sents.length) return String(text || "").slice(0, 200);
    var set = {}; qt.forEach(function (t) { set[t] = 1; });
    var best = sents[0], bs = -1;
    sents.forEach(function (s) {
      var ov = 0; tokenize(s).forEach(function (t) { if (set[t]) ov++; });
      if (ov > bs) { bs = ov; best = s; }
    });
    return best.trim();
  }
  function mkAnswer(level_name, level, text, citations, conf) {
    return {
      kind: "answer", answer_text: text, citations: citations || [], confidence: conf,
      grounding_score: conf, trajectory_id: "traj_ret_" + Math.random().toString(36).slice(2, 8),
      cost: 0, tokens: 0, tier: "none", level: level, lang: "en", cache_hit: false, cost_saved: 0,
      tokens_in: 0, tokens_out: 0, model_name: "demo model", complexity: "simple",
      authoritative_source: null, dataset_version: 1, reasoning: null,
      why: { level_name: level_name, explain: level_name === "discovery"
               ? "Searched the fabric; listed matching assets."
               : "Retrieved and composed from the index.",
             reasons: [], retrieved: (citations || []).length,
             signals: { retrieval: 1, semantic: 0.7, coverage: 0.8, agreement: 0.8, resolvable: 1 } }
    };
  }
  function discoveryAnswer(scored) {
    var best = {};
    scored.forEach(function (x) { var d = x.p.doc; if (!best[d] || x.score > best[d].score) best[d] = x; });
    var hits = Object.keys(best).map(function (d) { return best[d]; })
      .sort(function (a, b) { return b.score - a.score; }).slice(0, 6);
    if (!hits.length) return null;
    var parts = ["Found " + hits.length + " assets in the fabric you can reuse — each links to its source:"];
    var cites = [];
    hits.forEach(function (x, i) {
      var p = x.p;
      parts.push("• " + (docTitle(p.doc) || p.path || p.symbol) + " (" + p.kind + ") — " +
        (p.text || "").slice(0, 120) + " [" + (i + 1) + "]");
      cites.push(citeFor(p));
    });
    return mkAnswer("discovery", 1, parts.join("\n"), cites, 0.85);
  }
  function composeAnswer(question, scored, prof) {
    prof = prof || PERSONA_PROFILE.general;
    var cap = PERSONA_DEPTH[prof.depth] || 3, emphasis = prof.emphasis;
    var qt = tokenize(question).filter(function (t) { return !STOP[t]; });
    var top = scored.slice(0, 8);
    var code = top.filter(function (x) {
      if (x.p.kind !== "code" && x.p.kind !== "test") return false;
      var hay = (x.p.symbol + " " + x.p.path).toLowerCase();
      return qt.some(function (t) { return t.length >= 3 && hay.indexOf(t) >= 0; });
    });
    if (code.length) {
      // T27 emphasis — a quality persona leads with the verifying test, a builder
      // with the implementation; a tiebreaker only over passages already matched.
      code.sort(function (x, y) { return pbiasCode(y.p, emphasis) - pbiasCode(x.p, emphasis); });
      var p = code[0].p;
      return mkAnswer("lookup", 1, (p.text || p.symbol) + " [1]", [citeFor(p)], 0.85);
    }
    var docs = top.filter(function (x) { return x.p.kind !== "code" && x.p.kind !== "test"; });
    if (!docs.length || docs[0].score < 1.0) return null;  // too weak — honest gap
    var lead = docs.slice(0, Math.max(1, Math.min(cap, 2))), parts = [], cites = [];  // T27 depth
    lead.forEach(function (x, i) { parts.push(bestSentence(x.p.text, qt) + " [" + (i + 1) + "]"); cites.push(citeFor(x.p)); });
    return mkAnswer("fast", 2, parts.join(" "), cites, 0.7);
  }
  function pbiasCode(p, emphasis) {
    var isTest = p.kind === "test" || (p.path || "").toLowerCase().indexOf("test") >= 0;
    if (emphasis === "test") return isTest ? 1 : 0;
    if (emphasis === "code") return isTest ? 0 : 1;
    return 0;
  }
  function retrieve(question, prof) {
    if (!(SNAP.index && SNAP.index.passages && SNAP.index.passages.length)) return null;
    var qt = tokenize(question).filter(function (t) { return !STOP[t]; });
    if (!qt.length) return null;
    var scored = bm25(qt);
    if (!scored.length) return null;
    return DISCOVERY.test(question) ? discoveryAnswer(scored) : composeAnswer(question, scored, prof);
  }

  // ---- T27: role-conditioned lens ---------------------------------------
  // The role is the signed-in identity's org DESIGNATION (granted by the admin
  // at access time), read via designationOf — never chosen on the ask window.
  // persona_for maps the many titles onto a small set of personas, each with a
  // profile (depth + emphasis + note + lens). The grounded facts stay truthful;
  // the persona changed emphasis (which grounded evidence led) and depth (how
  // many sentences), and the lens frames the result — a verbatim mirror of the
  // server's personas.py + _persona_view.
  var PERSONA_PROFILE = {
    developer: { depth: "full", emphasis: "code", lens: "builder", note: "Developer view — implementation and code emphasised, in full." },
    quality: { depth: "full", emphasis: "test", lens: "quality", note: "Quality view — tests, coverage and how it is verified, in full." },
    delivery: { depth: "brief", emphasis: "authority", lens: "delivery", note: "Delivery view — the status in brief, from the authoritative source." },
    executive: { depth: "headline", emphasis: "authority", lens: "executive", note: "Executive view — the headline, grounded in the authoritative source." },
    curation: { depth: "full", emphasis: "none", lens: "curation", note: "Curator view — how well grounded, and where the gaps are." },
    operations: { depth: "full", emphasis: "none", lens: "operations", note: "Admin view — the level, model and cost that produced this." },
    general: { depth: "full", emphasis: "none", lens: "answer", note: "" }
  };
  var PERSONA_DEPTH = { headline: 1, brief: 2, full: 3 };
  var PERSONA_KEYWORDS = [
    ["ceo", "executive"], ["cto", "executive"], ["coo", "executive"], ["cio", "executive"],
    ["cfo", "executive"], ["chief", "executive"], ["director", "executive"], ["vp", "executive"],
    ["vice president", "executive"], ["head of", "executive"], ["founder", "executive"],
    ["delivery", "delivery"], ["manager", "delivery"], ["scrum", "delivery"],
    ["project lead", "delivery"], ["program", "delivery"], ["product owner", "delivery"],
    ["tester", "quality"], ["test engineer", "quality"], ["qe", "quality"], ["qa", "quality"],
    ["sdet", "quality"], ["quality", "quality"], ["automation", "quality"],
    ["developer", "developer"], ["engineer", "developer"], ["architect", "developer"],
    ["devops", "developer"], ["sre", "developer"], ["programmer", "developer"], ["sde", "developer"],
    ["curator", "curation"], ["knowledge manager", "curation"], ["steward", "curation"],
    ["admin", "operations"], ["operator", "operations"], ["platform", "operations"]
  ];
  function personaFor(designation) {
    var d = String(designation || "").trim().toLowerCase();
    if (!d) return "general";
    for (var i = 0; i < PERSONA_KEYWORDS.length; i++) {
      if (PERSONA_KEYWORDS[i][0] === "lead") continue;
      if (d.indexOf(PERSONA_KEYWORDS[i][0]) >= 0) return PERSONA_KEYWORDS[i][1];
    }
    return d.indexOf("lead") >= 0 ? "delivery" : "general";
  }
  function personaDepthCap(designation) { return PERSONA_DEPTH[PERSONA_PROFILE[personaFor(designation)].depth]; }
  function opsNote(a) {
    var model = a.model_name || model_name_or(a), cache = a.cache_hit ? " · served from cache" : "";
    return "Level " + a.level + " · " + model + " · $" + Number(a.cost || 0).toFixed(4) + cache + ".";
  }
  function model_name_or(a) { return a.model_name || "none (extractive core)"; }
  function curationNote(a, cited) {
    if (a.kind !== "answer") return "Declined — review whether the corpus should cover this.";
    var auth = a.authoritative_source ? "an authoritative source" : cited + " source(s)";
    return "Grounded at " + Number(a.grounding_score || 0).toFixed(2) + " on " + auth + ".";
  }
  function roleView(a, designation) {
    var prof = PERSONA_PROFILE[personaFor(designation)], lens = prof.lens;
    var view = { lens: lens, persona: personaFor(designation), designation: designation || "",
      depth: prof.depth, emphasis: prof.emphasis, note: prof.note };
    var answered = a.kind === "answer", cited = (a.citations || []).length;
    if (lens === "curation") {
      var gap = null;
      if (answered && cited <= 1) gap = "Rests on a single source — consider adding corroborating material.";
      else if (!answered) gap = "No grounded answer yet — a candidate gap for the backlog.";
      view.note = curationNote(a, cited);
      view.grounding = Number((a.grounding_score || 0).toFixed(3));
      view.sources = cited; view.authoritative = !!a.authoritative_source; view.gap_hint = gap;
    } else if (lens === "operations") {
      view.note = opsNote(a); view.level = a.level; view.model = a.model_name || "";
      view.cost = Number((a.cost || 0).toFixed(6)); view.cost_saved = Number((a.cost_saved || 0).toFixed(6));
      view.cache_hit = !!a.cache_hit; view.tokens_in = a.tokens_in || 0; view.tokens_out = a.tokens_out || 0;
    }
    return view;
  }
  // ---- T44/T45: baked answer files + the ask queue -----------------------
  // hash = sha256(norm(question))[:16] — the same key baking.py and
  // build_showcase.py compute, so answers/<hash>.json is addressable here.
  function sha256hex(s) {
    var subtle = (typeof crypto !== "undefined" && crypto.subtle) ? crypto.subtle : null;
    if (!subtle || typeof TextEncoder === "undefined") return Promise.resolve("");
    return subtle.digest("SHA-256", new TextEncoder().encode(s)).then(function (buf) {
      var b = new Uint8Array(buf), out = "";
      for (var i = 0; i < b.length; i++) out += (b[i] < 16 ? "0" : "") + b[i].toString(16);
      return out;
    }).catch(function () { return ""; });
  }
  function questionHash(q) { return sha256hex(norm(q)).then(function (h) { return h.slice(0, 16); }); }
  function answersPath(h) { return "answers/" + h + ".json"; }
  // 1) a baked answer file (bake at ingest, or the queue) — real network fetch.
  function bakedAnswer(question) {
    return questionHash(question).then(function (h) {
      if (!h) return { hash: "", answer: null };
      return realFetch(base + "/" + answersPath(h)).then(function (r) {
        if (!r || !r.ok) return { hash: h, answer: null };
        return r.json().then(function (j) {
          return { hash: h, answer: (j && j.kind) ? fromBaked(j) : null };
        }).catch(function () { return { hash: h, answer: null }; });
      }).catch(function () { return { hash: h, answer: null }; });
    });
  }
  // A baked file is Answer.to_dict() + {question, asked_at, model, cost_usd,
  // source, steps, trajectory_id}; surface the file's model/cost on the card.
  function fromBaked(j) {
    var a = clone(j);
    a.model_name = j.model || a.model_name || "";
    a.cost = Number(j.cost_usd != null ? j.cost_usd : a.cost) || 0;
    a.baked = { source: j.source || "bake", asked_at: j.asked_at || "", model: j.model || "",
                cost_usd: Number(j.cost_usd) || 0, steps: (j.steps || []).length };
    a.why = a.why || { level_name: "baked", explain: "Served from a baked answer.", reasons: [],
                       signals: {}, retrieved: (a.citations || []).length };
    return a;
  }
  function answerFor(subject, question, context) {
    var designation = designationOf(subject);  // the signed-in identity's org title
    // Rich two-turn context from the client, else a legacy history of strings.
    var turns = (context && context.turns) ||
      ((context && context.history) || []).map(function (q) {
        return { question: q, subject: "", answer_docs: [], kind: "answer", options: [] };
      });
    var prof = PERSONA_PROFILE[personaFor(designation)];  // T27 depth + emphasis
    var res = resolveCtx(question, turns);
    if (res.clarify) {
      var c = clarifyChips(res.clarify.chips, res.clarify.reason);
      c.role_view = roleView(c, designation);
      bumpUsage(subject, c);
      return Promise.resolve(c);
    }
    var rq = res.question;  // the (possibly rewritten) question to retrieve on
    return bakedAnswer(rq).then(function (b) {
      var a = b.answer;
      if (!a) {
        // 2) in-browser retrieval and facts. A snapshot answer is persona-agnostic;
        // live retrieval applies the persona's emphasis + depth.
        a = lookup(rq) || retrieve(rq, prof);
        if (!a) {
          var s2 = subjectInText(rq);
          if (s2) a = clarifyAnswer(s2, question, turns.map(function (t) { return t.question; }));
        }
        if (!a) a = gapAnswer(question);
        // 3) the queue — Level 2/3 or a gap with no baked file can get the full
        // answer from the agent; the Workspace offers it and polls the file.
        var eligible = a.kind === "gap" || (a.kind === "answer" && Number(a.level) >= 2);
        a.queue = { eligible: eligible, hash: b.hash, path: b.hash ? answersPath(b.hash) : "",
                    question: rq, repo: window.KF_REPO || "prashobh-ai/QualiZeal_Fabric" };
      }
      if (res.understood_as) a.understood_as = res.understood_as;  // shown under the bubble
      a.role_view = roleView(a, designation);  // T27 — the designation/persona lens
      answerFirst(a, designation);  // T81/T84/T85 — result + Explain offers + governance line
      bumpUsage(subject, a);
      return a;
    });
  }
  // T84 — the per-persona Explain offers, a verbatim mirror of personas.CONTRACT.
  var PERSONA_CONTRACT = {
    developer: ["How it works", "Callers", "Dependencies"],
    quality: ["Edge cases", "Coverage gaps"],
    delivery: ["Why?", "Break down", "Compare"],
    executive: ["Why?", "Break down"],
    curation: ["Audit trail", "Show working"],
    operations: ["Audit trail", "Cost & level"],
    general: ["Why?", "Show working"]
  };
  function answerFirst(a, designation) {
    a.result = a.answer_text;  // T81 — the direct answer, rendered immediately
    var answered = a.kind === "answer";
    var offers = PERSONA_CONTRACT[personaFor(designation)] || PERSONA_CONTRACT.general;
    a.explain = { available: answered, offers: answered ? offers.slice() : [], trace_id: a.trajectory_id };
    var c0 = (a.citations || [])[0];
    a.governance = (answered && c0) ? {
      source_kind: c0.source || (c0.coordinate && c0.coordinate.kind) || "document",
      authority: a.authoritative_source ? (a.authoritative_source.source || "authoritative") : "cited",
      freshness: SNAP.as_of || "as of the last fabric build",
      stale: false,
      document_id: c0.document_id
    } : null;
  }
  function gapAnswer(question) {
    return {
      kind: "gap", answer_text: "No supporting evidence exists in the fabric for that yet.",
      citations: [], confidence: 0, grounding_score: 0, trajectory_id: "traj_demo_gap",
      cost: 0, tokens: 0, tier: "none", level: 0, lang: "en", cache_hit: false, cost_saved: 0,
      tokens_in: 0, tokens_out: 0, model_name: "demo model", complexity: "simple",
      authoritative_source: null, dataset_version: 1, reasoning: null, clarify_back: null,
      why: { level_name: "gap", explain: "Below the grounding threshold.", reasons: [],
             signals: { retrieval: 0, semantic: 0, coverage: 0, agreement: 0, resolvable: 0 }, retrieved: 0 }
    };
  }
  function bumpUsage(subject, a) {
    var u = STATE.usage[subject]; if (!u || !u.windows) return;
    ["today", "7d", "30d"].forEach(function (w) {
      var win = u.windows[w]; if (!win) return;
      win.questions = (win.questions || 0) + 1;
      if (a.kind === "answer") win.answered = (win.answered || 0) + 1;
      else win.declined = (win.declined || 0) + 1;
      win.tokens_in = (win.tokens_in || 0) + (a.tokens_in || 0);
      win.tokens_out = (win.tokens_out || 0) + (a.tokens_out || 0);
      win.cost = (win.cost || 0) + (a.cost || 0);
      win.cost_saved = (win.cost_saved || 0) + (a.cost_saved || 0);
      if (a.kind === "answer") {
        var name = (a.why && a.why.level_name) || "";
        if (name && name !== "gap" && name !== "clarify") {
          win.by_level = win.by_level || {}; win.by_level[name] = (win.by_level[name] || 0) + 1;
        }
      }
    });
  }
  function emptyUsage(subject) {
    var w = { questions: 0, answered: 0, declined: 0, tokens_in: 0, tokens_out: 0, cost: 0, cost_saved: 0, cache_hit_rate: 0, by_level: {} };
    return { subject: subject, windows: { today: clone(w), "7d": clone(w), "30d": clone(w) }, budget: null, speech_seconds: null };
  }

  // ---- T47: fabric-data views (repositories / tables) --------------------
  // The card overlay is baked per repository; the SELECT box previews the
  // first rows the builder read from the real sqlite sheet (no SQL engine on
  // Pages, so the query text is shown back with a "static preview" note).
  function curatorGet(path) {
    return (STATE.get.curator && STATE.get.curator[path]) ||
           (STATE.get.admin && STATE.get.admin[path]) || undefined;
  }
  function repositoryCard(repo) {
    var card = (SNAP.repository || {})[repo];
    if (card) return respond(card);
    return respond({ error: "repository '" + repo + "' is not in facts.json" }, 404);
  }
  function deleteRepository(body) {
    var repo = body.repo || "", n = 0;
    ["curator", "admin"].forEach(function (b) {
      var list = STATE.get[b] && STATE.get[b]["/curator/repositories"];
      if (Array.isArray(list)) {
        var before = list.length;
        STATE.get[b]["/curator/repositories"] = list.filter(function (r) { return r.repo !== repo; });
        n = Math.max(n, before - STATE.get[b]["/curator/repositories"].length);
      }
    });
    return respond({ repo: repo, deleted: n, document_ids: [], dataset_version: 1,
                     note: "showcase — removed from the baked table (no server-side state on Pages)" });
  }
  function tableQuery(body) {
    var sql = String(body.sql || "").trim();
    if (!/^(select|with)\b/i.test(sql)) return respond({ error: "only SELECT queries are allowed" }, 400);
    var tables = curatorGet("/curator/tables") || [];
    var t = tables.filter(function (x) { return x.doc_id === body.doc_id && x.sheet === body.sheet; })[0];
    if (!t) return respond({ error: "unknown sheet " + body.doc_id + "/" + body.sheet }, 404);
    var rows = t.sample || [];
    return respond({ doc_id: t.doc_id, sheet: t.sheet, sql: sql,
                     columns: (t.columns || []).map(function (c) { return c.name; }),
                     rows: rows, row_count: rows.length, truncated: rows.length < (t.rows || 0),
                     note: "static preview" });
  }

  // ---- mutations --------------------------------------------------------
  function applyDecision(body) {
    // curator keep/delete/authoritative/rollback — reflect in the doc table.
    var g = STATE.get.curator || (STATE.get.admin || {});
    var docs = (STATE.get.curator && STATE.get.curator["/curator/documents"]) ||
               (STATE.get.admin && STATE.get.admin["/curator/documents"]);
    if (docs && docs.documents && (body.decision === "delete")) {
      docs.documents = docs.documents.filter(function (d) { return d.document_id !== body.document_id && d.id !== body.document_id; });
    }
  }
  function userMutation(body) {
    var box = STATE.users || (STATE.users = { users: [] });
    box.users = box.users || [];
    if (body.action === "delete") {
      box.users = box.users.filter(function (u) { return u.subject !== body.subject; });
    } else { // add / default
      var roles = body.roles || ["asker"];
      var scopes = body.scopes || (roles.indexOf("admin") >= 0 || roles.indexOf("curator") >= 0 ? ["public", "restricted"] : ["public"]);
      if (body.subject && !box.users.some(function (u) { return u.subject === body.subject; })) {
        box.users.push({ subject: body.subject, roles: roles, scopes: scopes,
                         department: body.department || "", team: body.team || "", status: "active" });
      }
    }
    // keep the admin GET bucket pointing at the mutated list, and persist so a
    // promoted admin/curator can sign back in after a reload (per browser).
    if (STATE.get.admin) STATE.get.admin["/admin/users"] = box;
    saveUsers(box);
    return box;
  }
  function recordFeedback(subject, body) {
    STATE.feedback.unshift({
      subject: subject || "asker.public",
      question: body.question || "", trace_id: body.trace_id || "",
      level: body.level || "", verdict: body.verdict || "down",
      note: body.note || "", at: Date.now()
    });
    STATE.feedback = STATE.feedback.slice(0, 200);
    saveFeedback();
  }

  // ---- GET dispatch -----------------------------------------------------
  function pickGet(subject, path, q) {
    var bucket = bucketOf(subject);
    var map = STATE.get[bucket] || {};
    if (map[path] !== undefined) return map[path];
    // admin inherits curator-scoped reads
    if (bucket === "admin" && STATE.get.curator && STATE.get.curator[path] !== undefined) return STATE.get.curator[path];
    return undefined;
  }

  // T81 — the last answers, keyed by trajectory id, so POST /api/explain can
  // produce the working from the same evidence (browser-side, model-free).
  var EXPLAINS = {};
  function buildExplain(a) {
    var lines = [];
    var w = a.why || {};
    if (w.explain) lines.push("Answered by “" + (w.level_name || "") + "” — " + w.explain);
    if (a.answer_text) lines.push(a.answer_text);
    var cites = a.citations || [];
    if (cites.length) {
      lines.push("Working — the evidence this rests on:");
      cites.forEach(function (c, i) {
        lines.push("[" + (i + 1) + "] " + (c.document_title || "") +
          " · " + (c.coordinate_render || "") + ": " + (c.snippet || ""));
      });
    }
    return {
      trace_id: a.trajectory_id,
      explanation: lines.filter(Boolean).join("\n"),
      model_name: a.model_name || "extractive core",
      cost: 0,
      citations: cites.map(function (c) {
        return { document_title: c.document_title, document_id: c.document_id };
      })
    };
  }
  function handle(method, path, q, body, token) {
    var subject = subjectOf(token);
    if (method === "POST") {
      if (path === "/login") {
        var r = resolveLogin(body.subject, body.password);
        if (r.login) return respond(r.login);
        if (r.code === 401) return respond({ error: "wrong password for " + (body.subject || "") }, 401);
        if (r.code === 400) return respond({ error: "enter your user id" }, 400);
        return respond({ error: "unknown user " + (body.subject || "") }, 404);
      }
      if (path === "/ask") return answerFor(subject, body.question || "", body.context)
        .then(function (a) { if (a && a.trajectory_id) EXPLAINS[a.trajectory_id] = a; return respond(a); });
      if (path === "/api/explain") {
        var ea = EXPLAINS[(body || {}).trace_id];
        if (!ea) return respond({ trace_id: (body || {}).trace_id, explanation: "", error: "unknown or expired trace" });
        return respond(buildExplain(ea));
      }
      if (path === "/curator/decision") { applyDecision(body); return respond({ ok: true, decision: body.decision }); }
      if (path === "/admin/users") return respond(userMutation(body));
      if (path === "/feedback") { recordFeedback(subject, body); return respond({ ok: true }); }
      if (path === "/curator/repository/delete") return deleteRepository(body);  // T47
      if (path === "/curator/tables/query") return tableQuery(body);             // T47
      // upload / sync / bulk-delete / budget / authority / connectors — demo success
      return respond({ ok: true, note: "showcase — action acknowledged (no server-side state on Pages)" });
    }
    // GET
    if (path === "/api/corpus") return respond(SNAP.corpus || {});
    if (path === "/api/suggestions") return respond((SNAP.suggestions || {})[scopeKey(subject)] || { suggestions: [] });
    if (path === "/api/usage") return respond(STATE.usage[subject] || emptyUsage(subject));
    if (path === "/api/galaxy") { var t = q.get("trace_id"); return respond((SNAP.galaxy || {})[t] || { trace_id: t, nodes: [], edges: [], activated_ids: [], halo_ids: [], stats: {} }); }
    if (path === "/api/galaxy/node") { var nid = q.get("id"); var nd = (SNAP.galaxy_nodes || {})[nid]; return nd ? respond(nd) : respond({ error: "unknown node" }, 404); }
    if (path === "/api/galaxy/full") return respond(SNAP.galaxy_full || { nodes: [], edges: [], activated_ids: [], halo_ids: [], stats: {} });
    if (path === "/api/provider") return respond(SNAP.provider || { provider: "Extractive", model: "core", dot: "#5A6B7C", label: "Extractive core" });
    if (path === "/api/analytics") { var w = q.get("window") || "7d"; return respond((SNAP.analytics || {})[w] || (SNAP.analytics || {})["7d"] || {}); }
    if (path === "/api/events") return respond({ events: SNAP.events || [] });
    if (path === "/api/trace") return respond({ spans: [] });
    if (path === "/curator/versions") { var d = q.get("document_id"); return respond((SNAP.versions || {})[d] || { versions: [], datasets: [] }); }
    if (path === "/admin/doctor") { var tg = q.get("target") || ""; return respond((SNAP.doctor || {})[tg] || (SNAP.doctor || {})[""] || { checks: [] }); }
    if (path === "/curator/feedback") return respond({ feedback: STATE.feedback }); // negative-feedback review (new)
    if (path === "/curator/repository") return repositoryCard(q.get("repo") || "");  // T47 card overlay
    var data = pickGet(subject, path, q);
    if (data !== undefined) return respond(data);
    return respond({ error: "not found: " + path }, 404);
  }

  // ---- fetch override ---------------------------------------------------
  window.fetch = function (input, opts) {
    opts = opts || {};
    var url = (typeof input === "string") ? input : (input && input.url) || "";
    var abs; try { abs = new URL(url, location.origin); } catch (e) { return realFetch(input, opts); }
    var path = abs.pathname;
    if (base && path.indexOf(base) === 0) path = path.slice(base.length) || "/";
    // static assets, the snapshot and the baked answer files (T44/T45) go to
    // the network untouched — answers/<hash>.json is a real file on Pages.
    if (path.indexOf("/assets/") === 0 || path.indexOf("/snapshot.json") >= 0 ||
        path.indexOf("/answers/") === 0 ||
        /\.(png|jpe?g|gif|svg|css|js|woff2?|ico)$/i.test(path)) {
      return realFetch(input, opts);
    }
    var method = (opts.method || "GET").toUpperCase();
    var token = "";
    try {
      var h = opts.headers || {};
      token = ((h.Authorization || h.authorization || (h.get && h.get("Authorization")) || "") + "").replace("Bearer ", "");
    } catch (e) {}
    var body = {};
    try { body = opts.body ? JSON.parse(opts.body) : {}; } catch (e) { body = {}; }
    return ready.then(function () { return handle(method, path, abs.searchParams, body, token); });
  };
})();
