"""T47 — the fabric-data surfaces.

Every new route answers the documented shape from fixture files in the exact
``fabric-data`` layout, gates by role, the extended ``/admin/sources`` and
``/api/corpus`` keep their old keys, the repository delete tombstones the
ingested documents, and the static showcase build carries the new keys in
``snapshot.json`` so the Pages build renders the panels.
"""

from __future__ import annotations

import json
import os
import shutil
import tempfile
import threading
import unittest
from http.server import ThreadingHTTPServer
from urllib.error import HTTPError
from urllib.parse import quote
from urllib.request import Request, urlopen

os.environ.setdefault("KF_MODEL_MODE", "off")

from knowledge_fabric import fabric_views  # noqa: E402
from knowledge_fabric.answer.service import AnswerService  # noqa: E402
from knowledge_fabric.ingestion.intake import IngestWorker, Intake  # noqa: E402
from knowledge_fabric.surfaces import http_api  # noqa: E402
from knowledge_fabric.surfaces.admin_ui import ADMIN_HTML  # noqa: E402
from knowledge_fabric.surfaces.ask_ui import ASK_HTML  # noqa: E402
from knowledge_fabric.surfaces.curator_ui import CURATOR_HTML  # noqa: E402
from tests.fixtures import fabric_data_fixture as fx  # noqa: E402
from tests.util import T, seeded  # noqa: E402

REPO_ROWS_KEYS = {
    "repo",
    "primary_language",
    "languages",
    "commits",
    "prs_merged",
    "contributors_count",
    "deployments_count",
    "capabilities",
    "enterprise_score",
    "pushed_at",
    "has_card",
    "has_architecture",
}
CARD_KEYS = {
    "facts",
    "languages_bar",
    "capabilities",
    "dependencies",
    "architecture_md",
    "card_md",
    "contributors",
    "recent_prs",
    "recent_commits",
}


class _Served(unittest.TestCase):
    """Serve the real handler on a seeded platform + a fixture fabric-data."""

    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.mkdtemp(prefix="kf-t47-")
        fx.write_fabric(cls.tmp)
        cls.prev_env = fx.point_env(cls.tmp)
        cls.p = seeded([T])
        # two GitHub passages for repo A: one pull request, one commit
        intake, worker = Intake(cls.p), IngestWorker(cls.p, None)
        worker.intake = intake
        for uri, title, body in (
            (
                f"github://{fx.REPO_A}/pulls/57",
                "PR #57 merged: harden the ledger",
                "Pull request #57 (merged) hardens the API ledger against a missing day file.",
            ),
            (
                f"github://{fx.REPO_A}/commits/abc123",
                "commit abc123",
                "Commit abc123: fabric_data layout helper shared by the tracks.",
            ),
        ):
            intake.submit(
                intake.canonical(
                    T, "github", uri, title, body.encode(), mime="text/markdown", acl=["public"]
                )
            )
        worker.drain()
        http_api._platform = cls.p
        http_api._svc = AnswerService(cls.p)
        cls.srv = ThreadingHTTPServer(("127.0.0.1", 0), http_api.Handler)
        cls.port = cls.srv.server_address[1]
        threading.Thread(target=cls.srv.serve_forever, daemon=True).start()
        cls.tok = {u: cls._login(u) for u in ("admin", "curator", "asker.public")}

    @classmethod
    def tearDownClass(cls):
        cls.srv.shutdown()
        http_api._platform = None
        http_api._svc = None
        fx.restore_env(cls.prev_env)
        shutil.rmtree(cls.tmp, ignore_errors=True)

    @classmethod
    def _login(cls, subject):
        r = urlopen(
            Request(
                f"http://127.0.0.1:{cls.port}/login",
                data=json.dumps({"tenant": T, "subject": subject}).encode(),
                method="POST",
            )
        )
        return json.load(r)["token"]

    def _call(self, method, path, user=None, body=None):
        headers = {"Authorization": f"Bearer {self.tok[user]}"} if user else {}
        req = Request(
            f"http://127.0.0.1:{self.port}{path}",
            method=method,
            data=json.dumps(body).encode() if body is not None else None,
            headers=headers,
        )
        try:
            with urlopen(req) as r:
                return r.status, json.load(r)
        except HTTPError as e:
            return e.code, json.loads(e.read() or b"{}")


class TestRepositories(_Served):
    def test_repositories_shape_and_numbers(self):
        code, rows = self._call("GET", "/curator/repositories", "curator")
        self.assertEqual(code, 200)
        self.assertEqual([r["repo"] for r in rows], [fx.REPO_A, fx.REPO_B])
        for r in rows:
            self.assertTrue(REPO_ROWS_KEYS <= set(r), sorted(REPO_ROWS_KEYS - set(r)))
        a = rows[0]
        self.assertEqual(a["commits"], 412)
        self.assertEqual(a["prs_merged"], 57)
        self.assertEqual(a["contributors_count"], 2)
        self.assertEqual(a["deployments_count"], 12)
        self.assertEqual(a["capabilities"], ["caching", "enterprise_readiness", "rag"])
        self.assertAlmostEqual(a["enterprise_score"], 0.75)
        self.assertTrue(a["has_card"] and a["has_architecture"])
        self.assertFalse(rows[1]["has_card"])
        self.assertIsNone(rows[1]["enterprise_score"])

    def test_repository_card(self):
        code, card = self._call(
            "GET", "/curator/repository?repo=" + quote(fx.REPO_A, safe=""), "curator"
        )
        self.assertEqual(code, 200)
        self.assertTrue(CARD_KEYS <= set(card), sorted(CARD_KEYS - set(card)))
        self.assertEqual(card["facts"]["commits"], 412)
        self.assertEqual(card["languages_bar"][0], {"name": "Python", "share": 0.8})
        self.assertEqual(
            {c["capability"] for c in card["capabilities"]},
            {"rag", "caching", "enterprise_readiness"},
        )
        self.assertEqual(
            card["capabilities"][0]["evidence"][0]["path"], "knowledge_fabric/answer/service.py"
        )
        self.assertEqual({d["licence"] for d in card["dependencies"]}, {"MIT", "BSD-3-Clause"})
        self.assertIn("# Architecture", card["architecture_md"])
        self.assertIn("fabric-core", card["card_md"])
        self.assertEqual(card["contributors"][0]["login"], "prashobh")
        # recent PRs / commits come from the ingested github:// passages
        self.assertEqual(len(card["recent_prs"]), 1)
        self.assertEqual(len(card["recent_commits"]), 1)
        self.assertIn("harden", card["recent_prs"][0]["snippet"])
        self.assertNotIn("note", card)

    def test_repository_card_without_activity_has_note_and_unknown_is_404(self):
        code, card = self._call(
            "GET", "/curator/repository?repo=" + quote(fx.REPO_B, safe=""), "curator"
        )
        self.assertEqual(code, 200)
        self.assertEqual(card["recent_prs"], [])
        self.assertEqual(card["recent_commits"], [])
        self.assertIn("note", card)
        self.assertEqual(card["architecture_md"], "")
        code, _ = self._call("GET", "/curator/repository?repo=nobody/nothing", "curator")
        self.assertEqual(code, 404)

    def test_repository_delete_tombstones_documents(self):
        before = [
            d for d in self.p.documents.list(T) if d["uri"].startswith(f"github://{fx.REPO_A}/")
        ]
        self.assertEqual(len(before), 2)
        code, out = self._call("POST", "/curator/repository/delete", "curator", {"repo": fx.REPO_A})
        self.assertEqual(code, 200)
        self.assertEqual(out["deleted"], 2)
        self.assertTrue(out["in_facts"])
        after = [
            d for d in self.p.documents.list(T) if d["uri"].startswith(f"github://{fx.REPO_A}/")
        ]
        self.assertEqual(after, [])
        # facts.json is untouched: the repository still lists (until the next analysis run)
        code, rows = self._call("GET", "/curator/repositories", "curator")
        self.assertIn(fx.REPO_A, [r["repo"] for r in rows])
        code, _ = self._call("POST", "/curator/repository/delete", "curator", {})
        self.assertEqual(code, 400)


class TestTablesInsights(_Served):
    def test_tables_shape(self):
        code, rows = self._call("GET", "/curator/tables", "curator")
        self.assertEqual(code, 200)
        self.assertEqual(len(rows), 1)
        t = rows[0]
        for k in ("doc_id", "doc_title", "sheet", "columns", "rows"):
            self.assertIn(k, t)
        self.assertEqual(t["doc_id"], fx.DOC_ID)
        self.assertEqual([c["name"] for c in t["columns"]], ["item", "amount", "owner"])
        self.assertEqual(t["rows"], 3)
        self.assertTrue(t["available"])
        # the read-only sample the static preview serves
        self.assertEqual(t["sample"][0][0], "licences")

    def test_tables_query_is_select_only_and_honest_when_unavailable(self):
        code, out = self._call(
            "POST",
            "/curator/tables/query",
            "curator",
            {"doc_id": fx.DOC_ID, "sheet": fx.SHEET, "sql": "DELETE FROM t"},
        )
        self.assertEqual(code, 400)
        code, out = self._call(
            "POST",
            "/curator/tables/query",
            "curator",
            {"doc_id": fx.DOC_ID, "sheet": fx.SHEET, "sql": "SELECT * FROM t"},
        )
        if http_api._table_query_fn() is None:
            self.assertEqual(code, 501)
            self.assertIn("unavailable", out["error"])
        else:
            self.assertEqual(code, 200)
            self.assertIn("rows", json.dumps(out))

    def test_insights(self):
        code, ins = self._call("GET", "/curator/insights", "curator")
        self.assertEqual(code, 200)
        caps = ins["capabilities"]
        self.assertEqual(set(caps), {"rag", "caching", "enterprise_readiness", "dashboard_ui"})
        self.assertEqual(caps["rag"], [{"repo": fx.REPO_A, "confidence": 0.92}])
        # reuse: summary.json wins for repo A (it exists); nothing for repo B
        self.assertEqual(
            ins["reuse"],
            [
                {
                    "repo": fx.REPO_A,
                    "symbol": "AnswerService.ask",
                    "path": "knowledge_fabric/answer/service.py",
                    "why": "the single governed entry point",
                }
            ],
        )

    def test_reuse_falls_back_to_caching_reusable(self):
        os.remove(os.path.join(self.tmp, "analysis", fx.slug(fx.REPO_A), "summary.json"))
        try:
            reuse = fabric_views.insights()["reuse"]
            self.assertEqual(reuse[0]["symbol"], "AnswerCache")
        finally:
            fx.write_fabric(self.tmp)


class TestSourcesCorpusGates(_Served):
    def test_admin_sources_extended_keeps_old_keys(self):
        code, out = self._call("GET", "/admin/sources", "admin")
        self.assertEqual(code, 200)
        self.assertIn("sources", out)  # the pre-T47 payload
        gh = out["github"]
        self.assertEqual(gh["counts"], {"repositories": 2, "commits": 445, "prs": 78, "issues": 0})
        for k in ("last_run", "next_run", "rate_limit_remaining"):
            self.assertIn(k, gh)
        self.assertEqual(out["jira"]["counts"], {"projects": 1, "issues": 120})
        self.assertEqual(out["confluence"]["counts"], {"spaces": 1, "pages": 34})
        for src in ("jira", "confluence"):
            self.assertIn("last_run", out[src])
            self.assertIn("next_run", out[src])

    def test_api_corpus_extended_keeps_old_keys(self):
        code, out = self._call("GET", "/api/corpus", "asker.public")
        self.assertEqual(code, 200)
        for k in ("documents", "passages", "entities", "relationships", "domains"):
            self.assertIn(k, out)
        self.assertEqual(out["repositories"], 2)
        self.assertEqual(out["jira_projects"], 1)
        self.assertEqual(out["confluence_spaces"], 1)
        # the fixture's one sheet + every CSV the seed ingested through the T41
        # spreadsheet route (each becomes tables/<doc>/<sheet>.sqlite + a facts row)
        self.assertEqual(out["tables"], len(fabric_views.tables()))
        self.assertGreaterEqual(out["tables"], 1)
        self.assertIn(fx.DOC_ID, {t["doc_id"] for t in fabric_views.tables()})
        self.assertEqual(out["images"], 2)

    def test_role_gates(self):
        for path in (
            "/curator/repositories",
            "/curator/repository?repo=x",
            "/curator/tables",
            "/curator/insights",
            "/admin/sources",
        ):
            code, _ = self._call("GET", path, None)
            self.assertEqual(code, 401, path)
            code, _ = self._call("GET", path, "asker.public")
            self.assertEqual(code, 403, path)
            code, _ = self._call("GET", path, "admin")
            self.assertIn(code, (200, 404), path)  # admin inherits curate
        for path, body in (
            ("/curator/repository/delete", {"repo": fx.REPO_B}),
            ("/curator/tables/query", {"doc_id": "d", "sheet": "s", "sql": "SELECT 1"}),
        ):
            code, _ = self._call("POST", path, "asker.public", body)
            self.assertEqual(code, 403, path)

    def test_empty_fabric_renders_empty_not_broken(self):
        prev = fx.point_env(os.path.join(self.tmp, "does-not-exist"))
        try:
            self.assertEqual(fabric_views.repositories(), [])
            self.assertEqual(fabric_views.tables(), [])
            self.assertEqual(fabric_views.insights(), {"capabilities": {}, "reuse": []})
            self.assertEqual(
                fabric_views.corpus_tiles(),
                {
                    "repositories": 0,
                    "jira_projects": 0,
                    "confluence_spaces": 0,
                    "tables": 0,
                    "images": 0,
                },
            )
        finally:
            fx.restore_env(prev)
            fx.point_env(self.tmp)


class TestPagesCarryThePanels(unittest.TestCase):
    def test_curator_page_has_repositories_insights_tables(self):
        for needle in (
            'id="repositories-card"',
            'id="repo-rows"',
            'id="repo-panel"',
            'id="insights-capabilities"',
            'id="insights-reuse"',
            'id="tables-card"',
            'id="tq-run"',
            "/curator/repositories",
            "/curator/repository?repo=",
            "/curator/tables/query",
        ):
            self.assertIn(needle, CURATOR_HTML, needle)

    def test_admin_page_has_sources_cards_and_models_untouched(self):
        self.assertIn('id="sources-cards"', ADMIN_HTML)
        self.assertIn("loadSources", ADMIN_HTML)
        self.assertIn('id="models-panel"', ADMIN_HTML)  # T35/T36 panel still there

    def test_workspace_has_ten_tiles_and_step_lines(self):
        for key in ("repositories", "jira_projects", "confluence_spaces", "tables", "images"):
            self.assertIn(f'id="tile-{key}"', ASK_HTML)
        self.assertIn("function renderTiles", ASK_HTML)
        self.assertIn("KF.streamStep=", ASK_HTML)
        self.assertIn("Checked <b>", ASK_HTML)


class TestShowcaseSnapshot(unittest.TestCase):
    """The static build bakes the new GETs (and the per-repo card) so the Pages
    build renders the Repositories / Tables / Insights / Sources panels."""

    @classmethod
    def setUpClass(cls):
        from scripts import build_showcase

        cls.tmp = tempfile.mkdtemp(prefix="kf-t47-showcase-")
        cls.fabric = fx.write_fabric(os.path.join(cls.tmp, "fabric-data"))
        cls.prev_env = fx.point_env(cls.fabric)
        cls.prev_limit = os.environ.get("KF_SHOWCASE_CORPUS_LIMIT")
        os.environ["KF_SHOWCASE_CORPUS_LIMIT"] = "2"
        cls.out = os.path.join(cls.tmp, "showcase")
        build_showcase.build(cls.out)
        with open(os.path.join(cls.out, "snapshot.json"), encoding="utf-8") as fh:
            cls.snap = json.load(fh)

    @classmethod
    def tearDownClass(cls):
        fx.restore_env(cls.prev_env)
        if cls.prev_limit is None:
            os.environ.pop("KF_SHOWCASE_CORPUS_LIMIT", None)
        else:
            os.environ["KF_SHOWCASE_CORPUS_LIMIT"] = cls.prev_limit
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def test_snapshot_carries_the_new_keys(self):
        cur, adm = self.snap["get"]["curator"], self.snap["get"]["admin"]
        for path in ("/curator/repositories", "/curator/tables", "/curator/insights"):
            self.assertIn(path, cur, path)
            self.assertIn(path, adm, path)
        self.assertEqual([r["repo"] for r in cur["/curator/repositories"]], [fx.REPO_A, fx.REPO_B])
        self.assertEqual(cur["/curator/tables"][0]["sample"][0][0], "licences")
        self.assertIn("rag", cur["/curator/insights"]["capabilities"])
        self.assertIn("github", adm["/admin/sources"])
        self.assertEqual(adm["/admin/sources"]["github"]["counts"]["repositories"], 2)
        self.assertEqual(self.snap["corpus"]["repositories"], 2)
        self.assertEqual(self.snap["corpus"]["images"], 2)
        self.assertIn("consumption", adm["/admin/models"])  # the T36 panel still bakes
        # the per-repository card overlay
        self.assertEqual(set(self.snap["repository"]), {fx.REPO_A, fx.REPO_B})
        self.assertIn("# Architecture", self.snap["repository"][fx.REPO_A]["architecture_md"])

    def test_engine_serves_the_new_paths(self):
        with open(os.path.join(self.out, "engine.js"), encoding="utf-8") as fh:
            eng = fh.read()
        for needle in (
            "/curator/repository",
            "/curator/tables/query",
            "static preview",
            "/curator/repository/delete",
        ):
            self.assertIn(needle, eng, needle)
        self.assertNotIn("http://", eng)
        self.assertNotIn("https://", eng)


if __name__ == "__main__":
    unittest.main()
