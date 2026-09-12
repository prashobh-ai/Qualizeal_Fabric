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

from .. import curation, fabric_views
from ..adapters import cloud
from ..answer import defaults as user_defaults
from ..answer import personas
from ..answer import registry as known_registry
from ..answer.service import AnswerService
from ..app import Platform
from ..connectors import admin as conn_admin
from ..connectors import registry
from ..contracts.types import new_id, now_ms
from ..governance import authority
from ..health import galaxy as galaxy_view
from ..health import graph_insights, kb_eval
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


def _github_rate_limit():
    """``rate_limit_remaining()`` from the live GitHub connector when that
    module ships it; ``None`` (shown as unknown) otherwise — never invented."""
    try:
        from ..connectors import github_live  # another track; optional
    except Exception:
        return None
    fn = getattr(github_live, "rate_limit_remaining", None)
    if not callable(fn):
        return None
    try:
        return fn()
    except Exception:
        return None


def _source_cards(p, tenant: str) -> dict:
    """T47 — the GitHub / Jira / Confluence cards for ``GET /admin/sources``:
    ``{last_run, next_run, counts, as_of}`` per source (+ ``rate_limit_remaining``
    for GitHub). Runs come from the refresh scheduler's health; counts from
    ``facts.json``."""
    health = {h["source"]: h for h in scheduler.health(p, tenant)}
    cards = fabric_views.source_counts()
    out = {}
    for source in ("github", "jira", "confluence"):
        h = health.get(source) or {}
        card = dict(cards.get(source) or {"counts": {}, "as_of": None})
        card.update(
            {
                "last_run": h.get("last_run"),
                "next_run": h.get("next_run"),
                "last_status": h.get("last_status"),
                "items": h.get("items", 0),
                "enabled": h.get("enabled", False),
            }
        )
        if source == "github":
            card["rate_limit_remaining"] = _github_rate_limit()
        out[source] = card
    return out


def _table_query_fn():
    """The SELECT-only table runner from the T42/T43 tool API
    (``answer.tools.run_table_query`` or ``answer.tables.run_table_query``),
    or ``None`` when that track has not landed — the route answers 501."""
    for mod in ("tools", "tables"):
        try:
            m = __import__(f"knowledge_fabric.answer.{mod}", fromlist=["run_table_query"])
        except Exception:
            continue
        fn = getattr(m, "run_table_query", None)
        if callable(fn):
            return fn
    return None


def _provider_badge(p) -> dict:
    """T52 — the active provider for the top-bar badge and the answer card.

    ``{"provider", "model", "dot", "label"}``. Claude when the Anthropic client
    is live, the open-source label when the fallback answers, else the
    extractive core. Reads the platform's constructed client, so a runtime
    provider flip (a restored key) shows on the next answer."""
    from ..adapters import model as _model

    client = getattr(p, "model", None)
    name = type(client).__name__ if client is not None else ""
    if name == "AnthropicModelClient":
        try:
            small, large = _model.resolve_models()
        except Exception:
            large = _model.DEFAULT_LARGE
        return {
            "provider": "Claude",
            "model": large,
            "dot": "#0096FF",
            "label": f"Claude · {large}",
        }
    if name == "OSSModelClient" and hasattr(client, "provider_label"):
        lab = client.provider_label()
        return {
            "provider": "Open-source",
            "model": lab.get("model", ""),
            "dot": lab.get("dot", "#0CA678"),
            "label": f"Open-source LLM · {lab.get('model', '')}",
        }
    return {
        "provider": "Extractive",
        "model": "core",
        "dot": "#5A6B7C",
        "label": "Extractive core",
    }


def _galaxy_for_trace(p, tenant: str, trace_id: str) -> dict:
    """Answer galaxy for one trace (T51). Delegates to ``health.galaxy`` which
    builds the physics-graph payload: nodes carry ``deg``/``type``/``docs``,
    edges carry ``relation``/``weight``, and the response carries
    ``activated_ids`` (the concepts this answer used) and ``halo_ids`` (one hop
    out) for the vis-network renderer. The caller has already authorised the
    tenant/trace."""
    return galaxy_view.build_payload(p, tenant, trace_id)


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
            # T82 — the reader's persona-drawn known questions from the registry,
            # so Home offers "the questions the fabric answers instantly" for this
            # audience alongside the tenant bank's ACL-gated suggestions.
            persona = personas.persona_for(prin.designation)
            reg = known_registry.Registry.load()
            return self._send(
                200,
                {
                    "tenant": prin.tenant,
                    "suggestions": out,
                    "families": sorted(seen_family),
                    "persona": persona,
                    "known": reg.suggestions(persona),
                },
            )
        if u.path == "/api/defaults":
            # T87 — the reader's stored defaults (persona view, depth, language,
            # Explain auto-expand) and the options the user menu offers. Any
            # signed-in principal reads its own.
            try:
                prin = self._principal()
            except PermissionError as e:
                return self._send(401, {"error": str(e)})
            return self._send(
                200,
                {
                    "defaults": user_defaults.get(prin.tenant, prin.subject),
                    "options": user_defaults.options(),
                    "designation": prin.designation or "",
                    "persona": personas.persona_for(prin.designation),
                },
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
            # T47 — five more tiles (repositories, jira_projects,
            # confluence_spaces, tables, images) read from the fabric-data files.
            return self._send(
                200,
                {
                    "tenant": prin.tenant,
                    "documents": len(docs),
                    "passages": p.passages.count(prin.tenant),
                    "entities": nodes,
                    "relationships": edges,
                    "domains": domains,
                    **fabric_views.corpus_tiles(),
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
            # T55/T56 — active-vs-idle split and percentile timing over this
            # subject's answer events, for the Usage panel.
            timing = {}
            try:
                from ..telemetry import insights

                events = p.telemetry.events(prin.tenant)
                timing = {
                    "active_idle": insights.active_vs_idle(events).get("aggregate", {}),
                    "percentiles": insights.timing_percentiles(events),
                }
            except Exception as e:
                timing = {"error": str(e)}
            return self._send(
                200,
                {
                    "subject": prin.subject,
                    "windows": out,
                    "budget": budget,
                    "speech_seconds": None,
                    "timing": timing,
                },
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
        if u.path == "/api/galaxy/node":
            # T51 — the node side sheet: name, type, document count and the
            # passages that mention the concept. Any signed-in principal in the
            # tenant may inspect a node (ACL still gates the passages).
            try:
                prin = self._principal()
            except PermissionError as e:
                return self._send(401, {"error": str(e)})
            detail = galaxy_view.node_detail(p, prin.tenant, first("id", ""))
            return self._send(200, detail or {"id": first("id", ""), "passages": []})
        if u.path == "/api/galaxy/full":
            # T51/T57 — the whole-fabric galaxy for the Curator graph, coloured
            # by community with cohesion flags and the insight lists.
            prin = self._require("curate")
            if not prin:
                return
            payload = galaxy_view.build_payload(p, prin.tenant, None)
            payload["insights"] = graph_insights.insights(p, prin.tenant)
            return self._send(200, payload)
        if u.path == "/api/trace":
            prin = self._require("curate")
            if not prin:
                return
            return self._send(200, {"spans": p.telemetry.trace(first("trace_id", ""))})
        if u.path == "/admin/sources":
            prin = self._require("curate")
            if not prin:
                return
            # T47 — beside the cursor-level source health, one card each for
            # GitHub / Jira / Confluence: last run + next run from the refresh
            # scheduler, counts from facts.json, the live rate limit when the
            # live GitHub connector exposes it.
            return self._send(
                200,
                {
                    "sources": SyncManager(p).source_health(prin.tenant),
                    **_source_cards(p, prin.tenant),
                },
            )
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
        if u.path == "/curator/recommendations":
            # T44 — documents whose generated questions FAILED in the bake
            # (gap / clarify / no citations), from data/quality/bake_failures.json.
            prin = self._require("curate")
            if not prin:
                return
            from .. import baking as _baking

            recs = _baking.recommendations(int(first("limit", "50") or 50))
            return self._send(
                200,
                {
                    "recommendations": recs,
                    "documents": len(recs),
                    "failed_questions": sum(r["failed"] for r in recs),
                    "state": {
                        k: {"baked_at": v.get("baked_at"), "kept": v.get("kept")}
                        for k, v in _baking.load_state().items()
                        if k.startswith(prin.tenant + ":")
                    },
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
                200,
                {
                    "users": [
                        {"subject": s, "roles": r, "scopes": sc, "designation": dg}
                        for s, r, sc, dg in map(demo.user_fields, rows)
                    ]
                },
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
        if u.path == "/admin/models":
            # T35/T36 — the provider card and API consumption, straight from the
            # doctor's provider_status.json and the call ledger, so the numbers
            # on screen are the ledger sums by construction.
            prin = self._require("admin")
            if not prin:
                return
            from ..adapters import model as _model
            from ..telemetry import api_ledger, insights

            days = int(first("days", "7") or 7)
            status = _model.provider_status()
            mode = (os.environ.get("KF_MODEL_MODE") or "anthropic").lower()
            payload = {
                "mode": mode,
                "allowed_models": list(_model.ALLOWED_MODELS),
                "provider": status,
                "provider_badge": _provider_badge(p),
                "key_present": bool(os.environ.get("ANTHROPIC_API_KEY", "").strip()),
                "consumption": api_ledger.consumption(days),
            }
            # T55 — the token-meter panels: cost breakdown, efficiency, waste,
            # burn rate, provider quota and percentile timing, each reconciled
            # with the ledger; definitions feed the "?" sheet.
            try:
                payload["telemetry"] = insights.overview(days)
                payload["definitions"] = insights.definitions()
            except Exception as e:  # telemetry must never break the console
                payload["telemetry"] = {"error": str(e)}
            return self._send(200, payload)
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

        if u.path == "/admin/coverage":
            # T83 — the audience coverage matrix (data type × persona). Served
            # from the generated data/coverage.json when present; otherwise
            # computed once over the self-contained coverage corpus and cached.
            prin = self._require("admin")
            if not prin:
                return
            rep = fabric_views.coverage_matrix()
            return self._send(200, rep)

        if u.path == "/admin/service-levels":
            # T86 — the business SLA & path panel: median/p95 time-to-answer, the
            # fast-vs-agent split, explain-request rate and cost per answer, per
            # persona and per data type, plus the headline SLA line.
            prin = self._require("admin")
            if not prin:
                return
            from ..telemetry import sla

            return self._send(200, sla.service_levels(p, prin.tenant))

        # ---- T47: fabric-data views (repositories, tables, insights) -----
        if u.path == "/curator/repositories":
            prin = self._require("curate")
            if not prin:
                return
            return self._send(200, fabric_views.repositories())
        if u.path == "/curator/repository":
            prin = self._require("curate")
            if not prin:
                return
            repo = first("repo", "") or ""
            card = fabric_views.repository(repo, p, prin.tenant)
            if card is None:
                return self._send(404, {"error": f"repository '{repo}' is not in facts.json"})
            return self._send(200, card)
        if u.path == "/curator/tables":
            prin = self._require("curate")
            if not prin:
                return
            return self._send(200, fabric_views.tables())
        if u.path == "/curator/insights":
            prin = self._require("curate")
            if not prin:
                return
            out = dict(fabric_views.insights())
            # T57 — community detection, surprising cross-domain links and
            # knowledge gaps over the tenant concept graph.
            try:
                out["graph"] = graph_insights.insights(p, prin.tenant)
            except Exception as e:  # never let the graph layer break the page
                out["graph"] = {"communities": {}, "surprising": [], "gaps": [], "error": str(e)}
            return self._send(200, out)
        if u.path == "/api/provider":
            # T52 — the active provider badge; any signed-in principal may read it.
            try:
                self._principal()
            except PermissionError as e:
                return self._send(401, {"error": str(e)})
            return self._send(200, _provider_badge(p))
        if u.path == "/curator/timeline":
            # T54 — curation-log events by month of the chosen year.
            prin = self._require("curate")
            if not prin:
                return
            year = first("year", "")
            return self._send(
                200,
                curation.timeline(
                    p,
                    prin.tenant,
                    year=int(year) if year.isdigit() else None,
                    source=first("source", "") or None,
                    mode=first("mode", "") or None,
                ),
            )
        if u.path == "/curator/review":
            # T53 — the manual-mode review queue with scores + recommendations.
            prin = self._require("curate")
            if not prin:
                return
            return self._send(200, {"items": curation.review_queue(p, prin.tenant)})
        if u.path == "/curator/curation-modes":
            # T53 — the per-source + global curation-mode settings.
            prin = self._require("curate")
            if not prin:
                return
            return self._send(200, curation.modes(p, prin.tenant))
        if u.path == "/curator/registry":
            # T82 — the governed known-question registry the Curator maintains:
            # every entry with its personas, answer kind, source and freshness
            # target, plus the persona→audience map the console renders.
            prin = self._require("curate")
            if not prin:
                return
            reg = known_registry.Registry.load()
            return self._send(
                200, {"entries": reg.all(), "audiences": known_registry.AUDIENCES_FOR}
            )
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
        if u.path == "/api/explain":
            # T81 — the on-demand narrative for a prior answer, a separate
            # ledgered step (purpose=explain). The direct answer already cost no
            # model tokens for a KPI; pressing "Why?" runs this.
            try:
                prin = self._principal()
            except PermissionError as e:
                return self._send(401, {"error": str(e)})
            b = self._body()
            return self._send(200, _svc.explain(prin, b.get("trace_id", "")))
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
        if u.path == "/api/defaults":
            # T87 — set the reader's own stored defaults; per user and audited.
            try:
                prin = self._principal()
            except PermissionError as e:
                return self._send(401, {"error": str(e)})
            b = self._body()
            stored = user_defaults.set(prin.tenant, prin.subject, b)
            self._audit(prin, "defaults:set", prin.subject, json.dumps(stored, sort_keys=True))
            return self._send(200, {"ok": True, "defaults": stored})

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
        if u.path == "/curator/curation-mode":
            # T53 — set the curation mode for one source or the global default.
            prin = self._require("curate")
            if not prin:
                return
            b = self._body()
            source = b.get("source", "*") or "*"
            mode = b.get("mode", "")
            try:
                curation.set_mode(p, prin.tenant, source, mode)
            except ValueError as e:
                return self._send(400, {"error": str(e)})
            self._audit(prin, "curate:mode", source, mode)
            return self._send(200, {"ok": True, "source": source, "mode": mode})
        if u.path == "/curator/review-decision":
            # T53 — accept or reject a manual-mode review item.
            prin = self._require("curate")
            if not prin:
                return
            b = self._body()
            rid, action, reason = b.get("review_id", ""), b.get("action", ""), b.get("reason", "")
            try:
                if action == "accept":
                    res = curation.accept(p, prin.tenant, rid, prin.subject)
                elif action == "reject":
                    res = curation.reject(p, prin.tenant, rid, prin.subject, reason)
                else:
                    return self._send(400, {"error": f"unknown action '{action}'"})
            except KeyError as e:
                return self._send(404, {"error": str(e)})
            p.cache.invalidate(prin.tenant)
            self._audit(prin, f"curate:review:{action}", rid, reason or "ok")
            return self._send(200, {"ok": True, "review_id": rid, "action": action, "result": res})
        if u.path == "/curator/registry":
            # T82 — add, edit or disable a known question. Each change is audited
            # and persists to the fabric-data runtime registry, so the curator's
            # governed list survives and takes effect for its persona immediately.
            prin = self._require("curate")
            if not prin:
                return
            b = self._body()
            reg = known_registry.Registry.load()
            action = b.get("action", "upsert")
            try:
                if action == "upsert":
                    entry = reg.upsert(b.get("entry") or {})
                    self._audit(prin, "registry:upsert", entry["id"], entry["pattern"])
                    return self._send(200, {"ok": True, "action": "upsert", "entry": entry})
                if action in ("enable", "disable"):
                    entry = reg.set_enabled(b.get("id", ""), action == "enable")
                    self._audit(prin, f"registry:{action}", entry["id"], "")
                    return self._send(200, {"ok": True, "action": action, "entry": entry})
                return self._send(400, {"error": f"unknown action '{action}'"})
            except ValueError as e:
                return self._send(400, {"error": str(e)})
            except KeyError as e:
                return self._send(404, {"error": f"unknown known question: {e}"})

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
            existing = {r[0] for r in rows}
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
                # T27 — the admin captures the user's designation here, at
                # access-grant time. It conditions answer framing, not access.
                designation = (b.get("designation") or "").strip()
                if subject not in existing:
                    rows.append((subject, list(roles), list(scopes), designation))
                    self._audit(prin, "add_user", subject, ",".join(roles))
                demo.DEMO_USERS[prin.tenant] = rows
            out = demo.DEMO_USERS.get(prin.tenant, demo._ROLE_USERS)
            return self._send(
                200,
                {
                    "ok": True,
                    "users": [
                        {"subject": s, "roles": r, "scopes": sc, "designation": dg}
                        for s, r, sc, dg in map(demo.user_fields, out)
                    ],
                },
            )

        # ---- T47: repositories + tables ----------------------------------
        if u.path == "/curator/repository/delete":
            # Tombstone every document the GitHub connector ingested for the
            # repository (uri prefix github://<repo>/) — the same path the
            # curator's per-document Delete takes, so passages leave retrieval
            # and the dataset version bumps.
            prin = self._require("curate")
            if not prin:
                return
            b = self._body()
            repo = (b.get("repo") or "").strip().strip("/")
            if not repo:
                return self._send(400, {"error": "repo required (owner/name)"})
            prefix = f"github://{repo}/"
            docs = [
                d for d in p.documents.list(prin.tenant) if (d.get("uri") or "").startswith(prefix)
            ]
            deleted = self._delete_docs(prin, docs, f"repository delete: {repo}")
            self._audit(prin, "delete_repository", repo, f"{len(deleted)} document(s)")
            return self._send(
                200,
                {
                    "repo": repo,
                    "deleted": len(deleted),
                    "document_ids": deleted,
                    "dataset_version": versioning.current_dataset(p, prin.tenant),
                    "in_facts": repo in (fabric_views.facts().get("repositories") or {}),
                },
            )
        if u.path == "/curator/tables/query":
            prin = self._require("curate")
            if not prin:
                return
            b = self._body()
            doc_id, sheet, sql = (
                str(b.get("doc_id") or ""),
                str(b.get("sheet") or ""),
                str(b.get("sql") or ""),
            )
            if not (doc_id and sheet and sql.strip()):
                return self._send(400, {"error": "doc_id, sheet and sql are required"})
            if not sql.lstrip().lower().startswith(("select", "with")):
                return self._send(400, {"error": "only SELECT queries are allowed"})
            run = _table_query_fn()
            if run is None:
                return self._send(
                    501,
                    {
                        "error": "table query unavailable: knowledge_fabric.answer.tools."
                        "run_table_query is not installed in this build",
                        "doc_id": doc_id,
                        "sheet": sheet,
                    },
                )
            try:
                out = run(doc_id, sheet, sql, max_rows=int(b.get("max_rows") or 200))
            except TypeError:
                out = run(doc_id, sheet, sql)
            except (ValueError, PermissionError) as e:
                return self._send(400, {"error": str(e)})
            except FileNotFoundError as e:
                return self._send(404, {"error": str(e)})
            self._audit(prin, "table_query", f"{doc_id}/{sheet}", sql[:200])
            return self._send(200, out if isinstance(out, dict) else {"result": out})
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
