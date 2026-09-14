/* T144 — the upload drop zone really adds a document, from Admin AND from Curator.
 *
 * Drives the ACTUAL shipped engine.js under a Node browser shim (with the vendored
 * JSZip and the real upload_parse.js loaded exactly as the Admin/Curator pages load
 * them) and uploads the REAL QualiZeal SOW (Nova_x_QZ_Product_Knowledge_Fabric_SOW)
 * through the same intake the drop zone calls. The test builds a snapshot whose
 * baseline has no uploads, so the drop genuinely adds the SOW.
 *
 * For each endpoint (/admin/upload, then /curator/upload on a fresh engine) it
 * asserts, with no reload: the endpoint parses the DOCX (passages_added > 0), the
 * Workspace documents tile rises by one, and "what is in the Knowledge Fabric SOW"
 * is answered with a citation to the SOW carrying a paragraph coordinate (… · ¶N).
 *
 * argv: <engine.js> <snapshot.json> <sow.json> <jszip.min.js> <upload_parse.js>
 * sow.json = { docx_b64, filename }.
 * Prints a JSON result: { ok: bool, checks: [{name, ok, extra}] }.
 */
"use strict";
const fs = require("fs");
const [enginePath, snapPath, sowPath, jszipPath, parserPath] = process.argv.slice(2);
const SNAP = JSON.parse(fs.readFileSync(snapPath, "utf8"));
const SOW = JSON.parse(fs.readFileSync(sowPath, "utf8"));

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

// JSZip + parser as page globals (same as the Admin/Curator UPLOAD_HEAD loads them)
const jszipSrc = fs.readFileSync(jszipPath, "utf8");
const jm = { exports: {} };
(function (module, exports) { eval(jszipSrc); })(jm, jm.exports);
globalThis.JSZip = jm.exports;
globalThis.KFUpload = (function () { const m = { exports: {} }; (function (module, exports) { eval(fs.readFileSync(parserPath, "utf8")); })(m, m.exports); return m.exports; })();

function loadEngine() { for (const k in store) delete store[k]; (0, eval)(fs.readFileSync(enginePath, "utf8")); }
const TOKEN = ((SNAP.login || {}).admin || {}).token || "";
function req(m, p, b) { const o = { method: m, headers: { Authorization: "Bearer " + TOKEN } }; if (b) { o.body = JSON.stringify(b); o.headers["Content-Type"] = "application/json"; } return globalThis.fetch(p, o).then((r) => r.json()); }
const corpus = () => req("GET", "/api/corpus");
const ask = (q) => req("POST", "/ask", { question: q });

const checks = [];
function check(name, ok, extra) { checks.push({ name, ok: !!ok, extra: extra == null ? "" : String(extra) }); }

async function viaEndpoint(label, endpoint) {
  loadEngine();
  await new Promise((r) => setTimeout(r, 0));
  const c0 = await corpus();
  const up = await req("POST", endpoint, { files: [{ filename: SOW.filename, content_b64: SOW.docx_b64 }] });
  const c1 = await corpus();
  check(label + ": endpoint parsed the DOCX (passages_added > 0)", (up.passages_added || 0) > 0, "passages_added=" + up.passages_added);
  check(label + ": documents tile +1 after upload", c1.documents === c0.documents + 1, c0.documents + "→" + c1.documents);
  const a = await ask("summarize the Knowledge Fabric SOW");
  const cited = (a.citations || []).find((c) => /sow/i.test(c.document_title || ""));
  check(label + ": SOW is answered on the next question", a.kind === "answer" && !!cited, "kind=" + a.kind + (cited ? " cite=" + cited.document_title : " (no SOW citation)"));
  check(label + ": SOW citation carries a paragraph coordinate", cited && /¶\d+|Table \d+/.test(cited.coordinate_render || ""), cited && cited.coordinate_render);
}

async function run() {
  if (!TOKEN) throw new Error("no admin token in snapshot");
  await viaEndpoint("admin drop", "/admin/upload");
  await viaEndpoint("curator drop", "/curator/upload");
  const ok = checks.every((c) => c.ok);
  process.stdout.write(JSON.stringify({ ok, checks }));
}
run().catch((e) => { process.stdout.write(JSON.stringify({ ok: false, error: String(e && e.stack ? e.stack : e), checks })); process.exit(1); });
