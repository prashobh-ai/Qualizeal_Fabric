"""T150 — content-rich questions are ANSWERED, not turned into a clarify.

Two layers:

* A fast, build-free check on the server resolver (``answer/context.py``): an
  existential/expletive phrasing ("is there any code…", "how many … are there")
  and an integrate/migrate question resolve to a real retrieval, while a genuine
  bare pronoun ("when was it made") still asks back. This guards the regression
  that put ``there`` in the pronoun set and ``integrate|migrate`` in the
  comparison-intent set, which short-circuited retrieval with a product-chip
  clarify.

* A real-engine check: build a small showcase and drive the shipped
  ``engine.js`` under a Node shim, asserting the same questions the visitor types
  come back as answers (not clarifies), and that a bare pronoun still clarifies.
  Skips cleanly where Node is absent.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import tempfile
import unittest

os.environ.setdefault("KF_MODEL_MODE", "off")

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
RUNNER = os.path.join(ROOT, "scripts", "showcase", "ask_runner.js")

_SUBJECTS = {"qmentisai": "QMentisAI", "validaite": "ValidAIte", "nexaai": "NexaAI"}
_BANK = ["QMentisAI", "ValidAIte", "NexaAI"]


class TestResolverRouting(unittest.TestCase):
    """The resolution ladder no longer clarifies a content-rich question."""

    def _resolve(self, q):
        from knowledge_fabric.answer import context as cx

        return cx.resolve(q, [], _SUBJECTS, _BANK)

    def test_existential_there_is_not_a_pronoun(self):
        # "there" here is existential, not anaphora → must not ask back.
        self.assertIsNone(self._resolve("Is there any code on login or SSO?").clarify)
        self.assertIsNone(self._resolve("How many repositories are there?").clarify)

    def test_integrate_question_is_not_a_comparison(self):
        self.assertIsNone(self._resolve("Is it possible to integrate with Jira?").clarify)

    def test_genuine_bare_pronoun_still_clarifies(self):
        self.assertIsNotNone(self._resolve("when was it made").clarify)

    def test_bare_compare_still_clarifies(self):
        self.assertIsNotNone(self._resolve("compare them").clarify)


@unittest.skipIf(shutil.which("node") is None, "node not available")
class TestEngineRouting(unittest.TestCase):
    """The shipped engine answers the questions the visitor actually types."""

    @classmethod
    def setUpClass(cls):
        from scripts import build_showcase

        cls.tmp = tempfile.mkdtemp(prefix="kf-ask-")
        cls.out = os.path.join(cls.tmp, "showcase")
        os.environ["KF_SHOWCASE_CORPUS_LIMIT"] = "20"
        try:
            build_showcase.build(cls.out)
        finally:
            os.environ.pop("KF_SHOWCASE_CORPUS_LIMIT", None)
        qs = [
            "Is there any code on login or SSO?",
            "How many repositories are there?",
            "Is it possible to integrate with Jira?",
            "when was it made",
            "compare them",
        ]
        res = subprocess.run(
            ["node", RUNNER, cls.out, *qs],
            capture_output=True,
            text=True,
            timeout=120,
        )
        assert res.stdout.strip(), f"no runner output; stderr={res.stderr[-2000:]}"
        rows = json.loads(res.stdout)
        assert isinstance(rows, list), rows
        cls.byq = {r["q"]: r for r in rows}

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def test_existential_there_answers_not_clarifies(self):
        # The repo count is a fact answer present in every build.
        self.assertEqual(self.byq["How many repositories are there?"]["kind"], "answer")
        # The code/SSO question must at least not be a coreference clarify.
        self.assertNotEqual(self.byq["Is there any code on login or SSO?"]["kind"], "clarify")

    def test_integrate_answers_not_clarifies(self):
        self.assertNotEqual(self.byq["Is it possible to integrate with Jira?"]["kind"], "clarify")

    def test_bare_pronoun_and_compare_still_clarify(self):
        self.assertEqual(self.byq["when was it made"]["kind"], "clarify")
        self.assertEqual(self.byq["compare them"]["kind"], "clarify")


if __name__ == "__main__":
    unittest.main()
