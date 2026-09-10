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
    "admin@qualizeal.com": { pw: "kf@qz2026", roles: ["admin"], scopes: ["public", "restricted"] },
    "curator@qualizeal.com": { pw: "kf@qz2026", roles: ["curator"], scopes: ["public", "restricted"] }
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
  function mint(subject, roles, scopes) {
    var token = "demo-" + subject;
    var l = { token: token, subject: subject, roles: roles, scopes: scopes };
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
      return password === seed.pw ? { login: mint(subject, seed.roles, seed.scopes) } : { code: 401 };
    }
    var promoted = (STATE.users && STATE.users.users || []).filter(function (u) {
      return (u.subject || "").toLowerCase() === subject;
    })[0];
    if (promoted) return { login: mint(subject, promoted.roles || ["asker"], promoted.scopes || ["public"]) };
    if (subject.slice(-SSO_DOMAIN.length) === SSO_DOMAIN) {
      return { login: mint(subject, ["asker"], ["public"]) };
    }
    return { code: 404 };
  }
  function bucketOf(subject) {
    var l = loginOf(subject), roles = (l && l.roles) || [];
    return roles.indexOf("admin") >= 0 ? "admin" : roles.indexOf("curator") >= 0 ? "curator" : "asker";
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

  // ---- answer selection (exact, then nearest bank question) -------------
  function answerFor(subject, question) {
    var answers = SNAP.answers || {};
    var key = norm(question);
    var a = answers[key];
    if (!a) {
      // nearest by token overlap (Jaccard) across the baked bank
      var qt = toks(question), best = null, bestScore = 0;
      Object.keys(answers).forEach(function (k) {
        var kt = k.split(" "), setk = {}; kt.forEach(function (t) { setk[t] = 1; });
        var inter = 0; qt.forEach(function (t) { if (setk[t]) inter++; });
        var uni = kt.length + qt.length - inter || 1, sc = inter / uni;
        if (sc > bestScore) { bestScore = sc; best = k; }
      });
      if (best && bestScore >= 0.34) a = answers[best];
    }
    a = a ? clone(a) : gapAnswer(question);
    bumpUsage(subject, a);
    return a;
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
      if (path === "/ask") return respond(answerFor(subject, body.question || ""));
      if (path === "/curator/decision") { applyDecision(body); return respond({ ok: true, decision: body.decision }); }
      if (path === "/admin/users") return respond(userMutation(body));
      if (path === "/feedback") { recordFeedback(subject, body); return respond({ ok: true }); }
      // upload / sync / bulk-delete / budget / authority / connectors — demo success
      return respond({ ok: true, note: "showcase — action acknowledged (no server-side state on Pages)" });
    }
    // GET
    if (path === "/api/corpus") return respond(SNAP.corpus || {});
    if (path === "/api/suggestions") return respond((SNAP.suggestions || {})[scopeKey(subject)] || { suggestions: [] });
    if (path === "/api/usage") return respond(STATE.usage[subject] || emptyUsage(subject));
    if (path === "/api/galaxy") { var t = q.get("trace_id"); return respond((SNAP.galaxy || {})[t] || { trace_id: t, nodes: [], edges: [], stats: {} }); }
    if (path === "/api/analytics") { var w = q.get("window") || "7d"; return respond((SNAP.analytics || {})[w] || (SNAP.analytics || {})["7d"] || {}); }
    if (path === "/api/trace") return respond({ spans: [] });
    if (path === "/curator/versions") { var d = q.get("document_id"); return respond((SNAP.versions || {})[d] || { versions: [], datasets: [] }); }
    if (path === "/admin/doctor") { var tg = q.get("target") || ""; return respond((SNAP.doctor || {})[tg] || (SNAP.doctor || {})[""] || { checks: [] }); }
    if (path === "/curator/feedback") return respond({ feedback: STATE.feedback }); // negative-feedback review (new)
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
    // static assets + the snapshot itself go to the network untouched
    if (path.indexOf("/assets/") === 0 || path.indexOf("/snapshot.json") >= 0 ||
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
