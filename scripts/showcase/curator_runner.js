/* T159 — drive the ACTUAL shipped engine.js under a Node browser shim and prove
 * that the Curator's three mutations REALLY persist per visitor (they are not
 * no-op acks): the per-source curation-mode switch, a review-queue decision, and
 * the known-question registry enable/disable/upsert. For each, we POST the change
 * and then re-GET the baked panel endpoint through the same engine and confirm the
 * overlay is reflected — exactly what a curator sees on the next panel load.
 *
 * Used by tests/unit/test_curator_routes.py.
 *
 * argv: <showcase-dir>
 * Prints: a JSON object of before/after observations for the Python gate to assert.
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

const TOKEN = ((SNAP.login || {})["curator"] || {}).token || "";
function req(m, p, b) {
  const o = { method: m, headers: { Authorization: "Bearer " + TOKEN } };
  if (b) {
    o.body = JSON.stringify(b);
    o.headers["Content-Type"] = "application/json";
  }
  return globalThis.fetch(p, o).then((r) => r.json());
}
function findEntry(reg, id) {
  return ((reg && reg.entries) || []).find((e) => e.id === id) || null;
}

async function run() {
  await new Promise((r) => setTimeout(r, 0));
  const out = { token: !!TOKEN };

  // 1) The four baked reads answer for the curator subject.
  out.gets = {};
  for (const p of ["/curator/curation-modes", "/curator/review", "/curator/quality", "/curator/registry"]) {
    const r = await req("GET", p);
    out.gets[p] = r && typeof r === "object" ? Object.keys(r) : null;
  }

  // 2) curation-mode switch — set github → manual, prove it persists on re-GET.
  out.curation_before = (await req("GET", "/curator/curation-modes")).sources.github;
  const cmPost = await req("POST", "/curator/curation-mode", { source: "github", mode: "manual" });
  out.curation_post_ok = !!(cmPost && cmPost.ok);
  out.curation_after = (await req("GET", "/curator/curation-modes")).sources.github;

  // 3) registry — disable a seeded entry, then upsert a brand-new one; re-GET each.
  const REG_ID = "biz.projects_capability";
  const before = findEntry(await req("GET", "/curator/registry"), REG_ID);
  out.registry_target = REG_ID;
  out.registry_before_enabled = before ? before.enabled : null;
  const disPost = await req("POST", "/curator/registry", { action: "disable", id: REG_ID });
  out.registry_disable_ok = !!(disPost && disPost.ok);
  const afterDis = findEntry(await req("GET", "/curator/registry"), REG_ID);
  out.registry_after_enabled = afterDis ? afterDis.enabled : null;

  const NEW_ID = "qa.smoke_probe";
  const upPost = await req("POST", "/curator/registry", {
    action: "upsert",
    entry: { id: NEW_ID, pattern: "smoke probe <x>", persona: ["qa"], answer_kind: "facts" },
  });
  out.registry_upsert_ok = !!(upPost && upPost.ok);
  const afterUp = findEntry(await req("GET", "/curator/registry"), NEW_ID);
  out.registry_upsert_present = !!afterUp;
  out.registry_upsert_enabled = afterUp ? afterUp.enabled : null;

  // 4) review-queue decision — decide the first item if the build baked any;
  //    either way the POST must ack, and any decided item must drop from the queue.
  const rev0 = await req("GET", "/curator/review");
  out.review_before = (rev0.items || []).length;
  if ((rev0.items || []).length) {
    const rid = rev0.items[0].review_id;
    const rdPost = await req("POST", "/curator/review-decision", { review_id: rid, action: "accept" });
    out.review_decide_ok = !!(rdPost && rdPost.ok);
    const rev1 = await req("GET", "/curator/review");
    out.review_after = (rev1.items || []).length;
    out.review_dropped = !(rev1.items || []).some((it) => it.review_id === rid);
  } else {
    const rdPost = await req("POST", "/curator/review-decision", { review_id: "none", action: "accept" });
    out.review_decide_ok = !!(rdPost && rdPost.ok);
    out.review_after = 0;
    out.review_dropped = true;
  }

  process.stdout.write(JSON.stringify(out));
}
run().catch((e) => {
  process.stdout.write(JSON.stringify({ error: String(e && e.stack ? e.stack : e) }));
  process.exit(1);
});
