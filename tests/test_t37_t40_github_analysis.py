"""T37–T40 — the live GitHub connector, code understanding, dependencies and
licences, capability detection: all offline, through injected transports.

* a fake GitHub (REST + GraphQL) serves one repository: exact commit count,
  PRs with reviews/comments, issues, releases, deployments, workflows, and a
  "clone" the fake runner materialises on disk;
* ``github_live.sync`` writes the pinned facts block, files one document per
  PR / commit / issue / release / source file, and records the rate limit;
* ``analysis.run`` produces symbols, comments, call graph, dependencies with
  licences (fake PyPI / npm), capabilities with evidence lines and the
  15-point readiness checklist, the card — and SKIPS the model summary in
  keyless mode; with a fake provider the summary is validated and stored.
"""

from __future__ import annotations

import json
import os
import shutil
import tempfile
import unittest
from unittest import mock

os.environ.setdefault("KF_MODEL_MODE", "extractive")

from knowledge_fabric import analysis  # noqa: E402
from knowledge_fabric import fabric_data as fd
from knowledge_fabric.analysis import capabilities, dependencies, summary  # noqa: E402
from knowledge_fabric.app import Platform  # noqa: E402
from knowledge_fabric.connectors import github_live  # noqa: E402
from knowledge_fabric.tenants import demo  # noqa: E402

T = "qualizeal"
REPO = "acme/knowledge-fabric"
API = github_live.API

CLONE_FILES = {
    "README.md": "# Knowledge Fabric\n\nA knowledge fabric with RAG over a knowledge graph.\n",
    "pyproject.toml": (
        '[project]\nname = "kf"\ndependencies = ["fastapi>=0.115", "rank_bm25", "left-pad"]\n'
        '[project.optional-dependencies]\ndev = ["pytest>=8"]\n'
    ),
    "package.json": '{"dependencies": {"recharts": "^2.0", "some-gpl-lib": "1.0"}}',
    "kf/__init__.py": "",
    "kf/retrieve.py": (
        '"""Retrieval: bm25 + dense embeddings fused with rrf."""\n'
        "from functools import lru_cache\n\n\n"
        "@lru_cache(maxsize=128)\n"
        "def bm25_search(q):\n"
        '    """BM25 over the passages."""\n'
        "    return rerank(q)\n\n\n"
        "def rerank(q):\n"
        "    # NOTE: reciprocal rank fusion (rrf) of bm25 and dense results\n"
        "    return q\n\n\n"
        "def answer(q):\n"
        "    hits = bm25_search(q)\n"
        "    return client.messages.create(model='x', messages=hits)\n"
    ),
    "kf/graph.py": "import networkx\n\n\ndef entities(text):\n    return networkx.Graph()\n",
    "kf/auth.py": (
        "def login(user, password):\n    return verify_token(user)\n\n\n"
        "def verify_token(t):\n    return t\n"
    ),
    "static/dashboard.css": ".dashboard { display: grid } .kpi { color: red }\n",
    "tests/test_a.py": "def test_a():\n    assert True\n",
    "Dockerfile": "FROM python:3.11-slim\n",
    ".github/workflows/ci.yml": "name: ci\non: [push]\n",
    "LICENSE": "MIT License\n",
    "docs/arch.md": "# Architecture\n\nrate_limit is enforced in the API.\n",
}

GQL_REPO = {
    "repository": {
        "defaultBranchRef": {"name": "main", "target": {"history": {"totalCount": 1284}}},
        "open": {"totalCount": 9},
        "closed": {"totalCount": 31},
        "merged": {"totalCount": 212},
        "openIssues": {"totalCount": 4},
        "closedIssues": {"totalCount": 40},
    }
}
GQL_PRS = {
    "repository": {
        "pullRequests": {
            "pageInfo": {"hasNextPage": False, "endCursor": None},
            "nodes": [
                {
                    "number": 57,
                    "title": "harden the ledger",
                    "body": "Hardens the API ledger against a missing day file.",
                    "state": "MERGED",
                    "merged": True,
                    "createdAt": "2026-09-01T00:00:00Z",
                    "updatedAt": "2026-09-02T00:00:00Z",
                    "mergedAt": "2026-09-02T00:00:00Z",
                    "closedAt": "2026-09-02T00:00:00Z",
                    "additions": 180,
                    "deletions": 60,
                    "changedFiles": 6,
                    "baseRefName": "main",
                    "headRefName": "ledger",
                    "url": "https://github.com/acme/knowledge-fabric/pull/57",
                    "author": {"login": "alice"},
                    "labels": {"nodes": [{"name": "bug"}]},
                    "reviews": {
                        "nodes": [
                            {
                                "author": {"login": "bob"},
                                "state": "APPROVED",
                                "body": "LGTM",
                                "submittedAt": "2026-09-02T00:00:00Z",
                                "url": "https://github.com/acme/knowledge-fabric/pull/57#pullrequestreview-1",
                            }
                        ]
                    },
                    "comments": {
                        "nodes": [
                            {
                                "author": {"login": "carol"},
                                "body": "thanks",
                                "createdAt": "",
                                "url": "",
                            }
                        ]
                    },
                    "files": {"nodes": [{"path": "kf/ledger.py"}]},
                }
            ],
        }
    }
}
GQL_COMMITS = {
    "repository": {
        "ref": {
            "target": {
                "history": {
                    "pageInfo": {"hasNextPage": False, "endCursor": None},
                    "nodes": [
                        {
                            "oid": "abc123def4567890",
                            "messageHeadline": "ledger: harden",
                            "message": "ledger: harden against a missing day file",
                            "committedDate": "2026-09-02T00:00:00Z",
                            "url": "https://github.com/acme/knowledge-fabric/commit/abc123def4567890",
                            "additions": 10,
                            "deletions": 2,
                            "author": {"name": "Alice", "user": {"login": "alice"}},
                        }
                    ],
                }
            }
        }
    }
}
GQL_ISSUES = {
    "repository": {
        "issues": {
            "pageInfo": {"hasNextPage": False, "endCursor": None},
            "nodes": [
                {
                    "number": 3,
                    "title": "Flaky test",
                    "body": "test_a is flaky",
                    "state": "OPEN",
                    "createdAt": "2026-09-03T00:00:00Z",
                    "updatedAt": "2026-09-03T00:00:00Z",
                    "closedAt": None,
                    "url": "https://github.com/acme/knowledge-fabric/issues/3",
                    "author": {"login": "dave"},
                    "labels": {"nodes": []},
                    "comments": {"nodes": []},
                }
            ],
        }
    }
}
REST = {
    f"/repos/{REPO}": {
        "full_name": REPO,
        "html_url": f"https://github.com/{REPO}",
        "description": "The fabric",
        "homepage": "",
        "topics": ["rag"],
        "default_branch": "main",
        "visibility": "public",
        "private": False,
        "archived": False,
        "size": 1200,
        "stargazers_count": 5,
        "forks_count": 1,
        "license": {"spdx_id": "MIT"},
        "created_at": "2025-01-01T00:00:00Z",
        "pushed_at": "2026-09-10T00:00:00Z",
        "open_issues_count": 4,
        "language": "Python",
    },
    f"/repos/{REPO}/languages": {"Python": 90000, "JavaScript": 10000},
    f"/repos/{REPO}/contributors": [
        {"login": "alice", "contributions": 640},
        {"login": "bob", "contributions": 400},
    ],
    f"/repos/{REPO}/releases": [
        {
            "tag_name": "v0.4.0",
            "name": "0.4.0",
            "published_at": "2026-08-01T00:00:00Z",
            "html_url": f"https://github.com/{REPO}/releases/tag/v0.4.0",
            "body": "Adds the ledger.",
        }  # fmt: skip
    ],
    f"/repos/{REPO}/tags": [{"name": "v0.4.0"}],
    f"/repos/{REPO}/deployments": [
        {"id": 1, "environment": "production"},
        {"id": 2, "environment": "staging"},
    ],
    f"/repos/{REPO}/deployments/1/statuses": [{"state": "success"}],
    f"/repos/{REPO}/deployments/2/statuses": [{"state": "success"}],
    f"/repos/{REPO}/actions/workflows": {
        "workflows": [
            {"id": 9, "name": "ci", "path": ".github/workflows/ci.yml", "state": "active"}
        ]
    },
    f"/repos/{REPO}/actions/workflows/9/runs": {
        "workflow_runs": [{"conclusion": "success", "updated_at": "2026-09-10T00:00:00Z"}]
    },
    "/orgs/acme/repos": [{"full_name": REPO, "fork": False}],
}


class FakeGitHub:
    def __init__(self):
        self.calls: list[str] = []

    def __call__(self, url, headers, timeout=30, method="GET", body=None):
        self.calls.append(f"{method} {url}")
        assert headers.get("Authorization") == "Bearer ghp_testtoken1234567"
        hdrs = {
            "X-RateLimit-Remaining": "4321",
            "X-RateLimit-Reset": "1800000000",
            "X-RateLimit-Limit": "5000",
        }
        if url.endswith("/graphql"):
            q = json.loads(body)["query"]
            if "history { totalCount }" in q:
                data = GQL_REPO
            elif "pullRequests(first" in q:
                data = GQL_PRS
            elif "ref(qualifiedName" in q:
                data = GQL_COMMITS
            else:
                data = GQL_ISSUES
            return 200, hdrs, json.dumps({"data": data}).encode()
        path = url[len(API) :].split("?", 1)[0]
        if path in REST:
            return 200, hdrs, json.dumps(REST[path]).encode()
        return 404, hdrs, b'{"message":"Not Found"}'


def fake_runner(cmd, capture_output=True, text=True):
    dest = cmd[-1]
    assert "x-access-token:ghp_testtoken1234567@" in cmd[-2]
    for rel, content in CLONE_FILES.items():
        p = os.path.join(dest, rel)
        os.makedirs(os.path.dirname(p), exist_ok=True)
        with open(p, "w", encoding="utf-8") as f:
            f.write(content)

    class R:
        returncode = 0
        stderr = ""

    return R()


def fake_registry(url, headers, timeout=20):
    if "pypi.org/pypi/fastapi/" in url:
        return 200, json.dumps({"info": {"license_expression": "MIT"}}).encode()
    if "pypi.org/pypi/rank_bm25/" in url:
        return 200, json.dumps(
            {"info": {"classifiers": ["License :: OSI Approved :: Apache Software License"]}}
        ).encode()
    if "pypi.org/pypi/left-pad/" in url:
        return 404, b"{}"
    if "pypi.org/pypi/pytest/" in url:
        return 200, json.dumps({"info": {"license": "MIT"}}).encode()
    if "registry.npmjs.org/recharts" in url:
        return 200, json.dumps({"license": "MIT"}).encode()
    if "registry.npmjs.org/some-gpl-lib" in url:
        return 200, json.dumps({"license": {"type": "GPL-3.0-only"}}).encode()
    return 500, b"boom"


class _Fabric(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="kf-t37-")
        self.prev = {
            k: os.environ.get(k)
            for k in ("KF_FABRIC_ROOT", "KF_DATA_ROOT", "GITHUB_TOKEN", "GITHUB_ORG")
        }
        os.environ["KF_FABRIC_ROOT"] = self.tmp
        os.environ["KF_DATA_ROOT"] = os.path.join(self.tmp, "data")
        os.environ["GITHUB_TOKEN"] = "ghp_testtoken1234567"
        os.environ["GITHUB_ORG"] = "acme"
        self.p = Platform(db_path=":memory:", blob_root=os.path.join(self.tmp, "blobs"))
        demo.seed(self.p, [T])

    def tearDown(self):
        for k, v in self.prev.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        shutil.rmtree(self.tmp, ignore_errors=True)


class TestGitHubLive(_Fabric):
    def test_sync_writes_exact_facts_and_files_activity_documents(self):
        gh = FakeGitHub()
        res = github_live.sync(self.p, T, transport=gh, runner=fake_runner)
        self.assertEqual(res["status"], "ran")
        self.assertGreater(res["ingested"], 5)
        facts = fd.read_json(fd.data_path("facts.json"), {})
        r = facts["repositories"][REPO]
        self.assertEqual(r["commits"]["total"], 1284)  # exact, from history.totalCount
        self.assertEqual(r["pull_requests"], {
            "open": 9, "closed": 31, "merged": 212, "total": 252,
            "mean_size": {"additions": 180, "deletions": 60, "changed_files": 6},
        })  # fmt: skip
        self.assertEqual(r["primary_language"], "Python")
        self.assertAlmostEqual(r["languages"]["Python"]["share"], 0.9)
        self.assertEqual(r["contributors_count"], 2)
        self.assertEqual(r["deployments"], {
            "environments": ["production", "staging"],
            "latest": {"production": "success", "staging": "success"},
            "count": 2,
        })  # fmt: skip
        self.assertEqual(r["workflows"][0]["last_conclusion"], "success")
        self.assertEqual(r["releases"][0]["tag"], "v0.4.0")
        self.assertEqual(r["acl"], ["group:everyone", "public"])
        self.assertTrue(os.path.isdir(r["clone_dir"]))
        self.assertEqual(r["default_branch"], "main")
        # documents: one per PR / commit / issue / release + the clone's files
        uris = {d["uri"] for d in self.p.documents.list(T)}
        for tail in (
            "pulls/57",
            "commits/abc123def4567890",
            "issues/3",
            "releases/v0.4.0",
            "kf/retrieve.py",
            "README.md",
        ):
            self.assertIn(f"github://{REPO}/{tail}", uris)
        pr = self.p.documents.by_source_uri(T, "github_live", f"github://{REPO}/pulls/57")
        meta = self.p.documents.meta_of(T, pr["id"])
        self.assertEqual(meta["citation_url"], f"https://github.com/{REPO}/pull/57")
        self.assertEqual(meta["area"], "Work item")
        # every PR passage carries its own citation URL (review → the review URL)
        texts = {pp.text[:12]: pp for pp in self.p.passages.by_document(T, pr["id"])}
        self.assertIn("Review by bo", texts)
        self.assertTrue(
            texts["Review by bo"].coordinate.locator["citation_url"].endswith("pullrequestreview-1")
        )
        # the rate limit is recorded for Admin → Sources
        self.assertEqual(github_live.rate_limit_remaining(), 4321)
        # the documents block was refreshed (T42)
        self.assertGreater(facts["documents"]["total"], 5)
        # second run: unchanged pushed_at → skipped via the cursor
        res2 = github_live.sync(self.p, T, transport=gh, runner=fake_runner)
        self.assertEqual(res2["pulled"], 0)

    def test_sync_is_explicit_when_unconfigured(self):
        os.environ.pop("GITHUB_TOKEN", None)
        res = github_live.sync(self.p, T)
        self.assertEqual(res["status"], "skipped")
        self.assertIn("GITHUB_TOKEN", res["reason"])

    def test_rate_limit_exhaustion_raises_not_skips(self):
        def transport(url, headers, timeout=30, method="GET", body=None):
            return 403, {"X-RateLimit-Remaining": "0", "X-RateLimit-Reset": "1800000000"}, b"{}"

        c = github_live.GitHubLiveConnector(
            T, {"repos": [REPO]}, transport=transport, runner=fake_runner
        )
        with self.assertRaises(github_live.ConnectorError) as cm:
            c.pull(None)
        self.assertIn("rate limit exhausted", str(cm.exception))
        self.assertNotIn("ghp_testtoken", str(cm.exception))


class TestAnalysis(_Fabric):
    def _ingest(self):
        github_live.sync(self.p, T, transport=FakeGitHub(), runner=fake_runner)

    def test_run_analyses_the_clone_and_skips_the_summary_without_a_provider(self):
        self._ingest()
        res = analysis.run(self.p, T, transport=fake_registry)
        self.assertEqual(res["analysed"], [REPO])
        slug = fd.repo_slug(REPO)
        for name in ("symbols.jsonl", "comments.jsonl", "callgraph.json", "card.md"):
            self.assertTrue(os.path.exists(fd.path("analysis", slug, name)), name)
        self.assertFalse(os.path.exists(fd.path("analysis", slug, "architecture.md")))
        syms = fd.read_jsonl(fd.path("analysis", slug, "symbols.jsonl"))
        names = {s["symbol"] for s in syms}
        self.assertTrue({"bm25_search", "rerank", "login", "verify_token"} <= names)
        graph = fd.read_json(fd.path("analysis", slug, "callgraph.json"))
        self.assertIn(
            ["kf.retrieve.bm25_search", "kf.retrieve.rerank"],
            [
                list(e[:2]) if isinstance(e, list) else [e.get("from"), e.get("to")]
                for e in graph["edges"]
            ],
        )
        comments = fd.read_jsonl(fd.path("analysis", slug, "comments.jsonl"))
        self.assertTrue(any(c["tag"] == "NOTE" for c in comments))
        state = fd.read_json(fd.data_path("analysis_state.json"))
        self.assertEqual(state[REPO]["result"]["summary"]["model"], "skipped (extractive)")
        # unchanged → skipped on the next run
        self.assertEqual(analysis.run(self.p, T, transport=fake_registry)["skipped"], [REPO])
        # the card is a searchable analysis document
        card = self.p.documents.by_source_uri(T, "analysis", f"analysis://{REPO}/card")
        card_meta = self.p.documents.meta_of(T, card["id"])
        self.assertEqual(card_meta["source_kind"], "analysis")
        self.assertEqual(card_meta["authority"], 65)
        md = open(fd.path("analysis", slug, "card.md"), encoding="utf-8").read()
        self.assertIn("**Commits:** 1,284 on main", md)
        self.assertIn("**CI:** .github/workflows/ci.yml", md)

    def test_dependencies_resolve_licences_and_categories(self):
        self._ingest()
        analysis.run(self.p, T, transport=fake_registry)
        deps = fd.read_json(fd.data_path("dependencies.json"))[REPO]
        by = {d["name"]: d for d in deps}
        self.assertEqual(
            (by["fastapi"]["licence"], by["fastapi"]["category"]), ("MIT", "permissive")
        )
        self.assertEqual(by["rank_bm25"]["category"], "permissive")
        self.assertEqual(by["some-gpl-lib"]["category"], "copyleft")
        self.assertEqual(
            (by["left-pad"]["licence"], by["left-pad"]["licence_source"]), ("unknown", "HTTP 404")
        )
        self.assertEqual(by["fastapi"]["manifest_path"], "pyproject.toml")
        self.assertEqual(by["recharts"]["ecosystem"], "npm")
        cache = fd.read_json(fd.data_path("licence_cache.json"))
        self.assertEqual(cache["pypi:fastapi"]["licence"], "MIT")

    def test_capabilities_have_evidence_lines_and_the_readiness_checklist(self):
        self._ingest()
        analysis.run(self.p, T, transport=fake_registry)
        caps = {
            c["capability"]: c
            for c in fd.read_json(fd.data_path("capabilities.json"))
            if c["repo"] == REPO
        }
        self.assertTrue(
            {"rag", "knowledge_graph", "caching", "auth", "dashboard_ui", "enterprise_readiness"}
            <= set(caps)
        )
        rag = caps["rag"]
        self.assertTrue(all({"path", "line", "snippet"} <= set(e) for e in rag["evidence"]))
        self.assertTrue(
            {"bm25", "dense", "rrf", "rerank"} <= set(rag["attributes"]["retrieval_techniques"])
        )
        self.assertGreaterEqual(rag["confidence"], 0.6)
        self.assertIn("login", caps["auth"]["attributes"]["login_symbols"])
        self.assertEqual(caps["dashboard_ui"]["attributes"]["css_files"], ["static/dashboard.css"])
        self.assertIn("recharts", caps["dashboard_ui"]["attributes"]["chart_library"])
        self.assertTrue(
            any(r["symbol"] == "bm25_search" for r in caps["caching"]["attributes"]["reusable"])
        )
        ready = caps["enterprise_readiness"]["attributes"]
        items = {c["item"]: c["present"] for c in ready["checklist"]}
        self.assertEqual(len(items), 16)
        self.assertTrue(
            items["Dockerfile"] and items["CI"] and items["licence file"] and items["auth"]
        )
        self.assertTrue(items["multi-environment deployments"])
        self.assertFalse(items["tests ≥ 20"])
        self.assertEqual(ready["score"], round(100 * sum(items.values()) / 16))

    def test_summary_is_validated_and_stored_with_a_provider(self):
        self._ingest()
        calls = []

        class FakeClient:
            def available(self):
                return True

            def messages(self, body, purpose="answer_bake", **kw):
                calls.append((purpose, body["model"]))
                reply = {
                    "purpose": "Answer questions over the org's knowledge.",
                    "architecture": (
                        "Retrieval fuses bm25 and dense results. The graph module builds entities."
                    ),
                    "components": [
                        {"name": "retrieve", "path": "kf/retrieve.py", "role": "retrieval"}
                    ],
                    "data_flow": "question → retrieve → answer",
                    "retrieval_techniques": ["bm25", "dense", "rrf"],
                    "reuse_candidates": [
                        {"symbol": "bm25_search", "path": "kf/retrieve.py", "why": "generic"}
                    ],
                    "risks": ["no load test"],
                    "citations": ["kf/retrieve.py", "kf/graph.py"],
                }
                return {
                    "content": [{"type": "text", "text": json.dumps(reply)}],
                    "model": body["model"],
                    "usage": {},
                    "request_id": "req_1",
                    "cost_usd": 0.01,
                }

        with (
            mock.patch.object(self.p, "model", FakeClient()),
            mock.patch.dict(os.environ, {"KF_MODEL_MODE": "anthropic"}),
        ):
            res = analysis.run(self.p, T, transport=fake_registry, force=True)
        self.assertEqual(res["analysed"], [REPO])
        # weak-evidence rows are classified first (capability_classify on the small
        # tier, which is Sonnet when Haiku is not listed); then the one summary call
        self.assertIn(("repo_summary", "claude-sonnet-4-6"), calls)
        self.assertEqual(calls.count(("repo_summary", "claude-sonnet-4-6")), 1)
        md = open(
            fd.path("analysis", fd.repo_slug(REPO), "architecture.md"), encoding="utf-8"
        ).read()
        self.assertIn("(kf/retrieve.py)", md)  # every sentence cites a path
        self.assertIn("Retrieval techniques: bm25, dense, rrf", md)
        doc = self.p.documents.by_source_uri(T, "analysis", f"analysis://{REPO}/architecture")
        self.assertEqual(doc["title"], f"{REPO} — architecture")
        self.assertEqual(self.p.documents.meta_of(T, doc["id"])["authority"], 65)

    def test_invalid_summary_is_rejected_not_stored(self):
        self._ingest()

        class BadClient:
            def messages(self, body, purpose="answer_bake", **kw):
                bad = {
                    "purpose": "x",
                    "architecture": "y",
                    "components": [{"name": "a", "path": "nope/missing.py", "role": "r"}],
                    "data_flow": "d",
                    "retrieval_techniques": ["magic"],
                    "reuse_candidates": [],
                    "risks": [],
                    "citations": ["kf/retrieve.py"],
                }
                return {
                    "content": [{"type": "text", "text": json.dumps(bad)}],
                    "model": body["model"],
                    "usage": {},
                }

        with (
            mock.patch.object(self.p, "model", BadClient()),
            mock.patch.dict(os.environ, {"KF_MODEL_MODE": "anthropic"}),
        ):
            res = analysis.run(self.p, T, transport=fake_registry, force=True)
        summ = fd.read_json(fd.data_path("analysis_state.json"))[REPO]["result"]["summary"]
        self.assertFalse(summ["written"])
        self.assertIn("not in the repository", summ["error"])
        self.assertIn("unknown retrieval technique", summ["error"])
        self.assertFalse(os.path.exists(fd.path("analysis", fd.repo_slug(REPO), "architecture.md")))
        self.assertEqual(res["analysed"], [REPO])


class TestParsers(unittest.TestCase):
    def test_every_manifest_kind_parses(self):
        cases = {
            "requirements.txt": (
                "requests==2.32\n# c\n-r other.txt\nnumpy>=1.26 ; python_version>'3'\n",
                {("requests", "==2.32"), ("numpy", ">=1.26")},
            ),
            "Pipfile": (
                '[packages]\nflask = "*"\n[dev-packages]\nblack = {version = "==24.0"}\n',
                {("flask", ""), ("black", "==24.0")},
            ),
            "go.mod": (
                "module x\nrequire (\n\tgithub.com/gin-gonic/gin v1.9.0\n)\n"
                "require golang.org/x/net v0.1.0\n",
                {("github.com/gin-gonic/gin", "v1.9.0"), ("golang.org/x/net", "v0.1.0")},
            ),
            "pom.xml": (
                "<project><dependencies><dependency><groupId>org.a</groupId><artifactId>b</artifactId><version>1.0</version></dependency></dependencies></project>",
                {("org.a:b", "1.0")},
            ),
            "build.gradle": (
                "dependencies { implementation 'org.x:y:2.0'\n"
                ' testImplementation("junit:junit:4.13") }',
                {("org.x:y", "2.0"), ("junit:junit", "4.13")},
            ),
            "Gemfile": (
                "gem 'rails', '~> 7.0'\ngem \"puma\"\n",
                {("rails", "~> 7.0"), ("puma", "")},
            ),
            "Cargo.toml": (
                '[dependencies]\nserde = "1.0"\ntokio = { version = "1", features = ["full"] }\n',
                {("serde", "1.0"), ("tokio", "1")},
            ),
            "app.csproj": (
                '<Project><ItemGroup><PackageReference Include="Newtonsoft.Json" '
                'Version="13.0.1" /></ItemGroup></Project>',
                {("Newtonsoft.Json", "13.0.1")},
            ),
        }
        for name, (text, expect) in cases.items():
            rows = dependencies.parse_manifest(name, text)
            self.assertEqual({(r["name"], r["version"]) for r in rows}, expect, name)

    def test_categorise(self):
        self.assertEqual(dependencies.categorise("MIT"), "permissive")
        self.assertEqual(dependencies.categorise("Apache Software License"), "permissive")
        self.assertEqual(dependencies.categorise("GPL-3.0-only"), "copyleft")
        self.assertEqual(dependencies.categorise("BUSL-1.1"), "non_commercial")
        self.assertEqual(dependencies.categorise(""), "unknown")
        self.assertEqual(dependencies.categorise("Something Odd"), "unknown")

    def test_summary_validation_rules(self):
        paths = {"a/b.py", "README.md"}
        ok = {
            "purpose": "p",
            "architecture": "a",
            "components": [],
            "data_flow": "d",
            "retrieval_techniques": ["bm25"],
            "reuse_candidates": [],
            "risks": [],
            "citations": ["a/b.py"],
        }
        self.assertEqual(summary.validate(ok, paths), [])
        self.assertIn("citations is empty", summary.validate({**ok, "citations": []}, paths))
        self.assertTrue(
            any(
                "missing risks" in p
                for p in summary.validate({k: v for k, v in ok.items() if k != "risks"}, paths)
            )
        )
        self.assertTrue(
            any(
                "not in the repository" in p
                for p in summary.validate({**ok, "citations": ["zzz.py"]}, paths)
            )
        )

    def test_capability_scan_is_bounded_and_evidence_shaped(self):
        tmp = tempfile.mkdtemp()
        try:
            os.makedirs(os.path.join(tmp, "node_modules", "x"))
            with open(os.path.join(tmp, "node_modules", "x", "i.js"), "w") as f:
                f.write("faiss faiss faiss")
            with open(os.path.join(tmp, "a.py"), "w") as f:
                f.write("import faiss\nembeddings = 1\n")
            ev, signals = capabilities.scan(tmp)
            self.assertEqual({e["path"] for e in ev["rag"]}, {"a.py"})  # node_modules skipped
            self.assertEqual(ev["rag"][0]["line"], 1)
        finally:
            shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    unittest.main()
