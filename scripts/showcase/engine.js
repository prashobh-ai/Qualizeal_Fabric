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
 * Serving order for POST /ask (T44/T92):
 *   1. a BAKED answer — answers/<hash>.json, hash = sha256(norm(question))[:16],
 *      fetched over the REAL network (the fluent answer from the bake workflow);
 *   2. in-browser retrieval and facts — the snapshot's baked bank, then BM25
 *      over the exported index, composed by the open-source path (Level 1/2/3),
 *      labelled "Open-source LLM".
 * Every question is answered in place (T92): there is no "Get full answer"
 * GitHub-issue detour. /answers/*.json and static assets pass through untouched.
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
      feedback: loadFeedback(),
      events: loadEvents(),  // T132 — one append-only ledger every surface reads
      ledger: loadLedger()   // T132 — one row per model call
    };
    if (STATE.get.admin) STATE.get.admin["/admin/users"] = STATE.users;
    applyConnState();  // T129 — restore the visitor's connector on/off + sync state
    applyUploads();  // T131 — restore the visitor's uploaded files into the index
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

  // ---- T132: one append-only event ledger, written on every action ------
  // Every surface (Workspace, Admin, Curator) reads the SAME store, so a thing
  // done on one screen recomputes every panel on every screen. kf.events holds a
  // row per answer/action; kf.ledger a row per model call. A change dispatches
  // "kf:changed"; the "storage" event carries it cross-tab (see ui_common).
  function loadEvents() {
    try { return JSON.parse(localStorage.getItem("kf.events") || "[]"); } catch (e) { return []; }
  }
  function loadLedger() {
    try { return JSON.parse(localStorage.getItem("kf.ledger") || "[]"); } catch (e) { return []; }
  }
  function evChanged(kind) {
    try { window.dispatchEvent(new CustomEvent("kf:changed", { detail: { kind: kind } })); } catch (e) {}
  }
  function evAppend(kind, row) {
    var e = Object.assign(
      { id: "e" + Date.now() + Math.random().toString(36).slice(2, 6), ts: Date.now(), kind: kind },
      row || {}
    );
    STATE.events = (STATE.events || []).concat([e]).slice(-1000);
    try { localStorage.setItem("kf.events", JSON.stringify(STATE.events)); } catch (x) {}
    evChanged(kind);
    return e;
  }
  function ledgerAppend(row) {
    var r = Object.assign({ ts: Date.now() }, row || {});
    STATE.ledger = (STATE.ledger || []).concat([r]).slice(-1000);
    try { localStorage.setItem("kf.ledger", JSON.stringify(STATE.ledger)); } catch (x) {}
    return r;
  }
  // Read fresh from localStorage so a sibling tab's writes (and the UI's own
  // KF.event) are always reflected when a panel recomputes.
  function events() { return loadEvents(); }
  function ledgerRows() { return loadLedger(); }
  // One helper the answer path calls for every delivered answer / clarify / gap:
  // writes the row-level event AND (when tokens were spent) the model-call ledger
  // row, so every Admin and Curator panel can recompute from real activity.
  function evAnswer(subject, designation, question, a) {
    a = a || {};
    var provider = a.provider || (a.baked && a.baked.model ? "baked" : "open-source");
    var ev = {
      subject: subject || "", role: designation || "", question: String(question || "").slice(0, 400),
      answer_kind: a.kind || "answer", level_name: (a.why && a.why.level_name) || "",
      provider: provider, model: a.model_name || "",
      tokens_in: a.tokens_in || 0, tokens_out: a.tokens_out || 0,
      cache_read: a.cache_read || 0, cost: a.cost || 0, cost_saved: a.cost_saved || 0,
      latency_ms: a.latency_ms || a._ms || 0, trust: a.grounding_score || 0,
      citations_n: (a.citations || []).length, sources: answerSources(a),
      path: a.tier === "agent" || a.reasoning ? "agent" : "fast",
      session_id: currentSessionId(), trace_id: a.trajectory_id || "", understood_as: a.understood_as || ""
    };
    evAppend("answer", ev);
    if (ev.tokens_in || ev.tokens_out || ev.cost) {
      ledgerAppend({
        provider: provider, model: ev.model, purpose: "answer",
        input_tokens: ev.tokens_in, output_tokens: ev.tokens_out, thinking_tokens: a.thinking_tokens || 0,
        cache_read: ev.cache_read, cache_write: 0, latency_ms: ev.latency_ms, cost_usd: ev.cost,
        source: "live", question_hash: hashStr(String(question || ""))
      });
    }
    return ev;
  }
  function answerSources(a) {
    // The connector each cited passage came from (github/jira/confluence/website/
    // files), so service-levels can group by data type. Best-effort from the index.
    var out = {}, cits = a.citations || [];
    cits.forEach(function (c) {
      var id = (c && c.document_id) || "";
      var p = indexBySrcDoc(id);
      if (p) out[p] = 1;
    });
    return Object.keys(out);
  }
  function hashStr(s) {
    var h = 0; for (var i = 0; i < s.length; i++) { h = (h * 31 + s.charCodeAt(i)) | 0; }
    return "q" + (h >>> 0).toString(36);
  }
  // A stable per-tab id so answer events can be grouped into a working session.
  function currentSessionId() {
    try {
      var v = sessionStorage.getItem("kf.sid");
      if (!v) { v = "s" + Date.now().toString(36); sessionStorage.setItem("kf.sid", v); }
      return v;
    } catch (e) { return "s0"; }
  }
  // The connector source a cited document belongs to, from the index passages.
  var _DOC2SRC = null;
  function indexBySrcDoc(docId) {
    if (!docId) return "";
    if (!_DOC2SRC) {
      _DOC2SRC = {};
      (((SNAP.index || {}).passages) || []).forEach(function (p) {
        if (p && p.doc && !_DOC2SRC[p.doc]) _DOC2SRC[p.doc] = (p.source || "").toLowerCase();
      });
    }
    return _DOC2SRC[docId] || "";
  }

  // ---- connectors — real in-browser on/off switches (T129) --------------
  // The baked connector cards are a permanent hidden preload; the visitor's
  // toggle / Save / Sync / delete are held here and persisted per browser, so
  // the demo behaves exactly like a live server — the mock is invisible. A
  // disabled (or deleted) source is excluded from retrieval, so deactivating it
  // really removes its answers.
  function connList() {
    var g = (STATE.get.admin && STATE.get.admin["/admin/connectors"]) || {};
    return g.connectors || [];
  }
  function connBySource(source) {
    var l = connList();
    for (var i = 0; i < l.length; i++) if (l[i].source === source) return l[i];
    return null;
  }
  function loadConnState() {
    try { return JSON.parse(localStorage.getItem("kf.connectors") || "{}"); } catch (e) { return {}; }
  }
  function saveConnState(st) {
    try { localStorage.setItem("kf.connectors", JSON.stringify(st)); } catch (e) {}
  }
  function applyConnState() {
    // Apply the visitor's persisted overrides onto the baked connector rows.
    var st = loadConnState();
    connList().forEach(function (c) {
      var o = st[c.source]; if (!o) return;
      c.health = c.health || {};
      if (o.enabled != null) c.enabled = o.enabled;
      if (o.allow != null) c.allow = o.allow;
      if (o.interval_s != null) { c.interval_s = o.interval_s; c.health.interval_s = o.interval_s; }
      if (o.items != null) c.health.items = o.items;
      if (o.last_run != null) { c.health.last_run = o.last_run; c.health.last_status = "ok"; c.health.freshness_minutes = 0; }
      if (o.deleted) c._deleted = true;
    });
  }
  function disabledSources() {
    var out = {};
    connList().forEach(function (c) {
      if (c.enabled === false || c._deleted) out[(c.source || "").toLowerCase()] = 1;
    });
    return out;
  }
  function connectorsPayload() {
    var g = (STATE.get.admin && STATE.get.admin["/admin/connectors"]) || {};
    return { connectors: connList().filter(function (c) { return !c._deleted; }), schedules: g.schedules || [] };
  }
  function saveConn(source, patch) {
    var c = connBySource(source);
    if (!c) return { status: "error", message: "unknown source " + source };
    c.health = c.health || {};
    if (patch.enabled != null) c.enabled = !!patch.enabled;
    if (patch.allow != null) c.allow = patch.allow;
    if (patch.interval_s != null) { c.interval_s = patch.interval_s; c.health.interval_s = patch.interval_s; }
    var st = loadConnState();
    st[source] = Object.assign(st[source] || {}, { enabled: c.enabled, allow: c.allow, interval_s: c.interval_s });
    saveConnState(st);
    ridxDirty();  // T132 — enabling/disabling changes the active passage set
    return { status: "ok", connector: { source: source, enabled: c.enabled, allow: c.allow, interval_s: c.interval_s } };
  }
  function syncConn(source) {
    var c = connBySource(source);
    if (!c) return { status: "error", message: "unknown source " + source };
    c.health = c.health || {};
    var items = c.health.items || 0;
    var now = Date.now() / 1000;
    c.health.last_run = now; c.health.last_status = "ok"; c.health.freshness_minutes = 0; c.health.error_count = 0;
    var st = loadConnState();
    st[source] = Object.assign(st[source] || {}, { items: items, last_run: now });
    saveConnState(st);
    return { status: "ok", pulled: items, ingested: items, tombstoned: 0, dataset_version: SNAP.dataset_version || 1 };
  }
  function deleteConn(source) {
    var c = connBySource(source);
    if (!c) return { status: "error", message: "unknown source " + source };
    c._deleted = true;
    var st = loadConnState();
    st[source] = Object.assign(st[source] || {}, { deleted: true });
    saveConnState(st);
    ridxDirty();  // T132 — a deleted source leaves the active passage set
    return { status: "ok", source: source, deleted: true };
  }
  function runDueConn() {
    var ran = [], now = Date.now() / 1000, st = loadConnState();
    connList().forEach(function (c) {
      if (c.enabled === false || c._deleted) return;
      c.health = c.health || {}; c.health.last_run = now; c.health.last_status = "ok"; c.health.freshness_minutes = 0;
      st[c.source] = Object.assign(st[c.source] || {}, { last_run: now });
      ran.push(c.source);
    });
    saveConnState(st);
    return { ran: ran, count: ran.length };
  }

  // ---- T131: uploaded files — real in-browser add/delete + repo commit ------
  // An uploaded .pptx / .pdf / .xlsx / .docx (or any text file) is added to the
  // Files source IN THE BROWSER immediately (persisted per visitor), so it is
  // answerable at once and the demo behaves like a live server — the mock is
  // invisible. In parallel, if a commit endpoint is configured (never a token in
  // the page — a small token-holding service the operator stands up), the file is
  // POSTed there to REALLY land in the repo; a rebuild then re-indexes its full
  // text (PPTX/PDF/XLSX parsed at build time) and it becomes a permanent doc.
  // Delete removes it from the browser and, when configured, from the repo too.
  function loadUploads() {
    try { return JSON.parse(localStorage.getItem("kf.uploads") || "[]"); } catch (e) { return []; }
  }
  function saveUploads(a) {
    // T138 — parsed uploads carry real passages, so a naive write can exceed the
    // ~5 MB localStorage budget. Trim to 200 files, then shed the oldest until the
    // serialised store fits ~4 MB; the newest uploads (unshifted first) survive.
    var arr = (a || []).slice(0, 200);
    for (;;) {
      var s = JSON.stringify(arr);
      if (s.length <= 4000000 || arr.length <= 1) { try { localStorage.setItem("kf.uploads", s); } catch (e) {} return; }
      arr = arr.slice(0, arr.length - 1);
    }
  }
  // T138 — the in-browser parser (upload_parse.js) and JSZip (vendored) are loaded
  // as page scripts; under Node the test harness sets these globals. When absent we
  // fall back to a stored-but-not-parsed placeholder so an add still stands.
  function getKFUpload() {
    if (typeof KFUpload !== "undefined") return KFUpload;
    if (typeof window !== "undefined" && window.KFUpload) return window.KFUpload;
    return null;
  }
  function getJSZip() {
    if (typeof JSZip !== "undefined") return JSZip;
    if (typeof window !== "undefined" && window.JSZip) return window.JSZip;
    return null;
  }
  function b64ToU8(b64) {
    try {
      var bin = atob(b64 || ""), u8 = new Uint8Array(bin.length);
      for (var i = 0; i < bin.length; i++) u8[i] = bin.charCodeAt(i);
      return u8;
    } catch (e) { return new Uint8Array(0); }
  }
  function normName(s) {
    return String(s || "").toLowerCase().replace(/\.[a-z0-9]+$/, "").replace(/[^a-z0-9]+/g, "");
  }
  // Baked document titles, so re-uploading a file already in the fabric is reported
  // as a duplicate rather than double-indexed.
  function bakedDocTitleSet() {
    ensureIndexBase();
    var out = {}, ix = SNAP.index || {}, n = ix._baseDocN || 0;
    (ix.docs || []).slice(0, n).forEach(function (d) { if (d && d.title) out[normName(d.title)] = 1; });
    return out;
  }
  function slugify(s) {
    return (String(s || "").toLowerCase().replace(/[^a-z0-9]+/g, "-").replace(/^-+|-+$/g, "").slice(0, 60)) || "upload";
  }
  function commitEndpoint() {
    try { var v = localStorage.getItem("kf.commit_endpoint"); if (v) return v; } catch (e) {}
    return SNAP.commit_endpoint || "";  // baked config, if the operator set one
  }
  function ensureIndexBase() {
    SNAP.index = SNAP.index || { passages: [], docs: [], df: {}, N: 0, avgdl: 1 };
    SNAP.index.passages = SNAP.index.passages || [];
    SNAP.index.docs = SNAP.index.docs || [];
    if (SNAP.index._baseN == null) {
      SNAP.index._baseN = SNAP.index.passages.length;
      SNAP.index._baseDocN = SNAP.index.docs.length;
      SNAP.index._baseDocuments = (SNAP.facts && SNAP.facts.documents) || 0;
    }
  }
  function ensureFilesCard(count) {
    var g = STATE.get && STATE.get.admin && STATE.get.admin["/admin/connectors"];
    if (!g) return;
    g.connectors = g.connectors || [];
    var c = null;
    for (var i = 0; i < g.connectors.length; i++) if (g.connectors[i].source === "files") { c = g.connectors[i]; break; }
    if (!c) {
      c = { source: "files", enabled: true, allow: [], interval_s: 3600, health: {} };
      g.connectors.push(c);
    }
    c.health = c.health || {};
    c.health.items = count;
    if (count) { c.health.last_run = Date.now() / 1000; c.health.last_status = "ok"; c.health.freshness_minutes = 0; }
    // honour a persisted toggle/delete on the Files source, like every other card
    var o = loadConnState()["files"];
    if (o) {
      if (o.enabled != null) c.enabled = o.enabled;
      if (o.interval_s != null) c.interval_s = o.interval_s;
      if (o.deleted) c._deleted = true;
    }
  }
  function applyUploads() {
    // T138 — re-inject the visitor's uploaded docs over the pristine baked index
    // (idempotent: truncate to the baked base, then push the current uploads). Each
    // upload now contributes its REAL parsed passages — one per paragraph / slide /
    // table / row — so it is retrieved, answered and cited exactly like a baked doc,
    // in the same active set and the same galaxy.
    ensureIndexBase();
    var ix = SNAP.index, ups = loadUploads();
    ix.passages.length = ix._baseN;
    ix.docs.length = ix._baseDocN;
    ups.forEach(function (u) {
      var passages = (u.passages && u.passages.length)
        ? u.passages
        : [{ text: u.text || ("Document “" + u.title + "” added to the Files source."),
             coord: u.coord || ("Files · " + u.title), kind: "document" }];
      passages.forEach(function (p, i) {
        var text = String(p.text || "");
        ix.passages.push({
          doc: u.doc, source: "files", text: text, kind: p.kind || "document",
          url: "", path: u.title, symbol: "",
          coord: p.coord || (u.title + " · ¶" + (i + 1)),
          section_path: p.section_path || "", slide: p.slide,
          idx: (u.title + " " + text).toLowerCase()
        });
      });
      ix.docs.push({ id: u.doc, title: u.title, kind: "document", url: "" });
    });
    ix.N = ix.passages.length || 1;
    if (SNAP.facts) SNAP.facts.documents = ix._baseDocuments + ups.length;
    ensureFilesCard(ups.length);
    ridxDirty();  // T132 — rebuild the index (and the doc→source map) with the new passages
  }
  function tryCommit(op, rec) {
    // Fire-and-forget POST to the operator's token-holding commit service, if one
    // is configured. The page never holds a token; without an endpoint this is a
    // silent no-op and the in-browser add/delete still stands.
    var url = commitEndpoint();
    if (!url) return false;
    try {
      realFetch(url, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          op: op, filename: rec.title, path: "corpus/uploads/" + rec.stored,
          content_b64: rec.content_b64 || "", ts: rec.ts
        })
      }).catch(function () {});
    } catch (e) { return false; }
    return true;
  }
  // T138 — parse one uploaded file into real passages/tables/images. DOCX/PPTX/XLSX
  // are unzipped and read in-browser (JSZip + upload_parse.js, no network); text and
  // CSV are read directly; a PDF is stored with a metadata passage until the build
  // extracts it server-side. Always resolves — a parse error degrades to a
  // stored-but-not-parsed placeholder, never a rejected upload.
  var BINARY_EXT = { pdf: 1, docx: 1, docm: 1, pptx: 1, pptm: 1, xlsx: 1, xlsm: 1 };
  function parseOne(f) {
    var name = (f && f.filename) || "document";
    var b64 = (f && f.content_b64) || "", text = (f && f.text) || "";
    var ext = (String(name).split(".").pop() || "").toLowerCase();
    var KU = getKFUpload();
    // A text-only add (Curator "add document", pasted JSON) whose name is not a
    // binary type: index its text directly as plain paragraphs — never route it
    // through the unzip path, which would fail and drop to a placeholder.
    if (!b64 && text && !BINARY_EXT[ext]) {
      var name2 = /\.[a-z0-9]+$/i.test(name) ? name : name + ".txt";
      var bytesT = typeof TextEncoder !== "undefined" ? new TextEncoder().encode(text) : new Uint8Array(0);
      if (KU) return KU.parse(name2, bytesT, {}).then(function (r) { r.filename = name; return r; }).catch(function (e) { return placeholderParse(name, e); });
      return Promise.resolve(placeholderParse(name, null));
    }
    var bytes = b64 ? b64ToU8(b64) : new Uint8Array(0);
    if (KU && bytes.length) {
      return KU.parse(name, bytes, { JSZip: getJSZip() }).catch(function (e) { return placeholderParse(name, e); });
    }
    return Promise.resolve(placeholderParse(name, null));
  }
  function placeholderParse(name, e) {
    var ext = (String(name).split(".").pop() || "").toLowerCase();
    return {
      filename: name, ext: ext, hash: "up_" + Math.abs((name + Date.now()).split("").reduce(function (a, c) { return (a * 31 + c.charCodeAt(0)) >>> 0; }, 7)).toString(16),
      parsed: false, note: e ? ("could not parse: " + (e.message || e)) : "stored; not parsed in-browser",
      passages: [{ text: "Document “" + name + "” added to the Files source.", coord: "Files · " + name, kind: "document" }],
      tables: [], images: []
    };
  }
  function buildUploadRecord(parsed) {
    var name = parsed.filename, slug = slugify(name);
    var ext = parsed.ext || (name.split(".").pop() || "txt").toLowerCase();
    var passages = (parsed.passages || []).slice(0, 400).map(function (p, i) {
      return {
        text: String(p.text || "").slice(0, 600),
        coord: p.coord || (name + " · ¶" + (i + 1)),
        kind: p.kind || "document",
        section_path: p.section_path || "", slide: p.slide
      };
    });
    if (!passages.length) passages = [{ text: "Document “" + name + "” added to the Files source.", coord: "Files · " + name, kind: "document" }];
    return {
      doc: "upload:" + slug + ":" + parsed.hash, hash: parsed.hash,
      title: name, ext: ext, source: "files",
      parsed: parsed.parsed !== false, note: parsed.note || "",
      stored: slug + "." + ext, ts: new Date().toISOString(),
      tables_count: (parsed.tables || []).length, images_count: (parsed.images || []).length,
      passages: passages,
      // legacy single-value fields kept for any older reader
      text: passages[0].text, coord: passages[0].coord,
      idx: (name + " " + passages.map(function (p) { return p.text; }).join(" ")).toLowerCase().slice(0, 4000),
      has_text: parsed.parsed !== false
    };
  }
  function uploadDocs(files) {
    var all = loadUploads(), seen = {}, added = [], duplicates = [], committed = false;
    all.forEach(function (u) { if (u.hash) seen[u.hash] = 1; });
    var baked = bakedDocTitleSet();
    var chain = Promise.resolve();
    (files || []).forEach(function (f) {
      chain = chain.then(function () {
        return parseOne(f).then(function (parsed) {
          var name = parsed.filename, hash = parsed.hash;
          // Dedupe by content hash (same bytes, any name) or by a name already baked
          // into the fabric — reported, not double-indexed.
          if (seen[hash]) { duplicates.push({ title: name, reason: "already uploaded" }); return; }
          if (baked[normName(name)]) { duplicates.push({ title: name, reason: "already in the fabric" }); return; }
          var rec = buildUploadRecord(parsed);
          seen[hash] = 1;
          if (tryCommit("add", { title: rec.title, stored: rec.stored, content_b64: (f && f.content_b64) || "", ts: rec.ts })) committed = true;
          all.unshift(rec);
          added.push(rec);
        });
      });
    });
    return chain.then(function () {
      saveUploads(all);
      applyUploads();
      var sum = function (key) { return added.reduce(function (n, a) { return n + (key === "passages" ? (a.passages ? a.passages.length : 0) : (a[key] || 0)); }, 0); };
      return {
        uploaded: added.length, ingested: added.length, held_for_review: 0,
        duplicates: duplicates,
        run_id: "upload-" + Date.now().toString(36), dataset_version: SNAP.dataset_version || 1,
        sources: ["files"], committed: committed,
        documents: added.map(function (a) { return a.title; }),
        passages_added: sum("passages"), tables_added: sum("tables_count"), images_added: sum("images_count"),
        note: (!added.length && duplicates.length) ? "already in the fabric"
          : (committed ? "committed to the repository" : "added to the Files source")
      };
    });
  }
  function deleteUpload(docId) {
    var all = loadUploads(), gone = null;
    var kept = all.filter(function (u) { if (u.doc === docId) { gone = u; return false; } return true; });
    saveUploads(kept);
    if (gone) tryCommit("delete", gone);
    applyUploads();
    return { status: "ok", deleted: gone ? 1 : 0, document_id: docId };
  }
  function uploadsPayload() {
    return {
      uploads: loadUploads().map(function (u) {
        return {
          document_id: u.doc, title: u.title, source: "files", added_at: u.ts,
          has_text: !!u.has_text, parsed: u.parsed !== false, ext: u.ext || "",
          passages: (u.passages || []).length, tables: u.tables_count || 0, images: u.images_count || 0,
          note: u.note || ""
        };
      })
    };
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
  // T132 fold-in — the ONE active document set every read agrees on: a passage is
  // active when its connector is on AND its document is not deactivated. Retrieval,
  // the ranking index and the corpus counts all read this, so a source toggle or a
  // document delete moves answers, tiles, Curator rings and Admin panels together —
  // no seam where a deactivated source still answers.
  function deactivatedDocs() {
    try { return JSON.parse(localStorage.getItem("kf.docs_off") || "{}") || {}; } catch (e) { return {}; }
  }
  function activePassages() {
    var off = disabledSources(), gone = deactivatedDocs();
    return (((SNAP.index || {}).passages) || []).filter(function (p) {
      return !off[(p.source || "").toLowerCase()] && !gone[p.doc];
    });
  }
  function ridxDirty() { RIDX = null; _DOC2SRC = null; }
  // T132 fold-in — the ten Workspace tiles (and Curator counts, and the Admin
  // sources card) computed from the ACTIVE set, not a frozen baked object, so a
  // source toggle or a document delete moves every count at once. One helper, one
  // source of truth — no two screens disagree.
  function corpusCounts() {
    var base = clone(SNAP.corpus || {});
    var off = disabledSources();
    var aps = activePassages(), docs = {};
    aps.forEach(function (p) {
      if (p.doc && p.kind !== "code" && p.kind !== "test") docs[p.doc] = 1;
    });
    base.passages = aps.length;
    base.documents = Object.keys(docs).length || 0;
    // T138 — a parsed upload's tables and images move the Tables / Images tiles too
    // (only while the Files source is on and the doc is not deactivated), so a DOCX
    // with 13 tables or a deck with embedded media visibly grows the corpus.
    var gone = deactivatedDocs(), upT = 0, upI = 0;
    if (!off.files) {
      loadUploads().forEach(function (u) {
        if (gone[u.doc]) return;
        upT += u.tables_count || 0; upI += u.images_count || 0;
      });
    }
    base.tables = (base.tables || 0) + upT;
    base.images = (base.images || 0) + upI;
    if (off.github) base.repositories = 0;
    if (off.jira) base.jira_projects = 0;
    if (off.confluence) base.confluence_spaces = 0;
    if (off.website || off.files) {
      // graph-derived tiles collapse when the org content is off
      if (Object.keys(off).length) { base.entities = base.entities || 0; }
    }
    return base;
  }
  // Active document counts per connector source — the Admin sources card and the
  // Curator readiness rings read this so they never disagree with the tiles.
  function activeDocCountBySource() {
    var out = {};
    activePassages().forEach(function (p) {
      var s = (p.source || "").toLowerCase(); if (!s) return;
      out[s] = out[s] || { documents: {}, passages: 0 };
      out[s].passages += 1; if (p.doc) out[s].documents[p.doc] = 1;
    });
    var flat = {};
    Object.keys(out).forEach(function (s) {
      flat[s] = { documents: Object.keys(out[s].documents).length, passages: out[s].passages };
    });
    return flat;
  }
  function ridx() {
    if (RIDX) return RIDX;
    var ix = SNAP.index || {};
    var full = (((SNAP.index || {}).passages) || []).length;
    var active = activePassages();
    // When nothing is toggled off (the default), keep the baked df / N / avgdl so the
    // ranking is byte-identical to the server (parity). When the active set is
    // reduced, recompute df / N / avgdl over the active passages so the toggle
    // genuinely changes what ranks and answers.
    var reduced = active.length !== full;
    var df = {}, tot = 0;
    var ps = active.map(function (p) {
      var tk = tokenize(p.idx || p.text), tf = {}, seen = {};
      tk.forEach(function (t) { tf[t] = (tf[t] || 0) + 1; seen[t] = 1; });
      if (reduced) { Object.keys(seen).forEach(function (t) { df[t] = (df[t] || 0) + 1; }); tot += tk.length; }
      return { p: p, tf: tf, dl: tk.length };
    });
    RIDX = reduced
      ? { ps: ps, df: df, N: ps.length || 1, avgdl: (ps.length ? tot / ps.length : 1) || 1, docs: {} }
      : { ps: ps, df: ix.df || {}, N: ix.N || ps.length || 1, avgdl: ix.avgdl || 1, docs: {} };
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
  // T91 — a word x 1.3 token estimate so the keyless browser answer shows a
  // real input/output split on the card (the server counts with tiktoken).
  function estTokens(s) { return Math.max(1, Math.round((String(s || "").split(/\s+/).filter(Boolean).length) * 1.3)); }
  function mkAnswer(level_name, level, text, citations, conf) {
    var evidence = (citations || []).map(function (c) { return c.snippet || ""; }).join(" ");
    var tin = estTokens(evidence || text), tout = estTokens(text);
    return {
      kind: "answer", answer_text: text, citations: citations || [], confidence: conf,
      grounding_score: conf, trajectory_id: "traj_ret_" + Math.random().toString(36).slice(2, 8),
      cost: 0, tokens: tin + tout, tier: "fast", level: level, lang: "en", cache_hit: false, cost_saved: 0,
      tokens_in: tin, tokens_out: tout, model_name: "Open-source LLM · Extractive-NLG", complexity: "simple",
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
  // T142 — routing so the organisation's documents win. A code passage may only
  // lead an answer when the question actually asks about code, or a query token
  // matches a whole symbol/path identifier (never a substring like "all" inside
  // "test_list_all_…"); and a "what all services/products" question is a Level-0
  // list of the service (or product) documents, not a single passage.
  var CODE_INTENT = /\b(function|method|class|symbol|implement|implementation|defined|where is|how does .* work|code|snippet|call(ed|s)?|import|module|file|repo(sitory)?)\b/i;
  var LIST_INTENT = /\b(what all|which|list|what are (all )?the|what .*\b(services|products|offerings|solutions|capabilities)\b)\b/i;
  var GENERIC_IDENT = { all: 1, list: 1, get: 1, set: 1, "new": 1, add: 1, run: 1, test: 1, data: 1, name: 1, type: 1, main: 1, index: 1, user: 1, file: 1, item: 1, value: 1 };
  function identSegs(s) { return String(s || "").toLowerCase().split(/[^a-z0-9]+/).filter(Boolean); }
  function docArea(id) { var d = ridx().docs[id]; return (d && d.area) || ""; }
  function serviceName(id) {
    var t = docTitle(id) || "";
    return t.replace(/^(Service|Product|Company)\s+/i, "").trim() || t;
  }
  // The distinct service (or product) documents behind a "what all …" question,
  // as a bulleted Level-0 list. Returns null when the question is not a list
  // question or fewer than three of the top passages come from that area — so it
  // never hijacks an ordinary question. Runs over the ACTIVE index, so a
  // toggled-off source drops out of the list.
  function serviceListAnswer(question, prof) {
    if (!LIST_INTENT.test(question)) return null;
    var qt = tokenize(question).filter(function (t) { return !STOP[t]; });
    if (!qt.length) return null;
    var scored = bm25(qt);
    if (!scored.length) return null;
    var wantArea = /\bproducts?\b/i.test(question) ? "Product"
      : (/\bservices?\b/i.test(question) ? "Service" : "");
    var inArea = function (id) {
      var a = docArea(id);
      return wantArea ? a === wantArea : (a === "Service" || a === "Product");
    };
    var topHits = scored.slice(0, 8).filter(function (x) { return inArea(x.p.doc); }).length;
    if (topHits < 3) return null;  // not really a services/products retrieval
    var byDoc = {}, order = [];
    scored.forEach(function (x) {
      if (!inArea(x.p.doc)) return;
      if (!byDoc[x.p.doc]) { byDoc[x.p.doc] = x.p; order.push(x.p.doc); }
    });
    if (order.length < 3) return null;
    var names = [], cites = [];
    order.slice(0, 20).forEach(function (d, i) {
      names.push(serviceName(d) + " [" + (i + 1) + "]");
      cites.push(citeFor(byDoc[d]));
    });
    var noun = wantArea === "Product" ? "products" : "services";
    return mkAnswer("lookup", 1, "QualiZeal offers these " + noun + ": " + names.join(", ") + ".", cites, 0.9);
  }
  function composeAnswer(question, scored, prof) {
    prof = prof || PERSONA_PROFILE.general;
    var cap = PERSONA_DEPTH[prof.depth] || 3, emphasis = prof.emphasis;
    var qt = tokenize(question).filter(function (t) { return !STOP[t]; });
    var top = scored.slice(0, 8);
    var list = serviceListAnswer(question, prof);
    if (list) return list;
    var code = top.filter(function (x) {
      if (x.p.kind !== "code" && x.p.kind !== "test") return false;
      if (CODE_INTENT.test(question)) return true;
      // else require a WHOLE-identifier match, not a substring, and not a generic word
      var hay = identSegs(x.p.symbol).concat(identSegs(x.p.path));
      return qt.some(function (t) { return t.length >= 3 && !GENERIC_IDENT[t] && hay.indexOf(t) >= 0; });
    });
    if (code.length) {
      // T27 emphasis — a quality persona leads with the verifying test, a builder
      // with the implementation; a tiebreaker only over passages already matched.
      code.sort(function (x, y) { return pbiasCode(y.p, emphasis) - pbiasCode(x.p, emphasis); });
      var p = code[0].p, body = p.text || p.symbol || "";
      // T142 — never return an empty (or near-empty) code answer; fall through to
      // the documents instead of citing a bare symbol.
      if (body.replace(/\s+/g, " ").trim().length >= 20) {
        return mkAnswer("lookup", 1, body + " [1]", [citeFor(p)], 0.85);
      }
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

  // T126 — the facts / inventory tier, mirroring the server's aggregate path. A
  // count question ("how many repos / Jira issues / Confluence pages / documents")
  // is answered from the baked facts summary BEFORE BM25 retrieval, so it returns
  // the real number instead of a matching code/test symbol. Only count intent
  // fires it, so "where is the login" still routes to the code/prose tiers.
  var COUNT_INTENT = /\b(how many|how much|number of|count of|total (?:number )?of)\b/i;
  function factCite(title, url) {
    return {
      document_id: url || title, document_title: title,
      coordinate: { kind: "page_paragraph", locator: url ? { url: url } : { path: "data/facts.json" } },
      coordinate_render: "", passage_id: "", snippet: ""
    };
  }
  function factsAnswer(question) {
    var f = SNAP.facts;
    if (!f || !COUNT_INTENT.test(question)) return null;
    var ql = question.toLowerCase();
    var asOf = SNAP.as_of || "as of the last fabric build";
    var off = disabledSources();  // T129 — a deactivated source answers no counts
    if (/\b(repos?|repositor(?:y|ies)|github)\b/.test(ql) && off.github) return null;
    if (/\b(issues?|tickets?|bugs?|jira)\b/.test(ql) && off.jira) return null;
    if (/\b(pages?|confluence|space)\b/.test(ql) && off.confluence) return null;
    if (/\b(repos?|repositor(?:y|ies))\b/.test(ql) && f.repo_count != null) {
      return mkAnswer("facts", 1,
        "The fabric covers " + f.repo_count + " repositories (" + asOf + "): " +
          (f.repositories || []).join(", ") + " [1].",
        [factCite("facts.json", "")], 0.95);
    }
    if (/\bbugs?\b/.test(ql) && f.jira && f.jira.by_type) {
      var nb = f.jira.by_type.Bug || 0;
      return mkAnswer("facts", 1,
        "Jira project " + f.jira.name + " (" + f.jira.project + ") has " + nb +
          " bugs of " + f.jira.total + " issues [1].",
        [factCite("Jira " + f.jira.project, f.jira.url)], 0.95);
    }
    if (/\b(issues?|tickets?|stories|story|tasks?|epics?|jira)\b/.test(ql) && f.jira) {
      var done = (f.jira.by_status && f.jira.by_status.Done) || 0;
      return mkAnswer("facts", 1,
        "Jira project " + f.jira.name + " (" + f.jira.project + ") has " + f.jira.total +
          " issues (" + done + " done) [1].",
        [factCite("Jira " + f.jira.project, f.jira.url)], 0.95);
    }
    if (/\b(pages?|confluence|space)\b/.test(ql) && f.confluence) {
      return mkAnswer("facts", 1,
        "Confluence space " + f.confluence.name + " (" + f.confluence.space + ") has " +
          f.confluence.pages + " pages [1].",
        [factCite("Confluence " + f.confluence.space, f.confluence.url)], 0.95);
    }
    if (/\b(documents?|docs|files)\b/.test(ql) && f.documents != null) {
      return mkAnswer("facts", 1,
        "The fabric holds " + f.documents + " documents (" + asOf + ") [1].",
        [factCite("facts.json", "")], 0.95);
    }
    return null;
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
    // T142 — a "what all services/products" question is a Level-0 document list. It
    // is resolved before coreference/clarify and before the baked cache, so it can
    // never be turned into a clarify-back or a single-document baked answer.
    var listAns = serviceListAnswer(question, prof);
    if (listAns) {
      listAns.role_view = roleView(listAns, designation);
      answerFirst(listAns, designation);
      bumpUsage(subject, listAns);
      bumpMeter(listAns);
      evAnswer(subject, designation, question, listAns);
      return Promise.resolve(listAns);
    }
    var res = resolveCtx(question, turns);
    if (res.clarify) {
      var c = clarifyChips(res.clarify.chips, res.clarify.reason);
      c.role_view = roleView(c, designation);
      bumpUsage(subject, c);
      evAnswer(subject, designation, question, c);  // T132 — ledger the clarify
      return Promise.resolve(c);
    }
    var rq = res.question;  // the (possibly rewritten) question to retrieve on
    // T129 — when the admin has deactivated a source, the baked-answer cache and
    // the fuzzy lookup are bypassed (they don't know a source is off), so the
    // question is answered by LIVE retrieval, which excludes the disabled source.
    // With every source enabled (the default), the fast baked path is used.
    var anyOff = Object.keys(disabledSources()).length > 0;
    var baked = anyOff ? Promise.resolve({ answer: null }) : bakedAnswer(rq);
    return baked.then(function (b) {
      var a = b.answer;
      if (!a) {
        // 2) in-browser retrieval and facts. A snapshot answer is persona-agnostic;
        // live retrieval applies the persona's emphasis + depth.
        a = (anyOff ? null : lookup(rq)) || factsAnswer(rq) || retrieve(rq, prof);
        if (!a) {
          var s2 = subjectInText(rq);
          if (s2) a = clarifyAnswer(s2, question, turns.map(function (t) { return t.question; }));
        }
        if (!a) a = gapAnswer(question);
        // T92 — every question is answered in place by the open-source path; no
        // GitHub-issue detour. A baked fluent answer (from the bake workflow) is
        // served above when present; otherwise this composed answer stands as-is.
      }
      if (res.understood_as) a.understood_as = res.understood_as;  // shown under the bubble
      a.role_view = roleView(a, designation);  // T27 — the designation/persona lens
      answerFirst(a, designation);  // T81/T84/T85 — result + Explain offers + governance line
      bumpUsage(subject, a);
      bumpMeter(a);  // T130 — live tokens/cost meter (per browser), never a key
      evAnswer(subject, designation, question, a);  // T132 — row + model-call ledger
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

  // ---- T130: live in-browser consumption meter --------------------------
  // The build bakes REAL provider tokens/cost from the driven Q&A (showcase.yml
  // builds with the Anthropic key when the provider check passes). On the static
  // page every visitor question is answered by the open-source / extractive path,
  // which still counts real tokens and imputes its self-hosted compute cost onto
  // the answer (a.tokens_in / a.tokens_out / a.cost — T125). We accumulate those
  // per browser so the Admin → Models tokens/cost and the ROI page GROW LIVE as
  // the visitor asks. The Anthropic key never ships to the page; the meter only
  // ever sums what an answer already carries.
  function r6(x) { return Number((Number(x) || 0).toFixed(6)); }
  function loadMeter() {
    try { return JSON.parse(localStorage.getItem("kf.consumption") || "null"); } catch (e) { return null; }
  }
  function saveMeter(m) {
    try { localStorage.setItem("kf.consumption", JSON.stringify(m)); } catch (e) {}
  }
  function emptyMeter() {
    return { calls: 0, input_tokens: 0, output_tokens: 0, cost_usd: 0, by_model: {}, by_day: {}, last_calls: [] };
  }
  function bumpMeter(a) {
    // Only a delivered answer consumes; clarifies and gaps carry no tokens.
    if (!a || a.kind !== "answer") return;
    var tin = a.tokens_in || 0, tout = a.tokens_out || 0, cost = a.cost || 0;
    if (!tin && !tout && !cost) return;
    var m = loadMeter() || emptyMeter();
    var model = a.model_name || "Open-source LLM · Extractive-NLG";
    var day = new Date().toISOString().slice(0, 10);
    m.calls += 1; m.input_tokens += tin; m.output_tokens += tout; m.cost_usd = r6(m.cost_usd + cost);
    var bm = m.by_model[model] || (m.by_model[model] = { calls: 0, input_tokens: 0, output_tokens: 0, cache_read: 0, cost_usd: 0 });
    bm.calls += 1; bm.input_tokens += tin; bm.output_tokens += tout; bm.cost_usd = r6(bm.cost_usd + cost);
    var bd = m.by_day[day] || (m.by_day[day] = { calls: 0, input_tokens: 0, output_tokens: 0, cache_read: 0, cache_write: 0, cost_usd: 0 });
    bd.calls += 1; bd.input_tokens += tin; bd.output_tokens += tout; bd.cost_usd = r6(bd.cost_usd + cost);
    m.last_calls.unshift({
      ts: new Date().toISOString(), purpose: "answer", model: model, workflow: "showcase",
      input_tokens: tin, output_tokens: tout, cache_read_input_tokens: 0, latency_ms: a.latency_ms || 0,
      cost_usd: cost, request_id: "live-" + Date.now().toString(36)
    });
    m.last_calls = m.last_calls.slice(0, 25);
    saveMeter(m);
  }
  function foldRows(baked, live) {
    var out = clone(baked) || {};
    Object.keys(live || {}).forEach(function (k) {
      var b = out[k] || (out[k] = { calls: 0, input_tokens: 0, output_tokens: 0, cache_read: 0, cache_write: 0, cost_usd: 0 });
      var l = live[k];
      b.calls = (b.calls || 0) + (l.calls || 0);
      b.input_tokens = (b.input_tokens || 0) + (l.input_tokens || 0);
      b.output_tokens = (b.output_tokens || 0) + (l.output_tokens || 0);
      b.cost_usd = r6((b.cost_usd || 0) + (l.cost_usd || 0));
    });
    return out;
  }
  // Merge the live meter into the baked GET /admin/models payload so the tokens /
  // cost totals, per-model / per-day tables and recent-calls list grow as asked.
  function mergeModels(payload) {
    var m = loadMeter(); if (!m || !m.calls) return payload;
    payload = clone(payload) || {};
    var c = payload.consumption = payload.consumption || {};
    var t = c.totals = c.totals || {};
    t.calls = (t.calls || 0) + m.calls;
    t.input_tokens = (t.input_tokens || 0) + m.input_tokens;
    t.output_tokens = (t.output_tokens || 0) + m.output_tokens;
    t.cost_usd = r6((t.cost_usd || 0) + m.cost_usd);
    c.by_model = foldRows(c.by_model, m.by_model);
    c.by_day = foldRows(c.by_day, m.by_day);
    c.last_calls = m.last_calls.concat(c.last_calls || []).slice(0, 50);
    var tel = payload.telemetry;
    if (tel && tel.totals) {
      tel.totals.calls = (tel.totals.calls || 0) + m.calls;
      tel.totals.input_tokens = (tel.totals.input_tokens || 0) + m.input_tokens;
      tel.totals.output_tokens = (tel.totals.output_tokens || 0) + m.output_tokens;
      tel.totals.cost_usd = r6((tel.totals.cost_usd || 0) + m.cost_usd);
    }
    return payload;
  }
  // Merge the live meter into the baked GET /admin/overview payload so the ROI
  // page (value delivered, spend, ratio) recomputes live off real consumption.
  function mergeOverview(payload) {
    // Always overlay the saved ROI knobs (so ROI-save reflects even before the
    // first live question); fold the live meter when there is one.
    var m = loadMeter() || emptyMeter();
    payload = clone(payload) || {};
    var val = payload.value = payload.value || {};
    var cost = payload.cost = payload.cost || {};
    var roi = payload.roi = payload.roi || {};
    var set = effectiveSettings();  // T133 — the saved ROI knobs drive the money math
    payload.settings = set;
    val.questions_answered = (val.questions_answered || 0) + m.calls;
    val.tokens_in = (val.tokens_in || 0) + m.input_tokens;
    val.tokens_out = (val.tokens_out || 0) + m.output_tokens;
    val.total_tokens = val.tokens_in + val.tokens_out;
    val.tokens_per_answer = val.questions_answered ? Number((val.total_tokens / val.questions_answered).toFixed(1)) : 0;
    var minutes = Number(set.minutes_saved_per_question) || 0;
    var rate = Number(set.loaded_rate_per_hour) || 0;
    val.hours_saved = Number((val.questions_answered * minutes / 60.0).toFixed(2));
    val.labour_value_usd = Number((val.hours_saved * rate).toFixed(2));
    var spend = r6((cost.total_spend_usd || 0) + m.cost_usd);
    cost.total_spend_usd = spend;
    cost.cost_per_answer_usd = val.questions_answered ? r6(spend / val.questions_answered) : 0;
    // Value delivered = the same real token volume at the frontier model's rate
    // (the baked per-Mtok rates), recomputed off the combined token totals.
    var fin = Number(roi.frontier_input_per_mtok) || 3.0;
    var fout = Number(roi.frontier_output_per_mtok) || 15.0;
    var value = r6(val.tokens_in * fin / 1e6 + val.tokens_out * fout / 1e6);
    roi.value_delivered_usd = value;
    roi.spend_usd = spend;
    roi.ratio = spend ? Number((value / spend).toFixed(2)) : null;
    roi.labour_value_usd = val.labour_value_usd;
    var per = adoptionPerUser(payload);
    payload.adoption = payload.adoption || {};
    payload.adoption.questions_per_user = per ? Number((val.questions_answered / per).toFixed(2)) : val.questions_answered;
    return payload;
  }
  function adoptionPerUser(payload) {
    var ad = payload.adoption || {};
    if (ad.by_user && typeof ad.by_user === "object") return Object.keys(ad.by_user).length || 1;
    return ad.active_users || 1;
  }
  // T133 — the ROI knobs (minutes saved / loaded rate) persist per browser, so the
  // ROI-save button really changes the value and spend math on the next read.
  function loadSettings() {
    try { return JSON.parse(localStorage.getItem("kf.settings") || "{}") || {}; } catch (e) { return {}; }
  }
  function bakedSettings() {
    var s = pickGet("admin@demo", "/admin/settings", null);
    return (s && typeof s === "object") ? s : { minutes_saved_per_question: 8, loaded_rate_per_hour: 75 };
  }
  function effectiveSettings() { return Object.assign({}, bakedSettings(), loadSettings()); }
  function saveSettingsPatch(patch) {
    var cur = loadSettings();
    ["minutes_saved_per_question", "loaded_rate_per_hour"].forEach(function (k) {
      if (patch && patch[k] != null && !isNaN(Number(patch[k])) && Number(patch[k]) >= 0) cur[k] = Number(patch[k]);
    });
    try { localStorage.setItem("kf.settings", JSON.stringify(cur)); } catch (e) {}
    evChanged("settings");
    return effectiveSettings();
  }

  // ---- T133: relive the Admin & Curator panels from the event ledger --------
  // The panels are baked (served via pickGet); these fold the live event ledger
  // over the baked payload — filtered to the ACTIVE connector set — so every
  // number moves as the visitor asks, syncs, toggles or curates. Each preserves
  // the baked shape (clone + overwrite specific fields), so a renderer never loses
  // a field; a window with no events keeps the baked baseline (never a placeholder).
  function evByKind(kind) { return events().filter(function (e) { return e.kind === kind; }); }
  function pctl(arr, p) {
    if (!arr.length) return 0;
    var s = arr.slice().sort(function (a, b) { return a - b; });
    return Math.round(s[Math.min(s.length - 1, Math.floor((p / 100) * s.length))]);
  }
  function slaRows(ans, keyFn) {
    var by = {};
    ans.forEach(function (e) {
      var k = keyFn(e); if (!k) return;
      (by[k] = by[k] || []).push(e);
    });
    return Object.keys(by).map(function (k) {
      var rows = by[k], lat = rows.map(function (e) { return e.latency_ms || 0; }).filter(Boolean);
      var fast = rows.filter(function (e) { return e.path !== "agent"; }).length;
      var agent = rows.length - fast;
      var cost = rows.reduce(function (s, e) { return s + (e.cost || 0); }, 0);
      var expl = rows.filter(function (e) { return e.explained; }).length;
      return { key: k, n: rows.length, p50_ms: pctl(lat, 50), p95_ms: pctl(lat, 95),
        fast_share: Number((fast / rows.length).toFixed(3)), agent_share: Number((agent / rows.length).toFixed(3)),
        explain_rate: Number((expl / rows.length).toFixed(3)), cost_per_answer: Number((cost / rows.length).toFixed(6)) };
    });
  }
  function mergeObservability(payload) {
    var ans = evByKind("answer"); if (!ans.length) return payload;
    payload = clone(payload) || {};
    var live = ans.slice().reverse().map(function (e) {
      var status = e.answer_kind || "answer";
      return { trace_id: e.trace_id || "", subject: e.subject || "", level: e.level_name || "",
        latency_ms: e.latency_ms || 0, cost_usd: e.cost || 0, error: status !== "answer", kind: status };
    });
    payload.traces = live.concat(payload.traces || []).slice(0, 200);
    payload.answers = (payload.answers || 0) + ans.length;
    var bad = ans.filter(function (e) { return (e.answer_kind || "answer") !== "answer"; }).length;
    payload.error_rate = payload.answers ? Number((bad / payload.answers).toFixed(4)) : payload.error_rate || 0;
    var lat = ans.map(function (e) { return e.latency_ms || 0; }).filter(Boolean);
    if (lat.length) { payload.latency_p50_ms = pctl(lat, 50); payload.latency_p95_ms = pctl(lat, 95); }
    return payload;
  }
  function mergeServiceLevels(payload) {
    var ans = evByKind("answer"); if (!ans.length) return payload;
    payload = clone(payload) || {};
    var h = payload.headline = payload.headline || {};
    var fast = ans.filter(function (e) { return e.path !== "agent"; });
    var flat = fast.map(function (e) { return e.latency_ms || 0; }).filter(Boolean);
    var alat = ans.filter(function (e) { return e.path === "agent"; }).map(function (e) { return e.latency_ms || 0; }).filter(Boolean);
    if (flat.length) { h.fast_p50_ms = pctl(flat, 50); h.fast_p95_ms = pctl(flat, 95); }
    if (alat.length) { h.agent_p50_ms = pctl(alat, 50); h.agent_p95_ms = pctl(alat, 95); }
    h.fast_share = Number((fast.length / ans.length).toFixed(3));
    h.agent_share = Number(((ans.length - fast.length) / ans.length).toFixed(3));
    h.cost_per_answer = Number((ans.reduce(function (s, e) { return s + (e.cost || 0); }, 0) / ans.length).toFixed(6));
    payload.n_answers = (payload.n_answers || 0) + ans.length;
    payload.by_persona = slaRows(ans, function (e) { return e.role || "general"; }).map(function (r) {
      return { persona: r.key, n: r.n, p50_ms: r.p50_ms, p95_ms: r.p95_ms, fast_share: r.fast_share,
        agent_share: r.agent_share, explain_rate: r.explain_rate, cost_per_answer: r.cost_per_answer };
    });
    var flatSrc = [];
    ans.forEach(function (e) { (e.sources || []).forEach(function (s) { flatSrc.push(Object.assign({ _s: s }, e)); }); });
    payload.by_data_type = slaRows(flatSrc, function (e) { return e._s; }).map(function (r) {
      return { data_type: r.key, n: r.n, p50_ms: r.p50_ms, p95_ms: r.p95_ms, fast_share: r.fast_share,
        agent_share: r.agent_share, cost_per_answer: r.cost_per_answer };
    });
    return payload;
  }
  function liveRuns(baked) {
    // /admin/runs is not baked (null) — synthesise the pipeline-run list from the
    // sync events the visitor triggered, newest first.
    var runs = evByKind("sync").slice().reverse().map(function (e) {
      var items = (e.pulled || 0) || (e.ingested || 0);
      return { id: e.id, source: e.source || "", status: "ok", items: items,
        duration_ms: Math.max(120, items * 40), started_at: e.ts };
    });
    return { runs: runs.slice(0, 12) };
  }
  function liveAudit(baked) {
    // /admin/audit is not baked (null) — the audit trail is the whole ledger.
    var rows = events().slice().reverse().slice(0, 40).map(function (e) {
      return { at: e.ts, subject: e.subject || e.actor || "system", is_agent: e.path === "agent",
        action: e.kind, resource: e.source || e.document_id || e.trace_id || "",
        decision: e.answer_kind || e.action || e.verdict || "" };
    });
    return { audit: rows };
  }
  function mergeSources(payload) {
    payload = clone(payload) || {};
    var bySrc = activeDocCountBySource();
    var lastSync = {};
    evByKind("sync").forEach(function (e) { if (e.source) lastSync[e.source] = e.ts; });
    var off = disabledSources();
    ["github", "jira", "confluence"].forEach(function (s) {
      var card = payload[s] = payload[s] || { counts: {} };
      card.enabled = !off[s];
      if (bySrc[s]) { card.items = bySrc[s].passages; card.counts = card.counts || {}; card.counts.documents = bySrc[s].documents; }
      if (!card.enabled) { card.items = 0; }
      if (lastSync[s]) { card.last_run = lastSync[s] / 1000; card.last_status = "ok"; }
    });
    return payload;
  }
  function activeFraction() {
    var full = (((SNAP.index || {}).passages) || []).length || 1;
    return Math.max(0, Math.min(1, activePassages().length / full));
  }
  function mergeQuality(payload) {
    payload = clone(payload) || {};
    // The readiness rings scale with the ACTIVE fraction, so turning a source off
    // visibly drops coverage / connectedness — and live gap questions raise `gaps`.
    var f = activeFraction();
    ["coverage", "connectedness", "traceability", "freshness"].forEach(function (k) {
      if (typeof payload[k] === "number") payload[k] = Number((payload[k] * f).toFixed(4));
    });
    var gapQs = {};
    evByKind("answer").forEach(function (e) {
      if ((e.answer_kind === "gap" || e.answer_kind === "clarify") && e.question) gapQs[e.question.toLowerCase()] = 1;
    });
    payload.gaps = (payload.gaps || 0) + Object.keys(gapQs).length;
    return payload;
  }
  function mergeGaps(payload) {
    payload = clone(payload) || {};
    var seen = {}, live = [];
    evByKind("answer").forEach(function (e) {
      if (e.answer_kind !== "gap" && e.answer_kind !== "clarify") return;
      var q = (e.question || "").trim(); if (!q) return;
      var key = q.toLowerCase();
      if (seen[key]) { seen[key].count += 1; return; }
      var row = { topic: q.slice(0, 60), question: q, count: 1, kind: e.answer_kind, department: e.role || "" };
      seen[key] = row; live.push(row);
    });
    if (live.length) payload.gaps = live.concat(payload.gaps || []);
    return payload;
  }
  function mergeTimeline(payload) {
    payload = clone(payload) || {};
    var acts = events().filter(function (e) {
      return e.kind === "curation" || e.kind === "sync" || e.kind === "source_toggle";
    });
    if (!acts.length) return payload;
    var y = new Date().getFullYear();
    var byMonth = {};
    acts.forEach(function (e) {
      var d = new Date(e.ts); if (d.getFullYear() !== (payload.year || y)) return;
      var mo = d.getMonth();
      byMonth[mo] = byMonth[mo] || { month: mo + 1, curation: 0, sync: 0, source_toggle: 0 };
      byMonth[mo][e.kind] += 1;
    });
    var live = Object.keys(byMonth).map(function (k) { return byMonth[k]; });
    if (live.length) payload.rows = live.concat(payload.rows || []);
    return payload;
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
  // A span waterfall for one trace: from the answer's reasoning steps when we
  // still hold it, else a single retrieval span so the panel renders honestly.
  function waterfallFor(tid, a) {
    if (!a) return { trace_id: tid, spans: [], total_ms: 0, note: "trace not in this session" };
    var total = a.latency_ms || a._ms || 0, off = 0, spans = [];
    var steps = (a.reasoning && a.reasoning.steps) || [];
    if (steps.length) {
      var each = total ? total / steps.length : 0;
      steps.forEach(function (s, i) {
        spans.push({ name: s.question || s.id || "step " + (i + 1), stage: s.kind || "reason",
          offset_ms: Math.round(off), duration_ms: Math.round(each) });
        off += each;
      });
    } else {
      spans.push({ name: "retrieve + compose", stage: (a.why && a.why.level_name) || "answer",
        offset_ms: 0, duration_ms: total });
    }
    return { trace_id: tid, spans: spans, total_ms: total };
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
      // T117 — the real password endpoint. The static showcase has no user
      // store, so email is the subject and the same demo/seed/SSO rules apply;
      // an unknown or bad-password account is a 401 (never a passwordless entry).
      if (path === "/api/auth/login") {
        var ar = resolveLogin(body.email, body.password);
        if (ar.login) return respond(ar.login);
        if (ar.code === 400) return respond({ error: "enter your email" }, 400);
        return respond({ error: "invalid credentials" }, 401);
      }
      if (path === "/ask") return answerFor(subject, body.question || "", body.context)
        .then(function (a) { if (a && a.trajectory_id) EXPLAINS[a.trajectory_id] = a; return respond(a); });
      if (path === "/api/explain") {
        var ea = EXPLAINS[(body || {}).trace_id];
        if (!ea) return respond({ trace_id: (body || {}).trace_id, explanation: "", error: "unknown or expired trace" });
        evAppend("explain", { trace_id: (body || {}).trace_id || "", tokens_in: ea.tokens_in || 0,
          tokens_out: ea.tokens_out || 0, cost: ea.cost || 0 });  // T132
        return respond(buildExplain(ea));
      }
      if (path === "/curator/decision") {
        applyDecision(body);
        evAppend("curation", { action: body.decision, document_id: body.document_id || "",
          reason: body.reason || "", actor: subject, mode: body.mode || "" });  // T132
        return respond({ ok: true, decision: body.decision });
      }
      if (path === "/admin/users") return respond(userMutation(body));
      if (path === "/admin/settings") return respond(saveSettingsPatch(body || {}));  // T133 — persist ROI knobs
      if (path === "/feedback") {
        recordFeedback(subject, body);
        evAppend("feedback", { trace_id: body.trace_id || "", verdict: body.verdict || body.value || "",
          category: body.category || "", prev_question: body.question || "" });  // T132
        return respond({ ok: true });
      }
      if (path === "/curator/repository/delete") {
        var rd = deleteRepository(body);
        evAppend("curation", { action: "delete-repository", document_id: body.repo || "", actor: subject });
        return rd;  // T47
      }
      if (path === "/curator/tables/query") return tableQuery(body);             // T47
      // T129 — connectors are REAL in-browser switches (persisted per visitor):
      // Save/toggle, Sync now, Run-due and delete all mutate state and return a
      // realistic payload so the toast and cards update as on a live server.
      if (path === "/admin/connectors") {
        var sc = saveConn((body || {}).source, body || {});
        var scn = sc && sc.connector;
        evAppend("source_toggle", { source: (body || {}).source || "",
          active: scn ? scn.enabled !== false : true, actor: subject });  // T132
        return respond(sc);
      }
      if (path === "/admin/sync") {
        var sy = syncConn((body || {}).source);
        evAppend("sync", { source: (body || {}).source || "", pulled: sy.pulled || 0,
          ingested: sy.ingested || 0, tombstoned: sy.tombstoned || 0 });  // T132
        return respond(sy);
      }
      if (path === "/admin/refresh/run-due") {
        var rdue = runDueConn();
        (rdue.ran || []).forEach(function (src) { evAppend("sync", { source: src, pulled: 0, ingested: 0 }); });
        return respond(rdue);
      }
      if (path === "/admin/connectors/delete") {
        var dc = deleteConn((body || {}).source);
        evAppend("source_toggle", { source: (body || {}).source || "", active: false, actor: subject });
        return respond(dc);
      }
      // T131 — uploads are REAL in-browser: the file is added to the Files source
      // at once (answerable immediately) and, when a commit endpoint is configured,
      // POSTed there to land in the repo. Delete removes it from both.
      if (path === "/admin/upload" || path === "/curator/upload") {
        return uploadDocs((body || {}).files || []).then(function (up) {
          (up.documents || []).forEach(function (t) {
            evAppend("curation", { action: "upload", document_id: t, title: t, actor: subject });
          });
          return respond(up);
        });
      }
      if (path === "/admin/uploads/delete") {
        var du = deleteUpload((body || {}).document_id || (body || {}).doc || "");
        evAppend("curation", { action: "delete-document", document_id: du.document_id || "", actor: subject });
        return respond(du);
      }
      // bulk-delete / budget / authority — demo success
      return respond({ ok: true, note: "showcase — action acknowledged (no server-side state on Pages)" });
    }
    // GET
    // T117 — the static build always runs in dev-login mode (no OIDC on Pages),
    // so the sign-in page keeps its email+password form and the dev picker.
    if (path === "/api/auth/config") return respond({ mode: "password", sso: { enabled: false, label: "SSO" }, dev_login: true });
    if (path === "/api/auth/whoami") {
      if (!subject) return respond({ error: "not signed in" }, 401);
      var wl = loginOf(subject) || {};
      return respond({ subject: subject, roles: wl.roles || ["asker"], scopes: wl.scopes || ["public"], designation: wl.designation || "" });
    }
    // T129 — the connectors card list reflects the visitor's live on/off + sync
    // state (and hides a deleted source), so a reload shows what they changed.
    if (path === "/admin/connectors") return respond(connectorsPayload());
    if (path === "/admin/uploads") return respond(uploadsPayload());  // T131
    if (path === "/api/corpus") return respond(corpusCounts());  // T132 — live counts
    // T82 — suggestions are baked per subject (so the reader's persona known
    // questions show) with a scope-key fallback for older snapshots.
    if (path === "/api/suggestions") return respond((SNAP.suggestions || {})[subject] || (SNAP.suggestions || {})[scopeKey(subject)] || { suggestions: [] });
    if (path === "/api/usage") return respond(STATE.usage[subject] || emptyUsage(subject));
    if (path === "/api/galaxy") { var t = q.get("trace_id"); return respond((SNAP.galaxy || {})[t] || { trace_id: t, nodes: [], edges: [], activated_ids: [], halo_ids: [], stats: {} }); }
    if (path === "/api/galaxy/node") { var nid = q.get("id"); var nd = (SNAP.galaxy_nodes || {})[nid]; return nd ? respond(nd) : respond({ error: "unknown node" }, 404); }
    if (path === "/api/galaxy/full") return respond(SNAP.galaxy_full || { nodes: [], edges: [], activated_ids: [], halo_ids: [], stats: {} });
    if (path === "/api/provider") return respond(SNAP.provider || { provider: "Extractive", model: "core", dot: "#5A6B7C", label: "Extractive core" });
    if (path === "/api/analytics") { var w = q.get("window") || "7d"; return respond((SNAP.analytics || {})[w] || (SNAP.analytics || {})["7d"] || {}); }
    if (path === "/api/events") return respond({ events: SNAP.events || [] });
    if (path === "/api/trace") return respond({ spans: [] });
    // T93 — a cited passage's paragraph + neighbours, when the build baked them;
    // otherwise 404 and the Workspace keeps the cited snippet as the paragraph.
    if (path.indexOf("/api/passage/") === 0) {
      var pcid = decodeURIComponent(path.slice("/api/passage/".length));
      var pc = (SNAP.passages || {})[pcid];
      return pc ? respond(pc) : respond({ error: "passage not baked" }, 404);
    }
    if (path === "/curator/versions") { var d = q.get("document_id"); return respond((SNAP.versions || {})[d] || { versions: [], datasets: [] }); }
    if (path === "/admin/doctor") { var tg = q.get("target") || ""; return respond((SNAP.doctor || {})[tg] || (SNAP.doctor || {})[""] || { checks: [] }); }
    if (path === "/curator/feedback") return respond({ feedback: STATE.feedback }); // negative-feedback review (new)
    if (path === "/curator/repository") return repositoryCard(q.get("repo") || "");  // T47 card overlay
    // T130 — fold the live in-browser consumption meter over the baked payloads
    // so the Models tokens/cost and the ROI page grow live as the visitor asks.
    if (path === "/admin/models") { var md = pickGet(subject, path, q); if (md !== undefined) return respond(mergeModels(md)); }
    if (path === "/admin/overview") { var ov = pickGet(subject, path, q); if (ov !== undefined) return respond(mergeOverview(ov)); }
    // T133 — the Admin & Curator panels recompute from the live event ledger.
    if (path === "/admin/observability") {
        var tid = q.get("trace_id");
        if (tid) { var ex = EXPLAINS[tid]; return respond(waterfallFor(tid, ex)); }
        var obs = pickGet(subject, path, q); if (obs !== undefined) return respond(mergeObservability(obs));
    }
    if (path === "/admin/service-levels") { var sl = pickGet(subject, path, q); if (sl !== undefined) return respond(mergeServiceLevels(sl)); }
    if (path === "/admin/runs") return respond(liveRuns(pickGet(subject, path, q)));
    if (path === "/admin/audit") return respond(liveAudit(pickGet(subject, path, q)));
    if (path === "/admin/sources") { var so = pickGet(subject, path, q); if (so !== undefined) return respond(mergeSources(so)); }
    if (path === "/curator/quality") { var qy = pickGet(subject, path, q); if (qy !== undefined) return respond(mergeQuality(qy)); }
    if (path === "/curator/gaps") { var gp = pickGet(subject, path, q); if (gp !== undefined) return respond(mergeGaps(gp)); }
    if (path === "/curator/timeline") { var tl = pickGet(subject, path, q); if (tl !== undefined) return respond(mergeTimeline(tl)); }
    if (path === "/admin/settings") return respond(effectiveSettings());  // T133 — live ROI knobs
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
