/* T157 — drive the ACTUAL shipped engine.js under a Node browser shim and prove the
 * curator golden-answer flow: save a verified {question, answer, citations}, and the
 * fabric serves it FIRST for that question (exact and ≥ 0.9 similarity) at Level 0,
 * $0, model "golden", cited — before retrieval or any model call; delete retires it.
 *
 * Used by tests/unit/test_golden_qa.py.
 *
 * argv: <showcase-dir>
 * Prints: a JSON object of observations for the Python gate to assert.
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
globalThis.location = { pathname: "/curator/", origin: "http://t.local", href: "http://t.local/curator/" };
globalThis.KF_SURFACE = "curator";
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

const CUR = ((SNAP.login || {})["curator"] || {}).token || "";
function req(m, p, b) {
  const o = { method: m, headers: { Authorization: "Bearer " + CUR } };
  if (b) {
    o.body = JSON.stringify(b);
    o.headers["Content-Type"] = "application/json";
  }
  return globalThis.fetch(p, o).then((r) => r.json());
}
const view = (a) => ({
  level: (a.why && a.why.level_name) || "",
  cost: a.cost || 0,
  model: a.model_name || "",
  golden: !!a.golden,
  cites: (a.citations || []).length,
});

async function run() {
  const out = { token: !!CUR };
  await new Promise((r) => setTimeout(r, 0));
  const Q = "what is our Knowledge Fabric";
  const ANS = "The QualiZeal Knowledge Fabric is our governed, role-aware Q&A platform over every connected source.";

  out.before = view(await req("POST", "/ask", { question: Q }));
  const save = await req("POST", "/curator/golden", { question: Q, answer: ANS, citations: ["Fabric overview"] });
  out.save_ok = !!(save && save.ok);
  out.id = save.entry && save.entry.id;
  out.list_after_save = ((await req("GET", "/curator/golden")).items || []).length;

  out.after = view(await req("POST", "/ask", { question: Q }));
  // a lightly reworded variant (added punctuation/case) still resolves to golden.
  out.reworded = view(await req("POST", "/ask", { question: "What is our Knowledge Fabric?" }));
  // an unrelated question must NOT be served from golden.
  out.unrelated = view(await req("POST", "/ask", { question: "how many repositories are there" }));

  const del = await req("DELETE", "/curator/golden/" + encodeURIComponent(out.id));
  out.delete_ok = !!(del && del.ok);
  out.list_after_delete = ((await req("GET", "/curator/golden")).items || []).length;
  out.after_delete = view(await req("POST", "/ask", { question: Q }));

  process.stdout.write(JSON.stringify(out));
}
run().catch((e) => {
  process.stdout.write(JSON.stringify({ error: String(e && e.stack ? e.stack : e) }));
  process.exit(1);
});
