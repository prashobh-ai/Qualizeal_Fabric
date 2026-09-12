"""Stage-2 Section G: the Ask / Curator / Admin consoles.

Three layers of checks:

1. markup — each ``*_HTML`` constant is a complete, self-contained page (no
   external scripts/stylesheets/CDNs), carries the brand shell, the sign-in
   bar and the element ids the JS renders into;
2. contract — each page speaks only to the endpoints its role owns (the
   Curator console has no connector / bulk / permission controls; the Admin
   console has them all) and reads every Answer field the contract lists;
3. served — the real ``http_api.Handler`` on a seeded in-memory platform
   serves the three pages at ``/``, ``/curator`` and ``/admin``, and the
   endpoints the pages call return the keys the JS reads (401/403 included).

An optional ``node --check`` syntax pass over the inline scripts runs when a
Node binary is available and is skipped otherwise.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer

from knowledge_fabric.answer.service import AnswerService
from knowledge_fabric.surfaces import http_api, ui_common
from knowledge_fabric.surfaces.admin_ui import ADMIN_HTML
from knowledge_fabric.surfaces.ask_ui import ASK_HTML
from knowledge_fabric.surfaces.curator_ui import CURATOR_HTML
from knowledge_fabric.surfaces.dashboard import DASHBOARD_HTML
from knowledge_fabric.surfaces.signin_ui import SIGNIN_HTML
from knowledge_fabric.tenants import demo
from tests.fixtures import synthetic_corpus
from tests.util import seeded

T = "test-fabric"
PAGES = {"ask": ASK_HTML, "curator": CURATOR_HTML, "admin": ADMIN_HTML}

# element ids each page must render into (contract Section G)
# L2 — the three-column Workspace: threads · conversation · right rail.
ASK_IDS = [
    "ws",
    "threads",
    "thread-list",
    "new-chat",
    "corpus-strip",
    "tile-documents",
    "tile-passages",
    "tile-entities",
    "tile-relationships",
    "tile-domains",
    "messages",
    "question",
    "ask-btn",
    "ask-status",
    "answer-lang",
    "mic-btn",
    "read-aloud",
    "galaxy",
    "galaxy-stats",
    "explain-btn",
    "answer-card",
    "usage-body",
    "usage-subject",
    "page-drawer",
    "explain-drawer",
]
CURATOR_IDS = [
    "quality-tiles",
    "risk-register",
    "gaps-list",
    "contradictions-list",
    "review-list",
    "doc-table",
    "doc-rows",
    "doc-filter",
    "doc-search",
    "history-panel",
    "versions-rows",
    "dataset-rows",
    "add-doc-form",
    "add-filename",
    "add-text",
    "add-btn",
    "authority-ranks",
]
ADMIN_IDS = [
    "connectors",
    "runs-panel",
    "runs",
    "runs-live",
    "run-due-btn",
    "bulk-upload",
    "upload-json",
    "upload-batch",
    "upload-btn",
    "bulk-delete",
    "delete-ids",
    "delete-source",
    "delete-prefix",
    "delete-btn",
    "budget",
    "budget-cap",
    "budget-btn",
    "users",
    "users-rows",
    "authority-editor",
    "audit-tail",
    "audit-rows",
    "aws-panel",
    "doctor-target",
    "doctor-btn",
    "doctor-report",
]
# L1.2 — the product top bar: nav, the user menu (#kf-who) and the gate.
# No tenant selector, no inline sign-in bar (sign-in is at /signin).
SHELL_IDS = ["kf-nav", "kf-who", "kf-gate"]

# Answer dict fields the Ask page must read (contract Section G + conflicts)
# The Ask console consumes every reader-facing Answer field. The raw ``tier``
# and ``level`` integers are deliberately NOT read here: the reader only ever
# sees the level as a word, mapped from ``why.level_name`` (L1.5 / D11).
ANSWER_FIELDS = [
    "kind",
    "answer_text",
    "citations",
    "confidence",
    "grounding_score",
    "why",
    "lang",
    "cache_hit",
    "cost_saved",
    "tokens_in",
    "tokens_out",
    "cost",
    "model_name",
    "complexity",
    "authoritative_source",
    "conflicts",
    "dataset_version",
    "reasoning",
    "trajectory_id",
    "clarify_back",
    "document_title",
    "coordinate_render",
    "snippet",
    "level_name",
    "explain",
    "reasons",
]

CURATOR_ENDPOINTS = [
    "/curator/quality",
    "/curator/documents",
    "/curator/decision",
    "/curator/upload",
    "/curator/versions",
    "/curator/gaps",
]
ADMIN_ENDPOINTS = [
    "/admin/connectors",
    "/admin/sync",
    "/admin/refresh/run-due",
    "/admin/runs",
    "/admin/upload",
    "/admin/bulk-delete",
    "/admin/audit",
    "/admin/budget",
    "/admin/users",
    "/admin/doctor",
    "/admin/authority",
]

_SCRIPT = re.compile(r"<script>(.*?)</script>", re.S)


def _has_id(html: str, id_: str) -> bool:
    return f'id="{id_}"' in html


# ==========================================================================
# 1. markup
# ==========================================================================
class TestMarkup(unittest.TestCase):
    def test_constants_are_complete_pages(self):
        for name, html in PAGES.items():
            with self.subTest(page=name):
                self.assertIsInstance(html, str)
                self.assertTrue(html.startswith("<!doctype html>"))
                self.assertIn("<title>QualiZeal Knowledge Fabric — ", html)  # L1.2 title
                self.assertTrue(html.rstrip().endswith("</html>"))
                # every opened <script> (inline or vendored src) is closed
                self.assertEqual(
                    len(re.findall(r"<script(?:\s[^>]*)?>", html)), html.count("</script>")
                )
                self.assertEqual(html.count("<script>"), 3)  # inline: directory, runtime, page
                self.assertEqual(html.count("<style>"), html.count("</style>"))
                self.assertGreater(len(html), 10_000)

    def test_zero_external_dependencies(self):
        # No EXTERNAL resources: no http(s) URLs, no @import, no external
        # scripts. Same-origin /static assets (favicon <link>, lockup <img>)
        # are allowed (L1.1/L1.2).
        for name, html in PAGES.items():
            with self.subTest(page=name):
                self.assertNotIn('src="http', html)
                self.assertNotIn('href="http', html)
                self.assertNotIn("@import", html)
                self.assertNotIn("http://", html)
                # The only https URLs allowed are GitHub deep-links (source /
                # citation anchors), assembled at click time — never a loaded
                # resource. (T92 removed the "Get full answer" issue detour.)
                for url in re.findall(r"https://[\w./-]+", html):
                    self.assertTrue(url.startswith("https://github.com/"), f"{name}: {url}")
                # any <link>/<script src> must point at /static (same origin)
                for m in re.findall(r'<(?:link|script)[^>]*?(?:href|src)="([^"]+)"', html):
                    self.assertTrue(
                        m.startswith("/static/"), f"{name} references non-static asset {m!r}"
                    )

    def test_brand_shell(self):
        for name, html in PAGES.items():
            with self.subTest(page=name):
                # white product chrome: surface white, panels the light token
                self.assertIn("--qz-surface:#FFFFFF", html)
                self.assertIn("--qz-panel:#F4F8FC", html)
                self.assertNotIn('data-theme="dark"', html)  # no dark theme (L1.1)
                self.assertIn("QualiZeal Knowledge Fabric", html)
                # top bar: lockup + role page switcher (L1.2)
                self.assertIn("qualizeal-lockup.png", html)
                self.assertIn("favicon-32.png", html)
                self.assertIn("qz-watermark", html)  # L1.3
                self.assertIn("QualiZeal. All rights reserved.", html)
                for id_ in SHELL_IDS:
                    self.assertTrue(_has_id(html, id_), f"{name} lacks #{id_}")
                for path in ("/", "/curator", "/admin"):
                    self.assertIn(f'href="{path}"', html)
                self.assertNotIn('id="kf-tenant"', html)  # no tenant selector (D5)
                self.assertIn("'/login'", html)  # sign-in flow
                self.assertIn("sessionStorage", html)
        # nav links carry data-path so the client can prefix the deploy base
        # (server default base is '' — the href stays absolute).
        self.assertIn('<a href="/" data-path="/" class="active">Workspace</a>', ASK_HTML)
        self.assertIn('<a href="/curator" data-path="/curator" class="active"', CURATOR_HTML)
        self.assertIn('<a href="/admin" data-path="/admin" class="active"', ADMIN_HTML)

    def test_page_ids(self):
        for id_ in ASK_IDS:
            self.assertTrue(_has_id(ASK_HTML, id_), f"ask lacks #{id_}")
        for id_ in CURATOR_IDS:
            self.assertTrue(_has_id(CURATOR_HTML, id_), f"curator lacks #{id_}")
        for id_ in ADMIN_IDS:
            self.assertTrue(_has_id(ADMIN_HTML, id_), f"admin lacks #{id_}")

    def test_demo_directory_matches_seed_and_is_embedded(self):
        d = ui_common.demo_directory()
        self.assertEqual([t["tenant"] for t in d["tenants"]], list(demo.DEMO_USERS))
        # The product directory carries the single `qualizeal` fabric and its
        # role users; questions now come from /api/suggestions (L0.2/L0.3),
        # so the static question list is empty.
        prod = "qualizeal"
        self.assertEqual(
            [u["subject"] for u in d["users"][prod]], [s for s, *_ in demo.DEMO_USERS[prod]]
        )
        self.assertEqual(d["questions"][prod], [])
        self.assertEqual(ui_common.demo_directory(), d)  # deterministic
        for html in PAGES.values():
            m = re.search(r"window\.KF_DIRECTORY=(\{.*?\});</script>", html, re.S)
            self.assertIsNotNone(m)
            embedded = json.loads(m.group(1))
            self.assertEqual(embedded, d)
            for subject in ("asker.public", "curator", "admin"):
                self.assertIn(subject, html)

    def test_role_gate_messages(self):
        for html in PAGES.values():
            self.assertIn("Sign in required", html)  # 401
            self.assertIn("This console needs the", html)  # 403 role message
        self.assertIn("gate(e,'curator')", CURATOR_HTML)
        self.assertIn("gate(e,'admin')", ADMIN_HTML)
        self.assertIn("gate(e,'asker')", ASK_HTML)

    def test_brand_tokens_present(self):
        """L1.1 — the QualiZeal deck-template tokens ship in every served page,
        the navy canvas is scoped to .galaxy/.health-ring only, and the page
        surface is white.
        """
        for name, html in PAGES.items():
            with self.subTest(page=name):
                for token in (
                    "--qz-blue:#0096FF",
                    "--qz-coral:#F53E5A",
                    "--qz-purple:#7048E8",
                    "--qz-green:#0CA678",
                    "--qz-surface:#FFFFFF",
                    "--qz-canvas:#0E1A45",
                ):
                    self.assertIn(token, html, f"{name} lacks {token}")
                # navy canvas scoped to .galaxy and .health-ring only
                self.assertIn(".galaxy,.health-ring", html)


# ==========================================================================
# 2. contract
# ==========================================================================
class TestContract(unittest.TestCase):
    def test_ask_reads_every_answer_field(self):
        for field in ANSWER_FIELDS:
            self.assertIn(field, ASK_HTML, f"Ask page never reads '{field}'")
        self.assertIn("'/ask'", ASK_HTML)
        # L2 card rows + the galaxy/usage endpoints the Workspace owns.
        self.assertIn("Answered by", ASK_HTML)
        self.assertIn("Moved levels", ASK_HTML)
        self.assertIn("Model", ASK_HTML)
        self.assertIn("Tokens", ASK_HTML)
        self.assertIn("Authoritative source", ASK_HTML)
        self.assertIn("condition", ASK_HTML)  # reasoning branch
        self.assertIn("/api/galaxy", ASK_HTML)
        self.assertIn("/api/usage", ASK_HTML)
        for path in CURATOR_ENDPOINTS + ADMIN_ENDPOINTS:
            self.assertNotIn(path, ASK_HTML, f"Ask page must not call {path}")

    def test_curator_console_scope(self):
        for path in CURATOR_ENDPOINTS:
            self.assertIn(path, CURATOR_HTML, f"Curator page never calls {path}")
        for path in ADMIN_ENDPOINTS:
            self.assertNotIn(path, CURATOR_HTML, f"Curator page must not call {path}")
        for decision in (
            'data-act="keep"',
            'data-act="delete"',
            "'authoritative'",
            "'not_authoritative'",
            "'rollback'",
        ):
            self.assertIn(decision, CURATOR_HTML)
        for word in ("keep", "review", "delete"):
            self.assertIn(word, CURATOR_HTML)
        self.assertIn("to_version", CURATOR_HTML)
        self.assertIn("Rollback", CURATOR_HTML)
        self.assertIn("Mark authoritative", CURATOR_HTML)
        # NO connector / bulk / permission controls on the curator console
        for forbidden in (
            'id="connectors"',
            'id="bulk-upload"',
            'id="bulk-delete"',
            'id="users"',
            'id="budget"',
            "Sync now",
            "allow-list",
            "Bulk",
            "interval_s",
        ):
            self.assertNotIn(
                forbidden, CURATOR_HTML, f"curator console must not contain {forbidden!r}"
            )

    def test_admin_console_scope(self):
        for path in ADMIN_ENDPOINTS:
            self.assertIn(path, ADMIN_HTML, f"Admin page never calls {path}")
        for path in ("/curator/decision", "/curator/upload"):
            self.assertNotIn(path, ADMIN_HTML)
        for stage in ("detect", "convert", "chunk", "extract", "graph", "embed", "health"):
            self.assertIn(f"'{stage}'", ADMIN_HTML)
        self.assertIn("2000)", ADMIN_HTML)  # 2 s polling
        self.assertIn("SLA BREACH", ADMIN_HTML)
        self.assertIn("confirm(", ADMIN_HTML)  # bulk delete confirm
        for body_key in (
            "enabled",
            "allow",
            "interval_s",
            "document_ids",
            "uri_prefix",
            "cap",
            "files",
        ):
            self.assertIn(body_key, ADMIN_HTML)

    @unittest.skipUnless(shutil.which("node"), "node not available for a JS syntax check")
    def test_inline_scripts_parse(self):
        for name, html in PAGES.items():
            scripts = _SCRIPT.findall(html)
            self.assertEqual(len(scripts), 3)
            with tempfile.NamedTemporaryFile("w", suffix=".js", delete=False) as f:
                f.write("\n".join("{\n" + s + "\n}" for s in scripts))
                path = f.name
            try:
                r = subprocess.run(
                    ["node", "--check", path], capture_output=True, text=True, timeout=30
                )
                self.assertEqual(r.returncode, 0, f"{name}: {r.stderr[:800]}")
            finally:
                os.unlink(path)


# ==========================================================================
# 3. served by the real handler on a seeded platform
# ==========================================================================
class TestServedPages(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.platform = seeded([T])
        cls._saved = (http_api._platform, http_api._svc)
        http_api._platform = cls.platform
        http_api._svc = AnswerService(cls.platform)
        cls.srv = ThreadingHTTPServer(("127.0.0.1", 0), http_api.Handler)
        cls.base = f"http://127.0.0.1:{cls.srv.server_address[1]}"
        cls.thread = threading.Thread(target=cls.srv.serve_forever, daemon=True)
        cls.thread.start()
        cls.tokens = {s: cls._login(s) for s in ("asker.public", "curator", "admin")}

    @classmethod
    def tearDownClass(cls):
        cls.srv.shutdown()
        cls.srv.server_close()
        http_api._platform, http_api._svc = cls._saved

    @classmethod
    def _call(cls, method, path, body=None, token=None):
        data = json.dumps(body).encode() if body is not None else None
        req = urllib.request.Request(cls.base + path, data=data, method=method)
        if token:
            req.add_header("Authorization", f"Bearer {token}")
        try:
            with urllib.request.urlopen(req, timeout=30) as r:
                return r.status, r.headers.get("Content-Type", ""), r.read()
        except urllib.error.HTTPError as e:
            return e.code, e.headers.get("Content-Type", ""), e.read()

    @classmethod
    def _login(cls, subject):
        code, _, raw = cls._call("POST", "/login", {"tenant": T, "subject": subject})
        assert code == 200, raw
        return json.loads(raw)["token"]

    def _json(self, method, path, body=None, token=None):
        code, _, raw = self._call(method, path, body, token)
        return code, json.loads(raw)

    def test_pages_are_served(self):
        for path, html in (
            ("/", ASK_HTML),
            ("/ask", ASK_HTML),
            ("/curator", CURATOR_HTML),
            ("/admin", ADMIN_HTML),
            ("/signin", SIGNIN_HTML),
        ):
            code, ctype, raw = self._call("GET", path)
            self.assertEqual(code, 200)
            self.assertTrue(ctype.startswith("text/html"))
            self.assertEqual(raw.decode("utf-8"), html)

    def test_signin_page_is_public(self):
        # L1.4 — /signin is reachable with no session (it is where the gate
        # sends the reader) and carries the lockup, the local IdP form and the
        # disabled SSO placeholder.
        code, ctype, raw = self._call("GET", "/signin")
        self.assertEqual(code, 200)
        body = raw.decode("utf-8")
        self.assertIn("Sign in to QualiZeal Knowledge Fabric", body)
        self.assertIn('id="signin-form"', body)
        self.assertIn("qualizeal-lockup.png", body)
        self.assertIn("Single sign-on", body)  # SSO placeholder
        self.assertIn("Showcase sign-in", body)  # demo identity picker

    def test_login_shape_used_by_sign_in_bar(self):
        code, j = self._json("POST", "/login", {"tenant": T, "subject": "curator"})
        self.assertEqual(code, 200)
        for k in ("token", "subject", "roles", "scopes", "tenant"):
            self.assertIn(k, j)
        self.assertEqual(j["roles"], ["curator"])
        code, j = self._json("POST", "/login", {"tenant": T, "subject": "nobody"})
        self.assertEqual(code, 404)
        self.assertIn("error", j)

    def test_ask_answer_card_fields(self):
        code, a = self._json(
            "POST",
            "/ask",
            {"question": "what must a release achieve before promotion?"},
            self.tokens["asker.public"],
        )
        self.assertEqual(code, 200)
        for k in (
            "kind",
            "answer_text",
            "citations",
            "confidence",
            "grounding_score",
            "tier",
            "level",
            "why",
            "lang",
            "cache_hit",
            "cost_saved",
            "tokens_in",
            "tokens_out",
            "cost",
            "model_name",
            "complexity",
            "authoritative_source",
            "dataset_version",
            "reasoning",
            "trajectory_id",
        ):
            self.assertIn(k, a, f"answer lacks '{k}'")
        for c in a["citations"]:
            for k in (
                "document_title",
                "coordinate_render",
                "snippet",
                "document_id",
                "passage_id",
            ):
                self.assertIn(k, c)
        if a["authoritative_source"]:
            for k in ("document_title", "source", "reason", "conflicts"):
                self.assertIn(k, a["authoritative_source"])
        if a["why"]:
            for k in ("level_name", "explain", "reasons"):
                self.assertIn(k, a["why"])
            # L2.3 — the Trust bars (five grounding signals) and Sources found/cited.
            self.assertIn("signals", a["why"])
            self.assertEqual(
                set(a["why"]["signals"]),
                {"retrieval", "semantic", "coverage", "agreement", "resolvable"},
            )
            self.assertIn("retrieved", a["why"])
            self.assertGreaterEqual(a["why"]["retrieved"], len(a["citations"]))
        # multistep question → reasoning timeline payload
        code, a = self._json(
            "POST",
            "/ask",
            {
                "question": "what must a release achieve before promotion and "
                "which requirement has a traceability gap?"
            },
            self.tokens["asker.public"],
        )
        self.assertEqual(code, 200)
        self.assertIsNotNone(a["reasoning"])
        self.assertIn("mode", a["reasoning"])
        for s in a["reasoning"]["steps"]:
            for k in ("id", "question", "kind", "answer_text", "grounding", "condition"):
                self.assertIn(k, s)

    def test_runtime_why_text_has_no_internal_words(self):
        # L1.5 / D11 at RUNTIME — the answer card shows ``why.explain`` verbatim,
        # so the server must never frame the answer with an internal routing word
        # (the static HTML audit cannot see server-generated strings).
        banned = ["tier", "trajectory", "pgvector", "rrf", "kf-mock", "token ledger", "routed to"]
        tok = self.tokens["asker.public"]
        for q in (
            "what must a release achieve before promotion?",
            "what is the release gate?",
            "compare the release gate and the incident policy and explain the difference",
        ):
            _, a = self._json("POST", "/ask", {"question": q}, tok)
            explain = ((a.get("why") or {}).get("explain") or "").lower()
            for w in banned:
                self.assertNotIn(w, explain, f"why.explain leaks {w!r} for {q!r}: {explain!r}")

    def test_usage_is_self_scoped(self):
        # L2.5 — /api/usage returns the CALLER's own aggregates across
        # today / 7d / 30d, plus the tenant budget bar; a signed-in asker can
        # read it (no curate role needed) and only sees their own subject.
        tok = self.tokens["asker.public"]
        self._json(
            "POST", "/ask", {"question": "what must a release achieve before promotion?"}, tok
        )
        code, u = self._json("GET", "/api/usage", token=tok)
        self.assertEqual(code, 200)
        self.assertEqual(u["subject"], "asker.public")
        for win in ("today", "7d", "30d"):
            self.assertIn(win, u["windows"])
            for k in (
                "questions",
                "answered",
                "declined",
                "tokens_in",
                "tokens_out",
                "cost",
                "cost_saved",
                "by_level",
            ):
                self.assertIn(k, u["windows"][win])
        self.assertGreaterEqual(u["windows"]["30d"]["questions"], 1)
        self.assertIn("budget", u)
        # unauthenticated is refused
        code, _ = self._json("GET", "/api/usage")
        self.assertEqual(code, 401)

    def test_galaxy_is_self_scoped_and_activates(self):
        # L2.4 — /api/galaxy lights up the graph for one answer's trace; an
        # asker may only see their OWN trace; an unknown trace is empty (not an
        # error) so the rail degrades gracefully.
        tok = self.tokens["asker.public"]
        code, a = self._json(
            "POST", "/ask", {"question": "what must a release achieve before promotion?"}, tok
        )
        tid = a["trajectory_id"]
        code, g = self._json("GET", f"/api/galaxy?trace_id={tid}", token=tok)
        self.assertEqual(code, 200)
        for k in ("nodes", "edges", "stats"):
            self.assertIn(k, g)
        self.assertIn("activated", g["stats"])
        # a foreign / unknown trace yields an empty galaxy, not a leak
        code, g2 = self._json("GET", "/api/galaxy?trace_id=does-not-exist", token=tok)
        self.assertEqual(code, 200)
        self.assertEqual(g2["nodes"], [])
        # unauthenticated is refused
        code, _ = self._json("GET", f"/api/galaxy?trace_id={tid}")
        self.assertEqual(code, 401)

    def test_unauthenticated_and_forbidden(self):
        code, j = self._json("POST", "/ask", {"question": "x"})
        self.assertEqual(code, 401)
        self.assertIn("error", j)
        code, j = self._json("GET", "/curator/quality", token=self.tokens["asker.public"])
        self.assertEqual(code, 403)
        self.assertIn("requires a higher role", j["error"])
        code, j = self._json("GET", "/admin/runs", token=self.tokens["curator"])
        self.assertEqual(code, 403)
        self.assertIn("error", j)

    def test_curator_payloads(self):
        tok = self.tokens["curator"]
        code, q = self._json("GET", "/curator/quality", token=tok)
        self.assertEqual(code, 200)
        for k in (
            "coverage",
            "freshness",
            "contradictions",
            "gaps",
            "connectedness",
            "traceability",
            "readability_avg",
            "duplicate_rate",
            "citation_coverage",
            "documents",
            "passages",
            "suggestions",
            "risk_register",
        ):
            self.assertIn(k, q)
        code, g = self._json("GET", "/curator/gaps", token=tok)
        self.assertEqual(code, 200)
        for k in ("gaps", "contradictions", "review_queue", "risk_register"):
            self.assertIn(k, g)
        code, d = self._json("GET", "/curator/documents", token=tok)
        self.assertEqual(code, 200)
        self.assertIn("documents", d)
        self.assertIn("dataset_version", d)
        self.assertIn("authority", d)
        self.assertGreater(len(d["documents"]), 0)
        doc = d["documents"][0]
        for k in (
            "document_id",
            "title",
            "source",
            "uri",
            "passages",
            "authoritative",
            "signals",
            "score",
            "suggestion",
            "reasons",
        ):
            self.assertIn(k, doc)
        self.assertIn(doc["suggestion"], ("keep", "review", "delete"))
        code, v = self._json(
            "GET", f"/curator/versions?document_id={doc['document_id']}", token=tok
        )
        self.assertEqual(code, 200)
        self.assertIn("history", v)
        self.assertIn("dataset_version", v)
        self.assertIn("dataset_versions", v)
        for h in v["history"]:
            for k in ("version", "content_hash", "created_at", "passages", "source_version"):
                self.assertIn(k, h)
        code, out = self._json(
            "POST",
            "/curator/decision",
            {"document_id": doc["document_id"], "decision": "keep", "reason": "ui test"},
            tok,
        )
        self.assertEqual(code, 200)
        self.assertTrue(out["ok"])
        self.assertIn("dataset_version", out)
        code, up = self._json(
            "POST",
            "/curator/upload",
            {
                "files": [
                    {
                        "filename": "qa/ui-added.md",
                        "text": "# UI added\n\nA curator-added note.",
                        "acl": ["public"],
                    }
                ]
            },
            tok,
        )
        self.assertEqual(code, 200)
        for k in ("uploaded", "ingested", "dataset_version"):
            self.assertIn(k, up)
        self.assertEqual(up["uploaded"], 1)

    def test_admin_payloads(self):
        tok = self.tokens["admin"]
        code, c = self._json("GET", "/admin/connectors", token=tok)
        self.assertEqual(code, 200)
        self.assertIn("connectors", c)
        sources = {x["source"] for x in c["connectors"]}
        self.assertTrue({"files", "github", "jira"} <= sources)
        for x in c["connectors"]:
            for k in ("source", "enabled", "allow", "scopes", "health", "registered"):
                self.assertIn(k, x)
        code, out = self._json(
            "POST",
            "/admin/connectors",
            {"source": "jira", "enabled": True, "allow": ["REL"], "interval_s": 600},
            tok,
        )
        self.assertEqual(code, 200)
        self.assertIn("connector", out)
        self.assertIn("health", out)
        for k in (
            "freshness_minutes",
            "last_status",
            "error_count",
            "next_run",
            "interval_s",
            "items",
            "sla_breach",
        ):
            self.assertIn(k, out["health"])
        # The product fabric carries no synthetic delta (L0.2); a real
        # connector supplies the records. Pass a fresh record (not already in
        # the fixture load) so the sync ingests a delta through all 7 stages.
        fresh_jira = synthetic_corpus.JIRA_RECORDS + [
            {
                "project": "REL",
                "key": "REL-901",
                "summary": "Fresh sync record",
                "status": "Open",
                "updated": 9999,
                "acl": ["public"],
                "description": "A new issue supplied by the connector to exercise the "
                "seven-stage ingestion pipeline on Sync now.",
            }
        ]
        code, s = self._json("POST", "/admin/sync", {"source": "jira", "records": fresh_jira}, tok)
        self.assertEqual(code, 200)
        self.assertIn("run_id", s)
        code, r = self._json("GET", "/admin/runs?limit=12", token=tok)
        self.assertEqual(code, 200)
        run = next(x for x in r["runs"] if x["id"] == s["run_id"])
        for k in (
            "id",
            "source",
            "status",
            "started_at",
            "finished_at",
            "items",
            "steps",
            "stages",
        ):
            self.assertIn(k, run)
        names = {st["name"] for st in run["stages"]}
        self.assertTrue(
            {"detect", "convert", "chunk", "extract", "graph", "embed", "health"} <= names
        )
        for st in run["steps"]:
            for k in ("name", "status", "count", "ms"):
                self.assertIn(k, st)
        code, out = self._json(
            "POST", "/admin/connectors", {"source": "github", "enabled": False}, tok
        )
        self.assertEqual(code, 200)
        code, j = self._json("POST", "/admin/sync", {"source": "github"}, tok)
        self.assertEqual(code, 409)
        self.assertIn("disabled", j["error"])
        self._json("POST", "/admin/connectors", {"source": "github", "enabled": True}, tok)
        code, up = self._json(
            "POST",
            "/admin/upload",
            {
                "files": [
                    {"filename": "qa/bulk-1.md", "text": "# Bulk one\n\nText one."},
                    {"filename": "qa/bulk-2.md", "text": "# Bulk two\n\nText two."},
                ]
            },
            tok,
        )
        self.assertEqual(code, 200)
        for k in ("uploaded", "ingested", "run_id", "dataset_version"):
            self.assertIn(k, up)
        self.assertEqual(up["uploaded"], 2)
        code, dl = self._json(
            "POST", "/admin/bulk-delete", {"uri_prefix": "upload://qa/bulk-"}, tok
        )
        self.assertEqual(code, 200)
        self.assertIn("deleted", dl)
        code, b = self._json("POST", "/admin/budget", {"cap": 7.5}, tok)
        self.assertEqual(code, 200)
        for k in ("tenant", "cap", "spent"):
            self.assertIn(k, b)
        code, u = self._json("GET", "/admin/users", token=tok)
        self.assertEqual(code, 200)
        self.assertEqual({x["subject"] for x in u["users"]}, {s for s, *_ in demo._ROLE_USERS})
        for x in u["users"]:
            for k in ("subject", "roles", "scopes", "designation"):
                self.assertIn(k, x)
        code, a = self._json("GET", "/admin/audit?limit=40", token=tok)
        self.assertEqual(code, 200)
        self.assertGreater(len(a["audit"]), 0)
        for k in ("subject", "action", "resource", "decision", "at"):
            self.assertIn(k, a["audit"][0])
        code, ranks = self._json("GET", "/admin/authority", token=tok)
        self.assertEqual(code, 200)
        self.assertIn("ranks", ranks)
        code, rd = self._json("POST", "/admin/refresh/run-due", {}, tok)
        self.assertEqual(code, 200)
        self.assertIn("ran", rd)


class TestStaticAssets(unittest.TestCase):
    """P1.1 — static asset serving under /static/*.

    The brand assets, the vendor JS/CSS, and self-hosted fonts must ship from
    ``surfaces/static/`` with a cache header, and path traversal must never
    resolve outside that directory.
    """

    @classmethod
    def setUpClass(cls):
        cls.platform = seeded([T])
        cls._saved = (http_api._platform, http_api._svc)
        http_api._platform = cls.platform
        http_api._svc = AnswerService(cls.platform)
        cls.srv = ThreadingHTTPServer(("127.0.0.1", 0), http_api.Handler)
        cls.base = f"http://127.0.0.1:{cls.srv.server_address[1]}"
        cls.thread = threading.Thread(target=cls.srv.serve_forever, daemon=True)
        cls.thread.start()

    @classmethod
    def tearDownClass(cls):
        cls.srv.shutdown()
        cls.srv.server_close()
        http_api._platform, http_api._svc = cls._saved

    def _get(self, path):
        try:
            with urllib.request.urlopen(self.base + path, timeout=10) as r:
                return r.status, r.headers, r.read()
        except urllib.error.HTTPError as e:
            return e.code, e.headers, e.read()

    def test_brand_lockup_served(self):
        code, headers, body = self._get("/static/assets/brand/logo/qualizeal-lockup.png")
        self.assertEqual(code, 200)
        self.assertEqual(headers.get("Content-Type"), "image/png")
        self.assertGreater(len(body), 512)
        self.assertIn("public", (headers.get("Cache-Control") or "").lower())

    def test_brand_mark_served(self):
        code, headers, _ = self._get("/static/assets/brand/logo/qualizeal-mark.png")
        self.assertEqual(code, 200)
        self.assertEqual(headers.get("Content-Type"), "image/png")

    def test_favicon_served(self):
        code, headers, _ = self._get("/static/assets/brand/logo/favicon-32.png")
        self.assertEqual(code, 200)
        self.assertEqual(headers.get("Content-Type"), "image/png")

    def test_old_demo_jpegs_gone(self):
        # The demo-era JPEGs with white backgrounds are removed (L0.1).
        code, _, _ = self._get("/static/brand/qualizeal-mark.jpg")
        self.assertEqual(code, 404)

    def test_missing_asset_returns_404(self):
        code, _, _ = self._get("/static/vendor/nonexistent-bundle.js")
        self.assertEqual(code, 404)

    def test_path_traversal_refused(self):
        """`/static/../etc/passwd` and its friends must all 404, not 200."""
        for path in (
            "/static/..%2Fetc%2Fpasswd",
            "/static/./../__init__.py",
            "/static/..%2F..%2Fapp.py",
        ):
            code, _, _ = self._get(path)
            self.assertEqual(code, 404, f"{path} should be 404, got {code}")


class TestCorpusTiles(unittest.TestCase):
    """P1.2 — corpus tiles: /api/corpus counts equal the store counts."""

    @classmethod
    def setUpClass(cls):
        cls.platform = seeded([T])
        cls._saved = (http_api._platform, http_api._svc)
        http_api._platform = cls.platform
        http_api._svc = AnswerService(cls.platform)
        cls.srv = ThreadingHTTPServer(("127.0.0.1", 0), http_api.Handler)
        cls.base = f"http://127.0.0.1:{cls.srv.server_address[1]}"
        cls.thread = threading.Thread(target=cls.srv.serve_forever, daemon=True)
        cls.thread.start()
        code, _, raw = cls._call("POST", "/login", {"tenant": T, "subject": "asker.public"})
        assert code == 200, raw
        cls.token = json.loads(raw)["token"]

    @classmethod
    def tearDownClass(cls):
        cls.srv.shutdown()
        cls.srv.server_close()
        http_api._platform, http_api._svc = cls._saved

    @classmethod
    def _call(cls, method, path, body=None, token=None):
        data = json.dumps(body).encode() if body is not None else None
        req = urllib.request.Request(cls.base + path, data=data, method=method)
        if token:
            req.add_header("Authorization", f"Bearer {token}")
        try:
            with urllib.request.urlopen(req, timeout=30) as r:
                return r.status, r.headers.get("Content-Type", ""), r.read()
        except urllib.error.HTTPError as e:
            return e.code, e.headers.get("Content-Type", ""), e.read()

    def test_corpus_counts_match_store(self):
        code, _, raw = self._call("GET", "/api/corpus", token=self.token)
        self.assertEqual(code, 200)
        c = json.loads(raw)
        for k in ("documents", "passages", "entities", "relationships", "domains", "tenant"):
            self.assertIn(k, c)
        self.assertEqual(c["tenant"], T)
        self.assertEqual(c["documents"], len(self.platform.documents.list(T)))
        self.assertEqual(c["passages"], self.platform.passages.count(T))
        nodes, edges = self.platform.graph_repo.counts(T)
        self.assertEqual(c["entities"], nodes)
        self.assertEqual(c["relationships"], edges)
        domains = len({d.get("source") for d in self.platform.documents.list(T) if d.get("source")})
        self.assertEqual(c["domains"], domains)

    def test_corpus_requires_auth(self):
        code, _, _ = self._call("GET", "/api/corpus")
        self.assertEqual(code, 401)

    def test_page_ships_tile_ids(self):
        for id_ in (
            "tile-documents",
            "tile-passages",
            "tile-entities",
            "tile-relationships",
            "tile-domains",
        ):
            self.assertIn(f'id="{id_}"', ASK_HTML)
        # animateNumber helper referenced in the Ask console script
        self.assertIn("animateNumber", ASK_HTML)


class TestSuggestedQuestions(unittest.TestCase):
    """P1.6 — suggested questions from the question bank, ACL-filtered.

    Under the P0.3 role mapping, `asker.public` carries only the `public`
    scope while `asker.restricted` also carries the `restricted` scope
    (an elevated asker who can see restricted documents). A question
    whose supporting document is restricted therefore appears for
    `asker.restricted` but not for `asker.public` — so `asker.public`'s
    suggestion set is a strict subset of `asker.restricted`'s. This is
    the same acceptance the P1.6 spec asks for, expressed for this
    session's role slugs (the direction is documented in
    `docs/progress/P1.6.md`).
    """

    @classmethod
    def setUpClass(cls):
        cls.platform = seeded([T])
        cls._saved = (http_api._platform, http_api._svc)
        http_api._platform = cls.platform
        http_api._svc = AnswerService(cls.platform)
        cls.srv = ThreadingHTTPServer(("127.0.0.1", 0), http_api.Handler)
        cls.base = f"http://127.0.0.1:{cls.srv.server_address[1]}"
        cls.thread = threading.Thread(target=cls.srv.serve_forever, daemon=True)
        cls.thread.start()
        cls.tokens = {s: cls._login(s) for s in ("asker.public", "asker.restricted")}

    @classmethod
    def tearDownClass(cls):
        cls.srv.shutdown()
        cls.srv.server_close()
        http_api._platform, http_api._svc = cls._saved

    @classmethod
    def _call(cls, method, path, body=None, token=None):
        data = json.dumps(body).encode() if body is not None else None
        req = urllib.request.Request(cls.base + path, data=data, method=method)
        if token:
            req.add_header("Authorization", f"Bearer {token}")
        try:
            with urllib.request.urlopen(req, timeout=30) as r:
                return r.status, r.headers.get("Content-Type", ""), r.read()
        except urllib.error.HTTPError as e:
            return e.code, e.headers.get("Content-Type", ""), e.read()

    @classmethod
    def _login(cls, subject):
        code, _, raw = cls._call("POST", "/login", {"tenant": T, "subject": subject})
        assert code == 200, raw
        return json.loads(raw)["token"]

    def _suggestions(self, token):
        code, _, raw = self._call("GET", "/api/suggestions", token=token)
        self.assertEqual(code, 200)
        return {s["question"] for s in json.loads(raw)["suggestions"]}

    def test_public_subset_of_elevated_asker(self):
        pub = self._suggestions(self.tokens["asker.public"])
        elev = self._suggestions(self.tokens["asker.restricted"])
        self.assertTrue(
            pub.issubset(elev), f"asker.public {pub - elev} not a subset of asker.restricted {elev}"
        )
        # Strict subset: the restricted-doc question is only seen by the
        # elevated asker.
        self.assertLess(pub, elev, "asker.public should see fewer questions than asker.restricted")
        # And the restricted question is in the elevated set specifically.
        self.assertIn("how fast must critical defects be triaged?", elev)
        self.assertNotIn("how fast must critical defects be triaged?", pub)

    def test_enter_submits_shift_enter_newlines(self):
        # L0.5 — Enter sends; Shift+Enter inserts a newline.
        self.assertIn("e.key==='Enter'&&!e.shiftKey", ASK_HTML)
        self.assertNotIn("(e.ctrlKey||e.metaKey)&&e.key==='Enter'", ASK_HTML)

    def test_ui_slices_to_six(self):
        # The API returns every accessible suggestion (so the ACL subset
        # relation stays provable without the cap dropping earlier common
        # items); the Ask console slices to six chips on the client side.
        self.assertIn(".slice(0,6)", ASK_HTML)


# ==========================================================================
# 4. L1.5 — the name / words audit (D11)
# ==========================================================================
import html as _htmllib  # noqa: E402

# every page a signed-in or signed-out reader can reach.
_AUDIT_PAGES = {
    "ask": ASK_HTML,
    "curator": CURATOR_HTML,
    "admin": ADMIN_HTML,
    "dashboard": DASHBOARD_HTML,
    "signin": SIGNIN_HTML,
}

# words that must never reach the reader as visible text (internal plumbing,
# storage engines, mis-spellings). Level numbers are checked separately.
_FORBIDDEN_WORDS = [
    "tenant",
    "tier",
    "trajectory",
    "kf-mock",
    "RRF",
    "pgvector",
    "token ledger",
    "console",
]
# only ``QualiZeal`` is correct — these spellings must never appear anywhere.
_BAD_SPELLINGS = ["Qualizeal", "QUALIZEAL", "Quali Zeal", "QUALI ZEAL", "qualiZeal"]
# a level shown as a number ("L2", "Level 3") anywhere the reader can see it.
_LEVEL_NUMBER = re.compile(r"\bL[0-9]\b|\blevel\s*[0-9]\b", re.I)
# the four reader words the selector level is allowed to surface as.
_LEVEL_WORDS = ["Look it up", "Quote it", "Summarise it", "Reason about it"]


def _visible_text(doc):
    """Approximate what a reader actually sees: text nodes, the visible
    attributes, and prose-like string literals the inline JS injects into the
    DOM. Deliberately excludes ``<style>``/``<script>`` machinery, element ids,
    CSS selectors and ``console.*`` calls — none of which reach the reader.
    """
    doc = re.sub(r"<!--.*?-->", " ", doc, flags=re.S)
    doc = re.sub(r"<style\b[^>]*>.*?</style>", " ", doc, flags=re.S | re.I)
    scripts = re.findall(r"<script\b[^>]*>(.*?)</script>", doc, flags=re.S | re.I)
    doc_ns = re.sub(r"<script\b[^>]*>.*?</script>", " ", doc, flags=re.S | re.I)
    attrs = re.findall(r'(?:placeholder|title|alt|aria-label)="([^"]*)"', doc)
    text = _htmllib.unescape(re.sub(r"<[^>]+>", " ", doc_ns))
    prose = []
    for s in scripts:
        for a, b in re.findall(r"'([^'\\]*)'|\"([^\"\\]*)\"", s):
            v = a or b
            # prose the JS renders reads as a sentence/label: it has a space
            # and none of the punctuation that marks a selector or code token.
            if " " in v and not re.search(r"[#<>{}=/();]", v):
                prose.append(v)
    return text + " || " + " || ".join(attrs) + " || " + " || ".join(prose)


class TestWordsAudit(unittest.TestCase):
    """L1.5 / D11 — nothing internal, mis-spelled or numeric leaks to the reader."""

    def test_no_forbidden_words_in_visible_text(self):
        for name, doc in _AUDIT_PAGES.items():
            vt = _visible_text(doc)
            for word in _FORBIDDEN_WORDS:
                with self.subTest(page=name, word=word):
                    self.assertNotIn(
                        word.lower(),
                        vt.lower(),
                        f"{name}: forbidden word {word!r} is visible to the reader",
                    )

    def test_qualizeal_spelt_correctly_everywhere(self):
        # spelling is checked against the whole document, not just visible text:
        # the product name must never be mis-cased even in a title or comment.
        for name, doc in _AUDIT_PAGES.items():
            for bad in _BAD_SPELLINGS:
                with self.subTest(page=name, spelling=bad):
                    self.assertNotIn(bad, doc, f"{name}: mis-spelled {bad!r} (must be 'QualiZeal')")
            self.assertIn("QualiZeal", doc, f"{name}: the product name is missing")

    def test_levels_are_words_never_numbers(self):
        for name, doc in _AUDIT_PAGES.items():
            vt = _visible_text(doc)
            hits = _LEVEL_NUMBER.findall(vt)
            with self.subTest(page=name):
                self.assertEqual(hits, [], f"{name}: a level number is visible: {hits}")

    def test_ask_surfaces_the_four_level_words(self):
        # the Ask console must be able to render every reader level as a word.
        for word in _LEVEL_WORDS:
            self.assertIn(word, ASK_HTML, f"Ask console can never show level {word!r}")


if __name__ == "__main__":
    unittest.main()
