/* T158 — drive the ACTUAL shipped engine.js under a Node browser shim and prove the
 * repeat-answer cache + cost-saved ledger behave as specified:
 *
 *  - the same question asked twice within the same active source set: the second is
 *    a cache hit at Level 0, $0, model "cache";
 *  - every delivered answer books cost_saved (the top-tier cost it avoided), tagged
 *    by bucket (repeat-cache / level-selection), and the live usage cost_saved rises;
 *  - toggling a cited source off changes the active-set fingerprint and invalidates
 *    the entry, so the next ask is NOT a cache hit.
 *
 * Used by tests/unit/test_repeat_cache.py.
 *
 * argv: <showcase-dir> <question>
 * Prints: a JSON object of observations for the Python gate to assert.
 */
"use strict";
const fs = require("fs"),
  path = require("path");
const SC = process.argv[2];
const Q = process.argv[3] || "what is QMentisAI";
const SNAP = JSON.parse(fs.readFileSync(path.join(SC, "snapshot.json"), "utf8"));

const store = {},
  sess = {};
globalThis.localStorage = {
  getItem: (k) => (k in store ? store[k] : null),
  setItem: (k, v) => {
    store[k] = String(v);
  },
  removeItem: (k) => {
    delete store[k];
  },
};
globalThis.sessionStorage = {
  getItem: (k) => (k in sess ? sess[k] : null),
  setItem: (k, v) => {
    sess[k] = String(v);
  },
  removeItem: (k) => {
    delete sess[k];
  },
};
globalThis.location = { pathname: "/admin/", origin: "http://t.local", href: "http://t.local/admin/" };
globalThis.KF_SURFACE = "admin";
globalThis.CustomEvent = class {
  constructor(t, o) {
    this.type = t;
    this.detail = (o || {}).detail;
  }
};
const _l = {};
globalThis.addEventListener = (t, f) => {
  (_l[t] = _l[t] || []).push(f);
};
globalThis.dispatchEvent = (e) => {
  (_l[e.type] || []).forEach((f) => f(e));
  return true;
};
globalThis.btoa = (s) => Buffer.from(s, "binary").toString("base64");
globalThis.atob = (b) => Buffer.from(b, "base64").toString("binary");
globalThis.fetch = async function (u) {
  u = String(u);
  if (u.indexOf("snapshot.json") >= 0) return { ok: true, json: async () => SNAP };
  const m = u.match(/\/(answers\/.*\.json)$/);
  if (m) {
    const fp = path.join(SC, m[1]);
    if (fs.existsSync(fp)) return { ok: true, json: async () => JSON.parse(fs.readFileSync(fp, "utf8")) };
    return { ok: false, status: 404, json: async () => ({}) };
  }
  return { ok: true, json: async () => ({}) };
};
globalThis.window = globalThis;
globalThis.self = globalThis;
(0, eval)(fs.readFileSync(path.join(SC, "engine.js"), "utf8"));

const TOKEN = ((SNAP.login || {})["admin"] || {}).token || "";
function req(m, p, b) {
  const o = { method: m, headers: { Authorization: "Bearer " + TOKEN } };
  if (b) {
    o.body = JSON.stringify(b);
    o.headers["Content-Type"] = "application/json";
  }
  return globalThis.fetch(p, o).then((r) => r.json());
}
const rv = (a) => ({
  kind: a.kind,
  level: (a.why && a.why.level_name) || "",
  model: a.model_name,
  cost: a.cost,
  cache_hit: !!a.cache_hit,
  cost_saved: a.cost_saved || 0,
  bucket: a.saved_bucket || "",
});

async function run() {
  await new Promise((r) => setTimeout(r, 0));
  const out = { token: !!TOKEN, q: Q };
  const a1 = await req("POST", "/ask", { question: Q });
  const a2 = await req("POST", "/ask", { question: Q });
  out.first = rv(a1);
  out.second = rv(a2);

  const usage = await req("GET", "/api/usage");
  out.usage_saved_7d = (((usage.windows || {})["7d"]) || {}).cost_saved || 0;

  // A live count ("facts") answer must NEVER be cached — it has to recompute so it
  // reflects the current corpus. Ask one twice; the second must not be a cache hit.
  const F = "how many repositories are there";
  const f1 = await req("POST", "/ask", { question: F });
  const f2 = await req("POST", "/ask", { question: F });
  out.facts_level = (f1.why && f1.why.level_name) || "";
  out.facts_second_cache_hit = !!f2.cache_hit;
  out.facts_second_level = (f2.why && f2.why.level_name) || "";

  // toggle a source off → fingerprint changes → the next ask must miss the cache.
  await req("POST", "/admin/connectors", { source: "github", enabled: false });
  const a3 = await req("POST", "/ask", { question: Q });
  out.after_toggle_cache_hit = !!a3.cache_hit;

  process.stdout.write(JSON.stringify(out));
}
run().catch((e) => {
  process.stdout.write(JSON.stringify({ error: String(e && e.stack ? e.stack : e) }));
  process.exit(1);
});
