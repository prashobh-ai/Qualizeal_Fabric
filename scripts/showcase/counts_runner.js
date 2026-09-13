/* T140 — counts agree across every screen after a real in-browser upload/delete.
 *
 * Runs the ACTUAL shipped engine.js under a Node browser shim, with the vendored
 * JSZip and the real upload_parse.js loaded exactly as the pages load them. It
 * uploads a synthetic DOCX (real paragraphs + a table) and a synthetic PPTX (real
 * per-slide passages + embedded media), then asserts:
 *
 *   • the Workspace corpus tiles move by the measured amount (documents +1,
 *     passages +N, tables +T, images +I) and revert on delete — no reload;
 *   • the Admin Files connector card item count and the /admin/uploads list agree
 *     with the Workspace document delta (one source of truth, three screens);
 *   • the uploaded document is retrieved and cited on the very next question, with
 *     its real coordinate (`… · ¶N` for the DOCX, `… · slide N` for the deck);
 *   • the upload persists across a reload (fresh engine, same localStorage);
 *   • dedupe by content hash reports a re-upload as already in the fabric.
 *
 * argv: <engine.js> <snapshot.json> <office.json> <jszip.min.js> <upload_parse.js>
 * office.json = { docx_b64, pptx_b64 } (built with stdlib zipfile by the test).
 * Prints a JSON result: { ok: bool, checks: [{name, ok, extra}] }.
 */
"use strict";
const fs = require("fs");
const [enginePath, snapPath, officePath, jszipPath, parserPath] = process.argv.slice(2);
const SNAP = JSON.parse(fs.readFileSync(snapPath, "utf8"));
const OFF = JSON.parse(fs.readFileSync(officePath, "utf8"));

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
globalThis.self = globalThis; // so UMD footers (root = self) bind to the shim

// JSZip + parser as page globals (same as UPLOAD_HEAD loads them)
const jszipSrc = fs.readFileSync(jszipPath, "utf8");
const jm = { exports: {} };
(function (module, exports) { eval(jszipSrc); })(jm, jm.exports);
globalThis.JSZip = jm.exports;
globalThis.KFUpload = (function () { const m = { exports: {} }; (function (module, exports) { eval(fs.readFileSync(parserPath, "utf8")); })(m, m.exports); return m.exports; })();

function loadEngine() { (0, eval)(fs.readFileSync(enginePath, "utf8")); }
loadEngine();
const TOKEN = ((SNAP.login || {}).admin || {}).token || "";
function req(m, p, b) { const o = { method: m, headers: { Authorization: "Bearer " + TOKEN } }; if (b) { o.body = JSON.stringify(b); o.headers["Content-Type"] = "application/json"; } return globalThis.fetch(p, o).then((r) => r.json()); }
const ask = (q) => req("POST", "/ask", { question: q });
const corpus = () => req("GET", "/api/corpus");
async function filesCardItems() {
  const c = await req("GET", "/admin/connectors");
  const f = (c.connectors || []).find((x) => x.source === "files");
  return f && f.health ? (f.health.items || 0) : 0;
}
const uploadCount = () => req("GET", "/admin/uploads").then((d) => (d.uploads || []).length);

const checks = [];
function check(name, ok, extra) { checks.push({ name, ok: !!ok, extra: extra || "" }); }

async function run() {
  await new Promise((r) => setTimeout(r, 0));
  if (!TOKEN) throw new Error("no admin token in snapshot");

  const c0 = await corpus(), f0 = await filesCardItems(), u0 = await uploadCount();

  // --- upload a DOCX with unique content + a table ---
  const ud = await req("POST", "/admin/upload", { files: [{ filename: "Quibberflux_SOW.docx", content_b64: OFF.docx_b64 }] });
  const c1 = await corpus(), f1 = await filesCardItems(), u1 = await uploadCount();
  check("docx add reports real passages", ud.passages_added >= 3, "passages_added=" + ud.passages_added);
  check("docx add reports a table", ud.tables_added >= 1, "tables_added=" + ud.tables_added);
  check("Workspace documents tile +1", c1.documents === c0.documents + 1, c0.documents + "→" + c1.documents);
  check("Workspace passages tile rises by parsed count", c1.passages === c0.passages + ud.passages_added, c0.passages + "→" + c1.passages + " (+" + ud.passages_added + ")");
  check("Workspace tables tile rises by parsed count", c1.tables === c0.tables + ud.tables_added, c0.tables + "→" + c1.tables + " (+" + ud.tables_added + ")");
  // three screens agree on the document delta
  check("Admin Files card agrees (+1)", f1 === f0 + 1, f0 + "→" + f1);
  check("Admin uploads list agrees (+1)", u1 === u0 + 1, u0 + "→" + u1);

  // retrievable + cited on the next question, no reload
  const a = await ask("what is Quibberflux");
  const cited = (a.citations || []).find((c) => String(c.document_id || "").indexOf("quibberflux") >= 0);
  check("uploaded DOCX is retrieved next question", a.kind === "answer" && !!cited, "kind=" + a.kind);
  check("DOCX citation carries a paragraph/table coord", cited && /¶\d+|Table \d+/.test(cited.coordinate_render || ""), cited && cited.coordinate_render);

  // dedupe by content hash — a re-upload of the same bytes is reported, not re-indexed
  const dup = await req("POST", "/admin/upload", { files: [{ filename: "copy.docx", content_b64: OFF.docx_b64 }] });
  const cDup = await corpus();
  check("re-upload deduped by content hash", dup.uploaded === 0 && (dup.duplicates || []).length === 1 && cDup.documents === c1.documents, "uploaded=" + dup.uploaded + " dups=" + (dup.duplicates || []).length);

  // persist across reload (fresh engine, same localStorage)
  loadEngine();
  await new Promise((r) => setTimeout(r, 0));
  const cR = await corpus();
  check("upload persists across reload", cR.documents === c1.documents && cR.passages === c1.passages, "docs=" + cR.documents + " passages=" + cR.passages);
  const aR = await ask("what is Quibberflux");
  check("still retrievable after reload", aR.kind === "answer", "kind=" + aR.kind);

  // --- upload a PPTX with per-slide passages + media ---
  const px = await req("POST", "/admin/upload", { files: [{ filename: "Governed_SDLC.pptx", content_b64: OFF.pptx_b64 }] });
  const c2 = await corpus();
  check("pptx add reports per-slide passages", px.passages_added === 5, "passages_added=" + px.passages_added);
  check("Workspace images tile rises by deck media", c2.images === cR.images + px.images_added && px.images_added >= 1, "images " + cR.images + "→" + c2.images);
  const aS = await ask("what is Snorkblat");
  const slide = (aS.citations || []).find((c) => /slide \d+/.test(c.coordinate_render || ""));
  check("slide citation present", !!slide, slide && slide.coordinate_render);

  // --- delete the DOCX → every counter reverts, together, no reload ---
  const docId = (await req("GET", "/admin/uploads")).uploads.find((u) => u.title.indexOf("Quibberflux") >= 0).document_id;
  await req("POST", "/admin/uploads/delete", { document_id: docId });
  const c3 = await corpus(), f3 = await filesCardItems(), u3 = await uploadCount();
  check("delete reverts documents tile toward baseline", c3.documents === c2.documents - 1, c2.documents + "→" + c3.documents);
  check("delete reverts tables tile", c3.tables === c0.tables, "tables=" + c3.tables + " (baseline " + c0.tables + ")");
  check("Admin Files card + uploads list still agree", f3 === u3 && f3 === u1, "files=" + f3 + " uploads=" + u3);

  const ok = checks.every((c) => c.ok);
  process.stdout.write(JSON.stringify({ ok, checks }));
}
run().catch((e) => { process.stdout.write(JSON.stringify({ ok: false, error: String(e && e.stack ? e.stack : e), checks })); process.exit(1); });
