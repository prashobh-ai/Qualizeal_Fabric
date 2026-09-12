"""T87 — stored per-user defaults.

A reader sets defaults once (persona view, depth, language, Explain auto-expand)
and the fabric applies them to every answer without re-asking. These tests prove
they persist per user, validate, and reshape the answer (persona lens, depth cap,
output language, Explain auto).
"""

from __future__ import annotations

import os
import tempfile
import unittest

import pytest

from knowledge_fabric.answer import defaults as ud
from knowledge_fabric.answer.service import AnswerService
from knowledge_fabric.contracts.types import AnswerKind
from knowledge_fabric.tenants import demo
from tests.util import T, seeded

_Q = "what must a release achieve before promotion?"


@pytest.fixture(autouse=True)
def _pin_data_root():
    prev = os.environ.get("KF_DATA_ROOT")
    os.environ["KF_DATA_ROOT"] = tempfile.mkdtemp(prefix="kf-defaults-")
    try:
        yield
    finally:
        if prev is None:
            os.environ.pop("KF_DATA_ROOT", None)
        else:
            os.environ["KF_DATA_ROOT"] = prev


class Base(unittest.TestCase):
    def setUp(self):
        self.p = seeded([T], model_mode="extractive")
        self.svc = AnswerService(self.p)

    def _ask(self):
        return self.svc.ask(demo.principal_for(self.p, T, "asker.public"), _Q)


class TestStore(Base):
    def test_persist_validate_and_clear(self):
        ud.set(T, "asker.public", {"persona": "executive", "depth": "headline", "language": "fr"})
        got = ud.get(T, "asker.public")
        self.assertEqual(got["persona"], "executive")
        self.assertEqual(got["depth"], "headline")
        self.assertEqual(got["language"], "fr")
        # invalid values are dropped, not stored
        ud.set(T, "asker.public", {"persona": "wizard", "depth": "epic"})
        got = ud.get(T, "asker.public")
        self.assertNotIn("persona", got)
        self.assertNotIn("depth", got)
        # per user: a different subject is unaffected
        self.assertEqual(ud.get(T, "someone.else"), {})

    def test_apply_to_is_identity_without_defaults(self):
        prin = demo.principal_for(self.p, T, "asker.public")
        self.assertIs(ud.apply_to(prin, T, "asker.public"), prin)


class TestReshape(Base):
    def test_persona_view_override(self):
        base = self._ask()
        self.assertEqual(base.role_view["persona"], "general")  # no designation → general
        ud.set(T, "asker.public", {"persona": "executive"})
        a = self._ask()
        self.assertEqual(a.role_view["persona"], "executive")
        # same grounded evidence, different lens
        self.assertEqual(a.kind, AnswerKind.ANSWER)
        self.assertTrue(a.citations)

    def test_depth_language_and_explain_auto(self):
        ud.set(T, "asker.public", {"depth": "headline", "language": "fr", "explain_auto": True})
        eff = ud.apply_to(demo.principal_for(self.p, T, "asker.public"), T, "asker.public")
        self.assertEqual(self.svc._depth_cap(eff), 1)  # headline caps at one sentence
        a = self._ask()
        self.assertEqual(a.lang, "fr")
        # rendered in the preferred language (cited to the English source of truth)
        self.assertIn("cited to the English source", a.answer_text)
        self.assertTrue(a.explain["auto"])  # Explain auto-expands
        self.assertEqual(a.result, a.answer_text)

    def test_depth_shortens_the_answer(self):
        full = self._ask()  # general → full (up to 3 sentences)
        ud.set(T, "asker.public", {"depth": "headline"})
        head = self._ask()
        self.assertLessEqual(head.answer_text.count("["), full.answer_text.count("[") + 0)
        self.assertLessEqual(head.answer_text.count("["), 1)


if __name__ == "__main__":
    unittest.main()
