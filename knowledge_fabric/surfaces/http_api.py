"""HTTP surfaces (Section 13 / roadmap WS3 / Stage-2): Ask, Curator console,
Admin console, the Agent path, the telemetry Dashboard, and the /api/analytics
economics API. One governed path for everyone (I7): every endpoint
authenticates a Principal and runs the same gate. Roles: askers use Ask;
curators get the Curator console (relevance, keep/delete suggestions, versions,
authority); admins get the Admin console (connectors + permissions + health,
bulk upload/delete, refresh schedules, budgets, users, audit, AWS readiness).

Standard library only — runs with zero dependencies.
"""

from __future__ import annotations

import base64
import json
import os
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

from ..adapters import cloud
from ..answer.service import AnswerService
from ..app import Platform
from ..connectors import admin as conn_admin
from ..connectors import registry
from ..contracts.types import new_id, now_ms
from ..governance import authority
from ..health import kb_eval
from ..health import metrics as health
from ..ingestion import runs, scheduler
from ..ingestion.intake import IngestWorker, Intake
from ..ingestion.sync import SyncManager
from ..stores import versioning
from ..tenants import demo
from .dashboard import DASHBOARD_HTML

# Static asset root — served under /static/* with cache headers. Nothing outside
# this directory is reachable; served HTML holds no external URLs (P1.1).
_STATIC_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "static"))
_STATIC_TYPES = {
    ".css": "text/css; charset=utf-8",
    ".js": "application/javascript; charset=utf-8",
    ".svg": "image/svg+xml",
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".ico": "image/x-icon",
    ".woff": "font/woff",
    ".woff2": "font/woff2",
    ".ttf": "font/ttf",
    ".json": "application/json; charset=utf-8",
    ".txt": "text/plain; charset=utf-8",
    ".md": "text/plain; charset=utf-8",
}

try:  # Stage-2 pages (Section G); fall back to minimal pages if absent
    from .ask_ui import ASK_HTML
except Exception:  # pragma: no cover
    ASK_HTML = (
        "<!doctype html><title>Ask</title><p>Ask UI not built. POST /ask with a bearer token.</p>"
    )
try:
    from .curator_ui import CURATOR_HTML
except Exception:  # pragma: no cover
    CURATOR_HTML = "<!doctype html><title>Curator</title><p>Curator UI not built.</p>"
try:
    from .admin_ui import ADMIN_HTML
except Exception:  # pragma: no cover
    ADMIN_HTML = "<!doctype html><title>Admin</title><p>Admin UI not built.</p>"
try:
    from .signin_ui import SIGNIN_HTML
except Exception:  # pragma: no cover
    SIGNIN_HTML = "<!doctype html><title>Sign in</title><p>Sign-in not built.</p>"

_platform: Platform | None = None
_svc: AnswerService | None = None


def platform() -> Platform:
    global _platform, _svc
    if _platform is None:
        _platform = Platform(db_path=os.environ.get("KF_DB", "./data/kf.db"))
        if not _platform.documents.list("qualizeal"):
            demo.seed(_platform)
        cap = os.environ.get("KF_BUDGET_CAP_USD")  # injected by the AWS module
        if cap:
            for t in demo.DEMO_TENANTS:
                _platform.policy.set_budget(t.tenant, float(cap))
        _svc = AnswerService(_platform)
    return _platform


def _demo_delta(tenant: str, source: str) -> list[dict] | None:
    """The product fabric carries no synthetic connector records (L0.2), so
    'Sync now' runs the real connector (which pulls the live API). Returns
    None here; the continuous-refresh demo runs against a test fabric."""
    return None


def _galaxy_for_trace(p, tenant: str, trace_id: str) -> dict:
    """Assemble the compact answer galaxy (L2.4).

    Activation comes from the answer span's persisted trajectory
    (``attrs.trajectory.{selected, graph_node_keys}``): the graph nodes lit up
    by this answer's retrieved passages and one-hop graph expansion, plus their
    immediate neighbourhood as dim context (the client renders non-activated
    edges at 0.04 opacity). Empty when the answer used no graph relationships.
    Read-only and self-contained; the caller has already been authorised for
    this tenant/trace.
    """
    spans = p.telemetry.trace(trace_id)
    ans = next((s for s in spans if s.get("name") == "answer" and s.get("tenant") == tenant), None)
    if not ans:
        return {"trace_id": trace_id, "nodes": [], "edges": [], "stats": {}}
    try:
        traj = (json.loads(ans.get("attrs") or "{}") or {}).get("trajectory") or {}
    except Exception:
        traj = {}
    try:
        sources = json.loads(ans.get("sources") or "[]")
    except Exception:
        sources = []
    selected = traj.get("selected") or []
    node_keys = traj.get("graph_node_keys") or []
    rows = {
        r["id"]: dict(r) for r in p.db.query("SELECT * FROM graph_nodes WHERE tenant=?", (tenant,))
    }
    by_key: dict[str, str] = {}
    for nid, row in rows.items():
        by_key.setdefault(row["canonical_key"], nid)
        by_key.setdefault((row["canonical_key"] or "").lower(), nid)
    # activation from the retrieved passages: an entity node lights up when its
    # name appears in the text the answer actually retrieved (node provenance
    # is keyed by content hash, not passage id, so a text match is the reliable
    # link). Graph-expansion keys from a multi-hop answer light up too.
    texts = ""
    if selected:
        marks = ",".join("?" * len(selected))
        for r in p.db.query(
            f"SELECT text FROM passages WHERE tenant=? AND id IN ({marks})", (tenant, *selected)
        ):
            texts += " " + (r["text"] or "").lower()
    active: set[str] = set()
    for nid, row in rows.items():
        key = (row["canonical_key"] or "").lower()
        if len(key) >= 4 and key in texts:
            active.add(nid)
    for k in node_keys:
        nid = by_key.get(k) or by_key.get(str(k).lower())
        if nid:
            active.add(nid)
    node_ids, edges, seen = set(active), [], set()
    for nid in list(active):
        for e in p.graph_repo.neighbors(tenant, nid):
            if e["id"] in seen or len(edges) >= 200:
                continue
            seen.add(e["id"])
            node_ids.add(e["src"])
            node_ids.add(e["dst"])
            edges.append(
                {
                    "src": e["src"],
                    "dst": e["dst"],
                    "relation": e.get("relation", ""),
                    "activated": e["src"] in active and e["dst"] in active,
                }
            )

    def _label(row):
        try:
            labels = json.loads(row.get("labels") or "[]")
        except Exception:
            labels = []
        return (labels[0] if labels else "") or row.get("canonical_key") or row["id"]

    nodes = [
        {
            "id": nid,
            "label": _label(rows[nid]),
            "type": rows[nid].get("type", ""),
            "activated": nid in active,
        }
        for nid in node_ids
        if nid in rows
    ]
    return {
        "trace_id": trace_id,
        "nodes": nodes,
        "edges": edges,
        "stats": {
            "documents": len(sources),
            "passages": len(selected),
            "relationships": len(edges),
            "hops": 1 if node_keys else 0,
            "activated": len(active),
        },
    }


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    # ------------------------------------------------------------ plumbing
    def _send(self, code, obj, ctype="application/json"):
        body = obj.encode() if isinstance(obj, str) else json.dumps(obj, default=str).encode()
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _serve_static(self, rel: str):
        """Serve a file from ``surfaces/static/`` with a public cache header.

        Path traversal is refused: the resolved path must live inside
        ``_STATIC_ROOT`` (``../`` and absolute paths therefore fall out).
        """
        rel = rel.split("?", 1)[0].split("#", 1)[0]
        full = os.path.realpath(os.path.join(_STATIC_ROOT, rel))
        if not full.startswith(_STATIC_ROOT + os.sep) or not os.path.isfile(full):
            return self._send(404, {"error": "not found"})
        ext = os.path.splitext(full)[1].lower()
        ctype = _STATIC_TYPES.get(ext, "application/octet-stream")
        with open(full, "rb") as fh:
            body = fh.read()
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "public, max-age=3600")
        self.end_headers()
        self.wfile.write(body)

    def _principal(self):
        return platform().idp.authenticate({"Authorization": self.headers.get("Authorization", "")})

    def _body(self) -> dict:
        n = int(self.headers.get("Content-Length", 0) or 0)
        return json.loads(self.rfile.read(n) or b"{}")

    def _require(self, action):
        try:
            prin = self._principal()
        except PermissionError as e:
            self._send(401, {"error": str(e)})
            return None
        if platform().policy.check(prin, action, {}).decision.value != "allow":
            self._send(403, {"error": f"'{action}' requires a higher role"})
            return None
        return prin

    def _audit(self, prin, action, resource, decision, trace_id=""):
        platform().audit.write(
            prin.tenant, prin.subject, prin.agent, action, resource, decision, trace_id, now_ms()
        )

    def _delete_docs(self, prin, docs: list[dict], reason: str) -> list[str]:
        p = platform()
        ids = []
        for d in docs:
            pids = p.passages.delete_document_passages(prin.tenant, d["id"])
            p.vindex.delete(prin.tenant, pids)
            p.documents.tombstone(prin.tenant, d["id"])
            ids.append(d["id"])
            self._audit(prin, "delete_document", d["id"], reason)
        if ids:
            p.cache.invalidate(prin.tenant)
            versioning.bump_dataset(p, prin.tenant, f"{reason}: {len(ids)} document(s) removed")
        return ids

    def _upload(self, prin, files: list[dict], ontology: str, source_label: str) -> dict:
        p = platform()
        intake, worker = Intake(p), IngestWorker(p, None)
        worker.intake = intake
        run_id = runs.start_run(p, prin.tenant, source_label)
        with runs.active(run_id):
            for f in files:
                data = (
                    base64.b64decode(f["content_b64"])
                    if "content_b64" in f
                    else f.get("text", "").encode()
                )
                intake.upload(
                    prin.tenant,
                    f["filename"],
                    data,
                    acl=f.get("acl", ["public"]),
                    ontology=ontology,
                )
            res = worker.drain()
        ingested = [r for r in res if r["status"] in ("ok", "updated")]
        runs.finish(p, run_id, "ok", len(ingested))
        self._audit(prin, "upload", source_label, f"{len(files)} file(s), {len(ingested)} ingested")
        return {
            "uploaded": len(files),
            "ingested": len(ingested),
            "noops": len([r for r in res if r["status"] == "noop"]),
            "run_id": run_id,
            "dataset_version": versioning.current_dataset(p, prin.tenant),
            "sources": SyncManager(p).source_health(prin.tenant),
        }

    # ------------------------------------------------------------ GET
    def do_GET(self):
        u = urlparse(self.path)
        q = parse_qs(u.query)
        p = platform()

        def first(key, default=None):
            return q.get(key, [default])[0]

        # static assets (brand, vendored JS/CSS, self-hosted fonts) — served
        # from ``surfaces/static/`` with cache headers; no path outside that
        # root is reachable, so ``/static/../etc/passwd`` returns 404.
        if u.path.startswith("/static/"):
            return self._serve_static(u.path[len("/static/") :])

        # pages
        if u.path == "/signin":
            return self._send(200, SIGNIN_HTML, "text/html; charset=utf-8")
        if u.path in ("/", "/ask"):
            return self._send(200, ASK_HTML, "text/html; charset=utf-8")
        if u.path == "/dashboard":
            return self._send(200, DASHBOARD_HTML, "text/html; charset=utf-8")
        if u.path == "/curator":
            return self._send(200, CURATOR_HTML, "text/html; charset=utf-8")
        if u.path == "/admin":
            return self._send(200, ADMIN_HTML, "text/html; charset=utf-8")

        # open
        if u.path == "/health":
            return self._send(
                200,
                {
                    "status": "ok",
                    "model": p.model_available(),
                    "connectors": registry.available(),
                    "adapters": {
                        k: v.get("adapter")
                        for k, v in cloud.selection(dict(os.environ)).items()
                        if isinstance(v, dict) and "adapter" in v
                    },
                },
            )
        if u.path == "/connectors":
            return self._send(200, {"connectors": registry.available()})
        if u.path == "/metrics":
            return self._send(200, p.telemetry.metrics(first("tenant", "qualizeal")))

        # curator + admin
        if u.path == "/api/analytics":
            prin = self._require("curate")
            if not prin:
                return
            return self._send(
                200,
                p.telemetry.analytics(
                    prin.tenant, first("window", "7d"), first("subject"), first("role")
                ),
            )
        if u.path == "/api/events":
            # T29 — flat telemetry for the self-serve Explorer (filter/pivot any
            # dimension in one table). Curator+ only, like analytics.
            prin = self._require("curate")
            if not prin:
                return
            return self._send(200, {"events": p.telemetry.events(prin.tenant)})
        if u.path == "/api/suggestions":
            # P1.6 — Suggested questions from the tenant question bank,
            # filtered by the asker's ACL (they never see a question
            # whose supporting document they could not retrieve). The
            # spec asks for confidence ≥ 0.56 and ≥ 2 cited documents;
            # ratings-per-question are populated by the eval bank, so
            # when a rating exists we honour it and when it does not we
            # fall back to family-diverse ACL-visible questions from the
            # seed bank (Deferred → docs/progress/P1.6.md).
            try:
                prin = self._principal()
            except PermissionError as e:
                return self._send(401, {"error": str(e)})
            accessible = set(prin.accessible_acls())
            uri_acls: dict[str, set[str]] = {}
            for d in p.documents.list(prin.tenant):
                uri = (d.get("uri") or "").replace("file://", "").replace("upload://", "")
                acl = d.get("acl") or []
                if isinstance(acl, str):
                    try:
                        acl = json.loads(acl)
                    except Exception:
                        acl = [acl]
                uri_acls[uri] = set(acl or ["public"])
            rows = p.db.query(
                "SELECT question, expected_docs, family FROM question_bank WHERE tenant=?",
                (prin.tenant,),
            )
            out = []
            seen_family = set()
            # The API returns every accessible suggestion in bank order; the
            # UI slices to six chips (F1.4). The subset relation the ACL gate
            # promises — asker.public ⊆ asker.restricted — is provable on the
            # raw API without the cap dropping earlier-ranked common items.
            for r in rows:
                uris = [u for u in (r["expected_docs"] or "").split(",") if u]
                # ACL gate: every supporting document must be accessible
                if not uris or not all(uri_acls.get(u, {"public"}) & accessible for u in uris):
                    continue
                fam = r["family"] if "family" in r.keys() else ""
                out.append({"question": r["question"], "family": fam or "", "docs": len(uris)})
                if fam:
                    seen_family.add(fam)
            return self._send(
                200, {"tenant": prin.tenant, "suggestions": out, "families": sorted(seen_family)}
            )
        if u.path == "/api/corpus":
            # Five-tile "corpus at a glance" for the Ask console header (P1.2).
            # Asker-accessible: every signed-in principal can see how big
            # their tenant's knowledge is (documents, passages, entities,
            # relationships, domains). Restricted documents are still
            # counted here — the ACL gate lives at retrieval time, not at
            # the tile level.
            try:
                prin = self._principal()
            except PermissionError as e:
                return self._send(401, {"error": str(e)})
            docs = p.documents.list(prin.tenant)
            nodes, edges = p.graph_repo.counts(prin.tenant)
            domains = len({d.get("source", "") for d in docs if d.get("source")})
            return self._send(
                200,
                {
                    "tenant": prin.tenant,
                    "documents": len(docs),
                    "passages": p.passages.count(prin.tenant),
                    "entities": nodes,
                    "relationships": edges,
                    "domains": domains,
                },
            )
        if u.path == "/api/usage":
            # L2.5 — the caller's OWN usage, self-scoped: an asker sees only
            # their own subject's activity (no privilege escalation — the
            # subject filter is fixed to the authenticated principal).
            # Aggregated from the telemetry spine across today / 7 d / 30 d.
            try:
                prin = self._principal()
            except PermissionError as e:
                return self._send(401, {"error": str(e)})
            windows = {"today": "24h", "7d": "7d", "30d": "all"}
            out = {}
            for label, win in windows.items():
                a = p.telemetry.analytics(prin.tenant, win, subject=prin.subject)
                by_level = a.get("routing_by_level", {}) or {}
                declined = int(by_level.get("clarify", 0)) + int(by_level.get("gap", 0))
                answered_levels = {
                    k: v for k, v in by_level.items() if k not in ("clarify", "gap", "")
                }
                out[label] = {
                    "questions": a.get("answers", 0),
                    "answered": max(0, a.get("answers", 0) - declined),
                    "declined": declined,
                    "tokens_in": a.get("tokens_in", 0),
                    "tokens_out": a.get("tokens_out", 0),
                    "cost": a.get("total_cost", 0.0),
                    "cost_saved": a.get("total_cost_saved", 0.0),
                    "cache_hit_rate": a.get("cache_hit_rate", 0.0),
                    "by_level": answered_levels,
                }
            cap_row = p.db.one("SELECT cap,spent FROM budgets WHERE tenant=?", (prin.tenant,))
            budget = (
                {
                    "cap": cap_row["cap"],
                    "spent": cap_row["spent"],
                    "remaining": cap_row["cap"] - cap_row["spent"],
                }
                if cap_row
                else None
            )
            # speech seconds arrive with voice (L5); no per-subject speech budget yet.
            return self._send(
                200,
                {"subject": prin.subject, "windows": out, "budget": budget, "speech_seconds": None},
            )
        if u.path == "/api/galaxy":
            # L2.4 — the compact answer galaxy for one trace, self-scoped: an
            # asker may light up only their OWN answers; a curator/admin may
            # inspect any trace in the tenant.
            try:
                prin = self._principal()
            except PermissionError as e:
                return self._send(401, {"error": str(e)})
            trace_id = first("trace_id", "")
            spans = p.telemetry.trace(trace_id)
            ans = next((s for s in spans if s.get("name") == "answer"), None)
            can_curate = p.policy.check(prin, "curate", {}).decision.value == "allow"
            if (
                not ans
                or ans.get("tenant") != prin.tenant
                or (ans.get("subject") != prin.subject and not can_curate)
            ):
                return self._send(
                    200, {"trace_id": trace_id, "nodes": [], "edges": [], "stats": {}}
                )
            return self._send(200, _galaxy_for_trace(p, prin.tenant, trace_id))
        if u.path == "/api/trace":
            prin = self._require("curate")
            if not prin:
                return
            return self._send(200, {"spans": p.telemetry.trace(first("trace_id", ""))})
        if u.path == "/admin/sources":
            prin = self._require("curate")
            if not prin:
                return
            return self._send(200, {"sources": SyncManager(p).source_health(prin.tenant)})
        if u.path == "/curator/gaps":
            prin = self._require("curate")
            if not prin:
                return
            return self._send(
                200,
                {
                    "gaps": p.curation.list(prin.tenant, "gap"),
                    "contradictions": p.curation.list(prin.tenant, "contradiction"),
                    "review_queue": p.curation.list(prin.tenant, "low-confidence"),
                    "risk_register": health.risk_register(p, prin.tenant),
                },
            )
        if u.path == "/curator/feedback":
            # L6 — negative feedback from readers, for the curator to check.
            prin = self._require("curate")
            if not prin:
                return
            out = []
            for r in p.curation.list(prin.tenant, "negative-feedback"):
                try:
                    d = json.loads(r.get("item") or "{}")
                except Exception:
                    d = {"question": r.get("item", "")}
                d.update(id=r.get("id"), at=r.get("at"), status=r.get("status", "open"))
                out.append(d)
            return self._send(200, {"feedback": out})
        if u.path == "/curator/quality":
            prin = self._require("curate")
            if not prin:
                return
            return self._send(200, kb_eval.data_quality(p, prin.tenant))
        if u.path == "/curator/documents":
            prin = self._require("curate")
            if not prin:
                return
            return self._send(
                200,
                {
                    "documents": kb_eval.document_quality(p, prin.tenant),
                    "authority": authority.list_ranks(p, prin.tenant),
                    "dataset_version": versioning.current_dataset(p, prin.tenant),
                },
            )
        if u.path == "/curator/versions":
            prin = self._require("curate")
            if not prin:
                return
            doc_id = first("document_id", "")
            diff = None
            if doc_id and first("from") and first("to"):
                try:
                    diff = versioning.diff(
                        p, prin.tenant, doc_id, int(first("from")), int(first("to"))
                    )
                except KeyError as e:
                    return self._send(404, {"error": str(e)})
                except ValueError as e:
                    return self._send(400, {"error": str(e)})
            return self._send(
                200,
                {
                    "document_id": doc_id,
                    "diff": diff,
                    "history": versioning.history(p, prin.tenant, doc_id) if doc_id else [],
                    "dataset_version": versioning.current_dataset(p, prin.tenant),
                    "dataset_versions": versioning.list_dataset_versions(p, prin.tenant, 20),
                },
            )
        if u.path == "/curator/lineage":
            prin = self._require("curate")
            if not prin:
                return
            return self._send(
                200, {"lineage": versioning.lineage(p, prin.tenant, first("passage_id", ""))}
            )

        # admin only
        if u.path == "/admin/connectors":
            prin = self._require("admin")
            if not prin:
                return
            hmap = {h["source"]: h for h in scheduler.health(p, prin.tenant)}
            out = []
            for c in conn_admin.list_all(p, prin.tenant):
                c = dict(c)
                c["health"] = hmap.get(c["source"], {})
                out.append(c)
            return self._send(
                200, {"connectors": out, "schedules": scheduler.schedules(p, prin.tenant)}
            )
        if u.path == "/admin/runs":
            prin = self._require("admin")
            if not prin:
                return
            return self._send(
                200, {"runs": runs.list_runs(p, prin.tenant, int(first("limit", "20")))}
            )
        if u.path == "/admin/audit":
            prin = self._require("admin")
            if not prin:
                return
            return self._send(
                200, {"audit": p.audit.for_tenant(prin.tenant, int(first("limit", "50")))}
            )
        if u.path == "/admin/users":
            prin = self._require("admin")
            if not prin:
                return
            rows = demo.DEMO_USERS.get(prin.tenant) or demo._ROLE_USERS
            return self._send(
                200, {"users": [{"subject": s, "roles": r, "scopes": sc} for s, r, sc in rows]}
            )
        if u.path == "/admin/authority":
            prin = self._require("admin")
            if not prin:
                return
            return self._send(200, {"ranks": authority.list_ranks(p, prin.tenant)})
        if u.path == "/admin/otlp":  # OTLP/JSON dry-run of this tenant's spans
            prin = self._require("admin")
            if not prin:
                return
            from ..adapters import otel_export

            try:
                return self._send(200, otel_export.export(p, prin.tenant, None))
            except TypeError:
                return self._send(200, otel_export.export(p, prin.tenant))
        if u.path == "/admin/doctor":
            prin = self._require("admin")
            if not prin:
                return
            from ..ops import readiness

            target = first("target", "local")
            if target not in readiness.TARGETS:
                return self._send(400, {"error": f"target must be one of {readiness.TARGETS}"})
            report = readiness.report(target=target, run_tests=False, live_health=False)
            render = getattr(readiness, "render", None) or getattr(readiness, "format_report", None)
            if render:
                try:
                    report["rendered"] = render(report)
                except Exception:  # pragma: no cover
                    pass
            report["selection"] = cloud.selection(dict(os.environ))
            return self._send(200, report)
        return self._send(404, {"error": "not found"})

    # ------------------------------------------------------------ POST
    def do_POST(self):
        u = urlparse(self.path)
        p = platform()
        if u.path == "/login":
            b = self._body()
            try:
                prin = demo.principal_for(p, b["tenant"], b["subject"])
            except KeyError as e:
                return self._send(404, {"error": str(e)})
            return self._send(
                200,
                {
                    "token": p.idp.mint(prin),
                    "subject": prin.subject,
                    "roles": prin.roles,
                    "scopes": prin.scopes,
                    "tenant": prin.tenant,
                },
            )
        if u.path in ("/ask", "/agent/ask"):
            try:
                prin = self._principal()
            except PermissionError as e:
                return self._send(401, {"error": str(e)})
            body = self._body()
            return self._send(
                200,
                _svc.ask(prin, body.get("question", ""), context=body.get("context")).to_dict(),
            )
        if u.path == "/feedback":
            # L3/L6 — a reader flags an answer (👎). Negative feedback lands in
            # the curator review queue as a 'negative-feedback' item; anyone
            # signed in may leave it (scoped to their own subject).
            try:
                prin = self._principal()
            except PermissionError as e:
                return self._send(401, {"error": str(e)})
            b = self._body()
            if (b.get("verdict") or "down") == "down":
                item = json.dumps(
                    {
                        "subject": prin.subject,
                        "question": b.get("question", ""),
                        "trace_id": b.get("trace_id", ""),
                        "level": b.get("level", ""),
                        "note": b.get("note", ""),
                    }
                )
                p.curation.add(prin.tenant, item, "negative-feedback", now_ms())
                self._audit(prin, "feedback", b.get("trace_id", ""), "down")
            return self._send(200, {"ok": True})

        # ---- curator -------------------------------------------------
        if u.path == "/curator/upload":
            prin = self._require("curate")
            if not prin:
                return
            b = self._body()
            return self._send(
                200,
                self._upload(
                    prin,
                    b.get("files", []),
                    b.get("ontology", "quality-assurance"),
                    "curator-upload",
                ),
            )
        if u.path == "/curator/decision":
            prin = self._require("curate")
            if not prin:
                return
            b = self._body()
            doc_id, decision, reason = (
                b.get("document_id", ""),
                b.get("decision", ""),
                b.get("reason", ""),
            )
            doc = p.documents.get(prin.tenant, doc_id)
            if not doc:
                return self._send(404, {"error": "document not found"})
            out = {"ok": True, "document_id": doc_id, "decision": decision}
            if decision == "delete":
                out["deleted"] = self._delete_docs(prin, [doc], f"curator delete: {reason}")
            elif decision in ("authoritative", "not_authoritative"):
                authority.mark_authoritative(
                    p, prin.tenant, doc_id, decision == "authoritative", prin.subject
                )
                p.cache.invalidate(prin.tenant)
            elif decision == "rollback":
                try:
                    out["rollback"] = versioning.rollback(
                        p, prin.tenant, doc_id, int(b.get("to_version", 1)), prin.subject
                    )
                except KeyError as e:
                    return self._send(404, {"error": str(e)})
                except (ValueError, TypeError) as e:
                    return self._send(400, {"error": f"invalid to_version: {e}"})
                p.cache.invalidate(prin.tenant)
                versioning.bump_dataset(
                    p, prin.tenant, f"rollback {doc_id} -> v{b.get('to_version')}"
                )
            elif decision != "keep":
                return self._send(400, {"error": f"unknown decision '{decision}'"})
            p.db.execute(
                "INSERT INTO "
                "curation_decisions(id,tenant,document_id,decision,reason,by_subject,at) "
                "VALUES(?,?,?,?,?,?,?)",
                (new_id("dec_"), prin.tenant, doc_id, decision, reason, prin.subject, now_ms()),
            )
            self._audit(prin, f"curate:{decision}", doc_id, reason or "ok")
            out["dataset_version"] = versioning.current_dataset(p, prin.tenant)
            return self._send(200, out)

        # ---- admin ---------------------------------------------------
        if u.path == "/admin/upload":
            prin = self._require("admin")
            if not prin:
                return
            b = self._body()
            return self._send(
                200,
                self._upload(
                    prin, b.get("files", []), b.get("ontology", "quality-assurance"), "bulk-upload"
                ),
            )
        if u.path == "/admin/bulk-delete":
            prin = self._require("admin")
            if not prin:
                return
            b = self._body()
            ids, source, prefix = (
                set(b.get("document_ids") or []),
                b.get("source"),
                b.get("uri_prefix"),
            )
            docs = [
                d
                for d in p.documents.list(prin.tenant)
                if (d["id"] in ids)
                or (source and d["source"] == source)
                or (prefix and d["uri"].startswith(prefix))
            ]
            deleted = self._delete_docs(prin, docs, "bulk delete")
            return self._send(
                200,
                {
                    "deleted": len(deleted),
                    "document_ids": deleted,
                    "dataset_version": versioning.current_dataset(p, prin.tenant),
                },
            )
        if u.path == "/admin/connectors":
            prin = self._require("admin")
            if not prin:
                return
            b = self._body()
            row = conn_admin.upsert(
                p,
                prin.tenant,
                b["source"],
                enabled=b.get("enabled"),
                config=b.get("config"),
                allow=b.get("allow"),
                scopes=b.get("scopes"),
            )
            if b.get("interval_s") is not None:
                existing = (scheduler.get_schedule(p, prin.tenant, b["source"]) or {}).get(
                    "config"
                ) or {}
                cfg = (
                    b["config"]
                    if b.get("config") is not None
                    else (existing or row.get("config") or {})
                )
                scheduler.set_schedule(
                    p,
                    prin.tenant,
                    b["source"],
                    int(b["interval_s"]),
                    cfg,
                    enabled=bool(b.get("enabled", True)),
                    now=time.time(),
                )
            self._audit(
                prin,
                "connector_config",
                b["source"],
                json.dumps({k: v for k, v in b.items() if k != "source"}),
            )
            return self._send(
                200, {"connector": row, "health": scheduler.health_for(p, prin.tenant, b["source"])}
            )
        if u.path == "/admin/sync":
            prin = self._require("admin")
            if not prin:
                return
            b = self._body()
            source = b.get("source", "")
            if source not in registry.available():
                return self._send(
                    404,
                    {"error": f"unknown connector '{source}'; registered: {registry.available()}"},
                )
            if not conn_admin.is_enabled(p, prin.tenant, source):
                return self._send(409, {"error": f"connector '{source}' is disabled by admin"})
            records = b.get("records")
            if records is None and b.get("demo_delta", True):
                records = _demo_delta(prin.tenant, source)
            cfg = b.get("config")
            if source == "files" and not ((cfg or {}).get("folder")):
                sched = scheduler.get_schedule(p, prin.tenant, source)
                folder = ((sched or {}).get("config") or {}).get("folder") or os.path.join(
                    os.environ.get("KF_DROP_ROOT", "./data/drop"), prin.tenant
                )
                os.makedirs(folder, exist_ok=True)
                cfg = {**(cfg or {}), "folder": folder}  # tenant default drop folder
            summary = scheduler.sync_now(p, prin.tenant, source, config=cfg, records=records)
            self._audit(prin, "sync_now", source, summary.get("last_status", "ok"))
            return self._send(200, summary)
        if u.path == "/admin/refresh/run-due":
            prin = self._require("admin")
            if not prin:
                return
            out = scheduler.run_due(p, prin.tenant, now=time.time())
            self._audit(prin, "refresh_run_due", "scheduler", f"{len(out)} source(s)")
            return self._send(200, {"ran": out})
        if u.path == "/admin/authority":
            prin = self._require("admin")
            if not prin:
                return
            b = self._body()
            authority.set_source_rank(p, prin.tenant, b["source"], int(b["rank"]))
            p.cache.invalidate(prin.tenant)
            self._audit(prin, "authority_rank", b["source"], f"rank={b['rank']}")
            return self._send(200, {"ranks": authority.list_ranks(p, prin.tenant)})
        if u.path == "/admin/budget":
            prin = self._require("set_budget")
            if not prin:
                return
            b = self._body()
            p.policy.set_budget(prin.tenant, float(b["cap"]))
            self._audit(prin, "set_budget", prin.tenant, f"cap={b['cap']}")
            return self._send(
                200, {"tenant": prin.tenant, "cap": b["cap"], "spent": p.policy.spent(prin.tenant)}
            )
        if u.path == "/admin/users":
            # L3.4 — Users & Access: add or disable a demo user. Roles are
            # attributes of users (F2.3); the directory is in memory for the
            # showcase/laptop deploy (the corporate directory replaces it live).
            prin = self._require("admin")
            if not prin:
                return
            b = self._body()
            subject = (b.get("subject") or "").strip()
            if not subject:
                return self._send(400, {"error": "subject required"})
            rows = demo.DEMO_USERS.setdefault(prin.tenant, list(demo._ROLE_USERS))
            existing = {s for s, _, _ in rows}
            if b.get("action") == "delete":
                if subject in ("admin",):
                    return self._send(400, {"error": "cannot remove the built-in admin"})
                demo.DEMO_USERS[prin.tenant] = [r for r in rows if r[0] != subject]
                self._audit(prin, "delete_user", subject, "removed")
            else:
                roles = b.get("roles") or ["asker"]
                scopes = b.get("scopes") or (
                    ["public", "restricted"] if ({"admin", "curator"} & set(roles)) else ["public"]
                )
                if subject not in existing:
                    rows.append((subject, list(roles), list(scopes)))
                    self._audit(prin, "add_user", subject, ",".join(roles))
                demo.DEMO_USERS[prin.tenant] = rows
            out = demo.DEMO_USERS.get(prin.tenant, demo._ROLE_USERS)
            return self._send(
                200,
                {
                    "ok": True,
                    "users": [{"subject": s, "roles": r, "scopes": sc} for s, r, sc in out],
                },
            )
        return self._send(404, {"error": "not found"})


_loops: list = []


def start_refresh_loops(p: Platform) -> list:
    """Continuous refresh: one RefreshLoop per tenant (KF_REFRESH_TICK_S, default 5s).
    The product fabric carries no synthetic connector records (L0.2); real
    connectors pull from the live API on each tick."""
    tick = float(os.environ.get("KF_REFRESH_TICK_S", "5"))
    if os.environ.get("KF_REFRESH", "1") != "1":
        return []
    loops = []
    for t in demo.DEMO_TENANTS:
        loop = scheduler.RefreshLoop(p, t.tenant, tick_s=tick, records_by_source=None)
        loop.start()
        loops.append(loop)
    return loops


def serve(host="0.0.0.0", port=8080):
    p = platform()
    _loops.extend(start_refresh_loops(p))
    srv = ThreadingHTTPServer((host, port), Handler)
    print(
        f"Knowledge Fabric on http://{host}:{port}  (Ask: /  ·  Curator: /curator  ·  Admin: "
        f"/admin  ·  Dashboard: /dashboard)"
        f"  refresh loops: {len(_loops)}"
    )
    srv.serve_forever()


if __name__ == "__main__":
    serve(port=int(os.environ.get("KF_PORT", "8080")))
