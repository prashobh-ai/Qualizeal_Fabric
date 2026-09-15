/* T156 — drive the ACTUAL shipped engine.js under a Node browser shim and prove the
 * dashboard analytics are LIVE: /api/analytics folds the visitor's own questions
 * (the kf.events ledger) onto the baked series, so every chart the dashboard draws
 * grows as the visitor asks. Asserts the answer count, cost-saved, savings breakdown,
 * timeseries tail, per-user rollup, model distribution and latency series all move.
 *
 * Used by tests/unit/test_analytics_live.py.
 *
 * argv: <showcase-dir>
 * Prints: a JSON object of before/after analytics observations for the Python gate.
 */
"use strict";
const fs = require("fs"),
  path = require("path");
const SC = process.argv[2];
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
globalThis.location = { pathname: "/dashboard/", origin: "http://t.local", href: "http://t.local/dashboard/" };
globalThis.KF_SURFACE = "dashboard";
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
const snap = (a) => ({
  answers: a.answers || 0,
  cost_saved: a.total_cost_saved || 0,
  ts: (a.timeseries || []).length,
  merged: !!a.live_merged,
  techniques: Object.keys(a.savings_by_technique || {}),
  users: Object.keys(a.per_user || {}).length,
  models: Object.keys(a.models_used || {}).length,
  tail_has_latency: (a.timeseries || []).length
    ? "latency_ms" in (a.timeseries[a.timeseries.length - 1] || {})
    : false,
});

async function run() {
  await new Promise((r) => setTimeout(r, 0));
  const out = { token: !!TOKEN };
  const before = await req("GET", "/api/analytics?window=7d");
  out.before = snap(before);

  const qs = [
    "what is QMentisAI",
    "compare QMentisAI and ValidAIte for test automation",
    "what is ValidAIte",
    "what is QMentisAI", // a repeat → cache hit, cost saved via repeat-cache
  ];
  for (const q of qs) await req("POST", "/ask", { question: q });

  const after = await req("GET", "/api/analytics?window=7d");
  out.after = snap(after);
  out.savings_map = after.savings_by_technique || {};
  out.window_all = snap(await req("GET", "/api/analytics?window=all"));

  process.stdout.write(JSON.stringify(out));
}
run().catch((e) => {
  process.stdout.write(JSON.stringify({ error: String(e && e.stack ? e.stack : e) }));
  process.exit(1);
});
