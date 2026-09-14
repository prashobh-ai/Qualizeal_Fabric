/* T142 — drive the real engine.js under a Node browser shim over a built snapshot
 * and print, for each question passed on argv, the answer kind and its citations
 * (document title + coordinate path). The Python gate (test_services_question.py)
 * asserts a "what all services" question returns a document list — ten or more
 * citations, every title a service name, none a .py path.
 *
 * argv: <engine.js> <snapshot.json> <question> [<question> ...]
 * Prints one JSON object: { results: [{question, kind, level, citations:[{title, path, doc}]}] }.
 */
"use strict";
const fs = require("fs");
const [enginePath, snapPath, ...questions] = process.argv.slice(2);
const SNAP = JSON.parse(fs.readFileSync(snapPath, "utf8"));

const store = {}, sess = {};
globalThis.localStorage = { getItem: (k) => (k in store ? store[k] : null), setItem: (k, v) => { store[k] = String(v); }, removeItem: (k) => { delete store[k]; } };
globalThis.sessionStorage = { getItem: (k) => (k in sess ? sess[k] : null), setItem: (k, v) => { sess[k] = String(v); }, removeItem: (k) => { delete sess[k]; } };
// pathname + surface must cancel so the derived KF_BASE is empty (else every
// route loses its leading slash and misses the handler).
globalThis.location = { pathname: "/workspace/", origin: "http://t.local", href: "http://t.local/workspace/" };
globalThis.KF_SURFACE = "workspace";
globalThis.CustomEvent = class { constructor(t, o) { this.type = t; this.detail = (o || {}).detail; } };
const _lis = {};
globalThis.addEventListener = (t, fn) => { (_lis[t] = _lis[t] || []).push(fn); };
globalThis.dispatchEvent = (e) => { (_lis[e.type] || []).forEach((fn) => fn(e)); return true; };
globalThis.btoa = (s) => Buffer.from(s, "binary").toString("base64");
globalThis.atob = (b) => Buffer.from(b, "base64").toString("binary");
globalThis.fetch = async function (url) { const u = String(url); if (u.indexOf("snapshot.json") >= 0) return { ok: true, json: async () => SNAP }; return { ok: true, json: async () => ({}) }; };
globalThis.window = globalThis;
globalThis.self = globalThis;

(0, eval)(fs.readFileSync(enginePath, "utf8"));
const TOKEN = ((SNAP.login || {})["asker.public"] || {}).token || "";
function req(m, p, b) { const o = { method: m, headers: { Authorization: "Bearer " + TOKEN } }; if (b) { o.body = JSON.stringify(b); o.headers["Content-Type"] = "application/json"; } return globalThis.fetch(p, o).then((r) => r.json()); }

async function run() {
  await new Promise((r) => setTimeout(r, 0));
  const results = [];
  for (const q of questions) {
    const a = await req("POST", "/ask", { question: q });
    results.push({
      question: q,
      kind: a.kind,
      level: (a.why && a.why.level_name) || "",
      answer_text: a.answer_text || "",
      citations: (a.citations || []).map((c) => ({
        title: c.document_title || "",
        path: (c.coordinate && c.coordinate.locator && c.coordinate.locator.path) || "",
        render: c.coordinate_render || "",
        doc: c.document_id || "",
      })),
    });
  }
  process.stdout.write(JSON.stringify({ results }));
}
run().catch((e) => { process.stdout.write(JSON.stringify({ error: String(e && e.stack ? e.stack : e) })); process.exit(1); });
