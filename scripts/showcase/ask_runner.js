/* T150 — drive the ACTUAL shipped engine.js under a Node browser shim and print,
 * for each question, the answer kind the visitor would see. Used by
 * tests/unit/test_ask_routing.py to prove that content-rich questions (an
 * existential "is there…", an "integrate with…") are ANSWERED, not turned into a
 * coreference clarify, while a genuine bare pronoun still clarifies.
 *
 * argv: <showcase-dir> <question> [<question> …]
 * Prints: [{ q, kind, level, cites }] as JSON.
 */
"use strict";
const fs = require("fs"),
  path = require("path");
const SC = process.argv[2];
const QS = process.argv.slice(3);
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
globalThis.location = { pathname: "/workspace/", origin: "http://t.local", href: "http://t.local/workspace/" };
globalThis.KF_SURFACE = "workspace";
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

const TOKEN = ((SNAP.login || {})["asker.public"] || {}).token || "";
function req(m, p, b) {
  const o = { method: m, headers: { Authorization: "Bearer " + TOKEN } };
  if (b) {
    o.body = JSON.stringify(b);
    o.headers["Content-Type"] = "application/json";
  }
  return globalThis.fetch(p, o).then((r) => r.json());
}

async function run() {
  await new Promise((r) => setTimeout(r, 0));
  const out = [];
  for (const q of QS) {
    const a = await req("POST", "/ask", { question: q });
    out.push({
      q: q,
      kind: a.kind,
      level: (a.why && a.why.level_name) || a.tier || "",
      cites: (a.citations || []).length,
    });
  }
  process.stdout.write(JSON.stringify(out));
}
run().catch((e) => {
  process.stdout.write(JSON.stringify({ error: String(e && e.stack ? e.stack : e) }));
  process.exit(1);
});
