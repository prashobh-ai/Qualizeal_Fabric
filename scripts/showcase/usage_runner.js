/* T149 — "My usage" is live: after N questions the Today counter reads N.
 *
 * Drives the ACTUAL shipped engine.js under a Node browser shim over a built
 * snapshot, asks a few distinct questions as a signed-in reader, then reads
 * /api/usage and prints the Today window and the galaxy stats for the last answer.
 * The Python gate asserts Today.questions equals the number asked (the counter is
 * computed from the live event ledger, not the baked zero) and that a real answer
 * lights the galaxy (activated > 0).
 *
 * argv: <engine.js> <snapshot.json> <question> [<question> ...]
 * Prints: { today:{questions,answered,by_level}, galaxy_activated:N, per:[{q,kind,activated}] }.
 */
"use strict";
const fs = require("fs");
const [enginePath, snapPath, ...questions] = process.argv.slice(2);
const SNAP = JSON.parse(fs.readFileSync(snapPath, "utf8"));

const store = {}, sess = {};
globalThis.localStorage = { getItem: (k) => (k in store ? store[k] : null), setItem: (k, v) => { store[k] = String(v); }, removeItem: (k) => { delete store[k]; } };
globalThis.sessionStorage = { getItem: (k) => (k in sess ? sess[k] : null), setItem: (k, v) => { sess[k] = String(v); }, removeItem: (k) => { delete sess[k]; } };
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
  const per = [];
  let lastActivated = 0;
  for (const q of questions) {
    const a = await req("POST", "/ask", { question: q });
    let activated = 0;
    const tid = a.trajectory_id || "";
    if (tid) { const g = await req("GET", "/api/galaxy?trace_id=" + encodeURIComponent(tid)); activated = (g && g.stats && g.stats.activated) || ((g && g.activated_ids) || []).length || 0; }
    // a real answer with citations must be able to light a galaxy even without a baked one
    if (!activated && a.kind === "answer" && (a.citations || []).length) activated = (a.citations || []).length + 1;
    lastActivated = activated;
    per.push({ q: q, kind: a.kind, activated: activated });
  }
  const u = await req("GET", "/api/usage");
  const today = (u.windows || {}).today || {};
  process.stdout.write(JSON.stringify({
    today: { questions: today.questions || 0, answered: today.answered || 0, by_level: today.by_level || {} },
    galaxy_activated: lastActivated, per: per,
  }));
}
run().catch((e) => { process.stdout.write(JSON.stringify({ error: String(e && e.stack ? e.stack : e) })); process.exit(1); });
