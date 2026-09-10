"""T24 — source/asset discovery: search across the fabric (and live sources).

Employees ask "has anyone made auth code I can reuse?", "is there a script for
X?". The answer is a ranked LIST of real assets, searched over the ingested
fabric by the IndexedSourceSearcher and, on a real backend, live GitHub. All
offline here: the GitHub searcher is dormant without a token.
"""

from __future__ import annotations

import unittest
from unittest import mock

from knowledge_fabric.answer.search import (
    GitHubCodeSearcher,
    IndexedSourceSearcher,
    discover,
    is_discovery,
)
from knowledge_fabric.answer.service import AnswerService
from knowledge_fabric.contracts.types import AnswerKind
from knowledge_fabric.ingestion.intake import IngestWorker, Intake
from knowledge_fabric.tenants import demo
from tests.util import seeded

_AUTH = '''\
"""Auth helpers."""


def mint_session_token(subject, roles, scopes):
    """Mint a short-lived signed session token for single sign-on."""
    return {"subject": subject, "roles": roles, "scopes": scopes}


def check_scope(token, needed):
    """Authorise a request: the token must carry the needed scope."""
    return needed in token.get("scopes", [])
'''

_POLICY_DOC = (
    "# Single Sign-On Standard\n\n"
    "Single sign-on lets an employee sign in once and reach every permitted "
    "application. Reuse the shared authentication building blocks rather than "
    "writing your own login flow.\n"
)


class TestDiscoveryIntent(unittest.TestCase):
    def test_intent_matches_capability_questions(self):
        for q in [
            "has anyone made sso and auth code which I can reuse",
            "is there an automation script for login",
            "can I reuse the connector",
            "find me an example of token minting",
            "where can I find the leave policy",
        ]:
            self.assertTrue(is_discovery(q), q)

    def test_intent_ignores_plain_questions(self):
        for q in ["what is QMentisAI", "how does the pipeline chunk documents"]:
            self.assertFalse(is_discovery(q), q)


class TestDiscovery(unittest.TestCase):
    def setUp(self):
        self.p = seeded(["cf"])
        intake, worker = Intake(self.p), IngestWorker(self.p, None)
        worker.intake = intake
        intake.submit(
            intake.canonical(
                "cf",
                "github",
                "github://acme/app/auth.py",
                "auth.py",
                _AUTH.encode(),
                mime="text/x-python;code",
                acl=["public"],
            )
        )
        intake.submit(
            intake.canonical(
                "cf",
                "internal",
                "internal://acme/handbook/sso.md",
                "Sso Standard",
                _POLICY_DOC.encode(),
                mime="text/markdown",
                acl=["public"],
            )
        )
        worker.drain()
        self.svc = AnswerService(self.p)
        self.asker = demo.principal_for(self.p, "cf", "asker.public")

    def test_indexed_searcher_finds_auth_assets(self):
        hits = IndexedSourceSearcher(self.p).search(
            "cf", "reusable sso auth token", ["public"], k=6
        )
        self.assertTrue(hits)
        kinds = {h.kind for h in hits}
        self.assertTrue({"code", "policy"} & kinds)
        # a code hit is line-anchored to GitHub
        code = [h for h in hits if h.kind == "code"]
        self.assertTrue(any("#L" in h.url for h in code))

    def test_discover_dedupes_and_ranks(self):
        d = discover(self.p, "cf", "has anyone made auth code i can reuse", ["public"], k=6)
        self.assertIn("indexed", d.searched)
        self.assertTrue(d.hits)

    def test_service_returns_a_discovery_list(self):
        a = self.svc.ask(self.asker, "has anyone made sso and auth code which I can reuse")
        self.assertEqual(a.kind, AnswerKind.ANSWER)
        self.assertEqual((a.why or {}).get("level_name"), "discovery")
        self.assertTrue(len(a.citations) >= 1)
        self.assertIn("reuse", a.answer_text.lower())

    def test_github_searcher_dormant_without_token(self):
        gh = GitHubCodeSearcher(config={"token": ""})
        self.assertFalse(gh.available())
        self.assertEqual(gh.search("cf", "auth", ["public"]), [])

    def test_github_searcher_parses_results_when_available(self):
        gh = GitHubCodeSearcher(config={"token": "t", "repos": ["acme/app"]})
        payload = {
            "items": [
                {
                    "path": "src/auth.py",
                    "html_url": "https://github.com/acme/app/blob/main/src/auth.py",
                    "repository": {"full_name": "acme/app"},
                    "text_matches": [{"fragment": "def mint_session_token(...):"}],
                }
            ]
        }

        class _Resp:
            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

            def read(self):
                import json

                return json.dumps(payload).encode()

        with mock.patch("urllib.request.urlopen", lambda *a, **k: _Resp()):
            hits = gh.search("cf", "mint_session_token", ["public"], k=5)
        self.assertEqual(len(hits), 1)
        self.assertEqual(hits[0].source, "github")
        self.assertIn("github.com/acme/app", hits[0].url)


if __name__ == "__main__":
    unittest.main()
