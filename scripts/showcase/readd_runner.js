/* T143 — delete a source, then re-add it, driving the ACTUAL shipped engine.js
 * under a Node browser shim over a built snapshot. Proves the defect is fixed:
 * a deleted source is NOT lost — it stays listed in a removed state and a restore
 * route (POST /admin/connectors/add) brings its answers back. Repeated twice, so
 * the cycle is idempotent.
 *
 * For each cycle it asserts, with no reload:
 *   • baseline: the github card is present and active, `repositories` > 0, and
 *     "how many repositories …" answers;
 *   • after delete: the github card is STILL listed but flagged `_deleted` (the
 *     removed state), the active connector count drops by one, `repositories` is
 *     0, and the repositories question no longer answers (it is a gap);
 *   • after re-add: the github card is active again, the active count is restored,
 *     `repositories` returns to its baseline, and the question answers again.
 *
 * argv: <engine.js> <snapshot.json>
 * Prints a JSON result: { ok: bool, checks: [{name, ok, extra}] }.
 */
"use strict";
const fs = require("fs");
const [enginePath, snapPath] = process.argv.slice(2);
const SNAP = JSON.parse(fs.readFileSync(snapPath, "utf8"));

const store = {}, sess = {};
globalThis.localStorage = { getItem: (k) => (k in store ? store[k] : null), setItem: (k, v) => { store[k] = String(v); }, removeItem: (k) => { delete store[k]; } };
globalThis.sessionStorage = { getItem: (k) => (k in sess ? sess[k] : null), setItem: (k, v) => { sess[k] = String(v); }, removeItem: (k) => { delete sess[k]; } };
globalThis.location = { pathname: "/admin/", origin: "http://t.local", href: "http://t.local/admin/" };
globalThis.KF_SURFACE = "admin";
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
const TOKEN = ((SNAP.login || {}).admin || {}).token || "";
function req(m, p, b) { const o = { method: m, headers: { Authorization: "Bearer " + TOKEN } }; if (b) { o.body = JSON.stringify(b); o.headers["Content-Type"] = "application/json"; } return globalThis.fetch(p, o).then((r) => r.json()); }
const conns = () => req("GET", "/admin/connectors").then((d) => d.connectors || []);
const ghCard = async () => (await conns()).find((c) => c.source === "github") || null;
const activeCount = async () => (await conns()).filter((c) => !c._deleted).length;
const repos = () => req("GET", "/api/corpus").then((c) => c.repositories || 0);
// When github is on, this is answered at Level 0 from facts.json (the repository
// count); when github is off, that facts path gaps and the question is no longer a
// facts count. The level is the precise signal that the source's answers left.
const reposLevel = () => req("POST", "/ask", { question: "how many repositories does QualiZeal have?" }).then((a) => (a.why && a.why.level_name) || a.kind || "");

const checks = [];
function check(name, ok, extra) { checks.push({ name, ok: !!ok, extra: extra == null ? "" : String(extra) }); }

async function cycle(n) {
  const tag = "cycle " + n + ": ";
  // baseline
  const gh0 = await ghCard(), a0 = await activeCount(), r0 = await repos(), l0 = await reposLevel();
  check(tag + "github listed and active at start", gh0 && !gh0._deleted, gh0 && ("_deleted=" + gh0._deleted));
  check(tag + "repositories > 0 at start", r0 > 0, "repositories=" + r0);
  check(tag + "repositories question is a facts count at start", l0 === "facts", "level=" + l0);

  // delete
  const dr = await req("POST", "/admin/connectors/delete", { source: "github" });
  const ghD = await ghCard(), aD = await activeCount(), rD = await repos(), lD = await reposLevel();
  check(tag + "delete returns ok", dr && dr.status === "ok", dr && dr.status);
  check(tag + "github STILL listed but flagged removed", ghD && ghD._deleted === true, ghD && ("_deleted=" + (ghD ? ghD._deleted : "gone")));
  check(tag + "active connector count drops by one", aD === a0 - 1, a0 + "→" + aD);
  check(tag + "repositories tile is 0 while removed", rD === 0, "repositories=" + rD);
  check(tag + "repositories facts count is gone while removed", lD !== "facts", "level=" + lD);

  // re-add
  const ar = await req("POST", "/admin/connectors/add", { source: "github" });
  const ghA = await ghCard(), aA = await activeCount(), rA = await repos(), lA = await reposLevel();
  check(tag + "re-add returns ok", ar && ar.status === "ok", ar && ar.status);
  check(tag + "github active again after re-add", ghA && !ghA._deleted && ghA.enabled !== false, ghA && ("_deleted=" + ghA._deleted + " enabled=" + ghA.enabled));
  check(tag + "active connector count restored", aA === a0, a0 + "→" + aA);
  check(tag + "repositories tile restored to baseline", rA === r0, r0 + "→" + rA);
  check(tag + "repositories facts count answers again", lA === "facts", "level=" + lA);
}

async function run() {
  await new Promise((r) => setTimeout(r, 0));
  if (!TOKEN) throw new Error("no admin token in snapshot");
  await cycle(1);
  await cycle(2);
  const ok = checks.every((c) => c.ok);
  process.stdout.write(JSON.stringify({ ok, checks }));
}
run().catch((e) => { process.stdout.write(JSON.stringify({ ok: false, error: String(e && e.stack ? e.stack : e), checks })); process.exit(1); });
