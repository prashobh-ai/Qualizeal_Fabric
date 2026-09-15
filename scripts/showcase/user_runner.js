/* T160 — drive the ACTUAL shipped engine.js under a Node browser shim and prove the
 * admin user-management flow: add/upsert a user with a role + designation (capturing
 * email/department/team/daily cap), the user appears in /admin/users with its effective
 * principals, and signing in as that user carries the designation so answers are
 * conditioned by it (T27). Also: edit (upsert), disable (blocks sign-in), reset budget,
 * delete, and CSV-style bulk upsert.
 *
 * Used by tests/unit/test_user_management.py.
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

const ADMIN = ((SNAP.login || {})["admin"] || {}).token || "";
function req(tok, m, p, b) {
  const o = { method: m, headers: tok ? { Authorization: "Bearer " + tok } : {} };
  if (b) {
    o.body = JSON.stringify(b);
    o.headers["Content-Type"] = "application/json";
  }
  return globalThis.fetch(p, o).then((r) => r.json());
}

async function run() {
  await new Promise((r) => setTimeout(r, 0));
  const out = { admin_token: !!ADMIN };
  const SUBJ = "analyst.jo";

  // 1) add a user: role=asker, designation=Tester, with the extra fields.
  await req(ADMIN, "POST", "/admin/users", {
    subject: SUBJ, roles: ["asker"], scopes: ["public"], designation: "Tester",
    email: "jo@acme.com", department: "QA", team: "Payments", daily_cap: 5,
  });
  let list = (await req(ADMIN, "GET", "/admin/users")).users || [];
  let row = list.filter((u) => u.subject === SUBJ)[0] || {};
  out.added = {
    present: !!row.subject, designation: row.designation, email: row.email,
    department: row.department, roles: row.roles, scopes: row.scopes, status: row.status,
  };

  // 2) sign in as that user (promoted path) → the designation is carried → answers
  //    are conditioned by it (T27). Tester conditions the "quality" persona lens.
  const login = await req(null, "POST", "/login", { subject: SUBJ, password: "whatever" });
  out.signin_designation = login.designation;
  const who = await req(login.token, "GET", "/api/auth/whoami");
  out.whoami = { subject: who.subject, roles: who.roles, designation: who.designation };
  const a = await req(login.token, "POST", "/ask", { question: "what is QMentisAI" });
  out.answer_persona = (a.role_view || {}).persona || "";
  out.answer_designation = (a.role_view || {}).designation || "";

  // 3) edit (upsert): promote to curator, change department.
  await req(ADMIN, "POST", "/admin/users", { subject: SUBJ, roles: ["curator"], designation: "Tester", department: "Quality Eng" });
  row = ((await req(ADMIN, "GET", "/admin/users")).users || []).filter((u) => u.subject === SUBJ)[0] || {};
  out.edited = { roles: row.roles, department: row.department, designation: row.designation };

  // 4) disable → sign-in is blocked; enable → allowed again.
  await req(ADMIN, "POST", "/admin/users", { subject: SUBJ, action: "disable" });
  const blocked = await req(null, "POST", "/login", { subject: SUBJ, password: "x" });
  out.disabled_login_blocked = !blocked.token;
  await req(ADMIN, "POST", "/admin/users", { subject: SUBJ, action: "enable" });
  const reok = await req(null, "POST", "/login", { subject: SUBJ, password: "x" });
  out.reenabled_login_ok = !!reok.token;

  // 5) reset budget + delete.
  await req(ADMIN, "POST", "/admin/users", { subject: SUBJ, action: "reset_budget" });
  await req(ADMIN, "POST", "/admin/users", { subject: SUBJ, action: "delete" });
  out.deleted_absent = !((await req(ADMIN, "GET", "/admin/users")).users || []).some((u) => u.subject === SUBJ);

  // 6) CSV-style bulk upsert of two users.
  for (const u of [
    { subject: "dev.sam", roles: ["asker"], scopes: ["public"], designation: "Developer" },
    { subject: "arch.lee", roles: ["curator"], scopes: ["public", "restricted"], designation: "Architect" },
  ]) {
    await req(ADMIN, "POST", "/admin/users", u);
  }
  list = (await req(ADMIN, "GET", "/admin/users")).users || [];
  out.bulk_present = ["dev.sam", "arch.lee"].every((s) => list.some((u) => u.subject === s));

  process.stdout.write(JSON.stringify(out));
}
run().catch((e) => {
  process.stdout.write(JSON.stringify({ error: String(e && e.stack ? e.stack : e) }));
  process.exit(1);
});
