/* Static-parity runner (T32).
 *
 * Loads the ACTUAL shipped engine.js under a minimal browser shim and drives it
 * exactly as the Workspace would — POST /login, then POST /ask per case — so the
 * parity check compares the real static answer path against the Python server,
 * not a re-implementation of it.
 *
 * Usage:  node parity_runner.js <engine.js> <snapshot.json> <cases.json>
 * Emits a JSON array of the engine's answers (one per case) on stdout.
 */
"use strict";
const fs = require("fs");

const [enginePath, snapPath, casesPath] = process.argv.slice(2);
const SNAP = JSON.parse(fs.readFileSync(snapPath, "utf8"));
const CASES = JSON.parse(fs.readFileSync(casesPath, "utf8"));

// ---- minimal browser shim -------------------------------------------------
// engine.js is an IIFE that, at load, reads window.KF_SURFACE + location, binds
// realFetch = window.fetch (which must serve snapshot.json), then overrides
// window.fetch with its own handler. Making window === globalThis keeps its
// `window.KF_BASE = …` writes and `window.fetch` reads consistent.
const store = {};
globalThis.localStorage = {
  getItem: (k) => (k in store ? store[k] : null),
  setItem: (k, v) => { store[k] = String(v); },
  removeItem: (k) => { delete store[k]; },
};
globalThis.location = {
  pathname: "/workspace/",
  origin: "http://parity.local",
  href: "http://parity.local/workspace/",
};
globalThis.KF_SURFACE = "workspace";
// realFetch: engine captures this before overriding, and uses it for the
// snapshot (and static assets). Serve the snapshot; everything else is inert.
globalThis.fetch = async function (url) {
  const u = String(url);
  if (u.indexOf("snapshot.json") >= 0) return { ok: true, json: async () => SNAP };
  return { ok: true, json: async () => ({}) };
};
globalThis.window = globalThis; // engine references window.* — alias to global

// Node provides global Response and URL (used by the engine's fetch override).

// ---- load the real engine (runs the IIFE, overrides window.fetch) ---------
(0, eval)(fs.readFileSync(enginePath, "utf8"));

async function post(path, body, token) {
  const opts = { method: "POST", body: JSON.stringify(body || {}) };
  if (token) opts.headers = { Authorization: "Bearer " + token };
  const r = await globalThis.fetch(path, opts);
  return r.json();
}

async function run() {
  await new Promise((r) => setTimeout(r, 0)); // let the snapshot `ready` resolve
  const out = [];
  for (const c of CASES) {
    const login = await post("/login", { subject: c.subject, password: c.password || "" });
    const token = login.token || (login.login && login.login.token) || "";
    const a = await post("/ask", { question: c.question, context: c.context || null }, token);
    out.push({
      id: c.id,
      kind: a.kind,
      level_name: (a.why || {}).level_name || null,
      understood_as: a.understood_as || null,
      role_view: a.role_view || null,
      citations: (a.citations || []).length,
      has_code: typeof a.answer_text === "string" && a.answer_text.indexOf("```") >= 0,
    });
  }
  process.stdout.write(JSON.stringify(out));
}

run().catch((e) => {
  process.stderr.write("parity_runner error: " + (e && e.stack ? e.stack : e) + "\n");
  process.exit(2);
});
