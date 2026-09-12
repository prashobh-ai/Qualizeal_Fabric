"""T81 / T84 / T85 — the answer-first contract.

The direct ``result`` is composed and returned immediately with a governance
line, costing no model tokens on the deployed extractive floor; the narrative
``explanation`` is a separate, ledgered step produced only when the reader asks
(``AnswerService.explain``); and the same question cites the same documents for
two personas while the persona contract (the ``Explain`` offers, the shape)
differs.
"""

from __future__ import annotations

import os
import tempfile
import unittest

import pytest

_TMP = tempfile.mkdtemp(prefix="kf-answerfirst-")
os.environ["KF_DATA_ROOT"] = _TMP

from knowledge_fabric.answer import personas  # noqa: E402
from knowledge_fabric.answer.service import AnswerService  # noqa: E402
from knowledge_fabric.contracts.types import AnswerKind  # noqa: E402
from knowledge_fabric.telemetry import api_ledger  # noqa: E402
from knowledge_fabric.tenants import demo  # noqa: E402
from tests.util import T, seeded  # noqa: E402


@pytest.fixture(autouse=True)
def _pin_data_root():
    prev = os.environ.get("KF_DATA_ROOT")
    os.environ["KF_DATA_ROOT"] = _TMP
    try:
        yield
    finally:
        if prev is None:
            os.environ.pop("KF_DATA_ROOT", None)
        else:
            os.environ["KF_DATA_ROOT"] = prev


class AnswerFirstBase(unittest.TestCase):
    def setUp(self):
        self.p = seeded([T], model_mode="extractive")
        self.svc = AnswerService(self.p)
        self.asker = demo.principal_for(self.p, T, "asker.public")
        self.title = self.p.documents.list(T)[0]["title"]


class TestAnswerFirst(AnswerFirstBase):
    def test_result_is_direct_and_costs_no_model_tokens(self):
        a = self.svc.ask(self.asker, f"What is {self.title}?")
        self.assertEqual(a.kind, AnswerKind.ANSWER)
        # the direct answer is rendered immediately, no model call on the floor
        self.assertTrue(a.result)
        self.assertEqual(a.result, a.answer_text)
        self.assertEqual(a.tokens_out, 0)
        self.assertEqual(a.cost, 0.0)
        # and it carries a governance line with a freshness stamp (T85)
        self.assertIsNotNone(a.governance)
        self.assertIn("as of", a.governance["freshness"])
        self.assertIn("stale", a.governance)
        # the Explain affordance is offered but not yet expanded
        self.assertTrue(a.explain["available"])
        self.assertTrue(a.explain["offers"])
        self.assertIsNone(a.explanation)
        # the whole contract survives serialisation
        d = a.to_dict()
        for key in ("result", "explanation", "explain", "governance"):
            self.assertIn(key, d)

    def test_explain_is_a_separate_ledgered_step(self):
        a = self.svc.ask(self.asker, f"What is {self.title}?")
        before = [r for r in api_ledger.rows(1) if r.get("purpose") == "explain"]

        out = self.svc.explain(self.asker, a.trajectory_id)

        self.assertTrue(out["explanation"])
        self.assertNotIn("error", out)
        after = [r for r in api_ledger.rows(1) if r.get("purpose") == "explain"]
        self.assertEqual(len(after), len(before) + 1)
        # the explain step recorded its own signal on the trace
        names = {s.get("name") for s in self.p.telemetry.trace(a.trajectory_id)}
        self.assertIn("kf.explain.requested", names)

    def test_explain_unknown_trace_is_safe(self):
        out = self.svc.explain(self.asker, "traj_does_not_exist")
        self.assertEqual(out["explanation"], "")
        self.assertIn("error", out)


class TestPersonaContract(AnswerFirstBase):
    def test_same_citations_different_contract(self):
        dev = demo.principal_for(self.p, T, "asker.public")
        dev.designation = "Senior Developer"
        exe = demo.principal_for(self.p, T, "asker.public")
        exe.designation = "CTO"

        a_dev = self.svc.ask(dev, f"What is {self.title}?")
        a_exe = self.svc.ask(exe, f"What is {self.title}?")

        cites_dev = {c.document_id for c in a_dev.citations}
        cites_exe = {c.document_id for c in a_exe.citations}
        self.assertTrue(cites_dev and cites_exe)
        # same core evidence: they share the lead document and overlap. Persona
        # emphasis (T27) may add one emphasised citation, never swap the base.
        self.assertEqual(a_dev.citations[0].document_id, a_exe.citations[0].document_id)
        self.assertTrue(cites_dev & cites_exe)
        # different persona contract (the Explain offers differ)
        self.assertEqual(a_dev.explain["offers"], personas.CONTRACT["developer"]["offers"])
        self.assertEqual(a_exe.explain["offers"], personas.CONTRACT["executive"]["offers"])
        self.assertNotEqual(a_dev.explain["offers"], a_exe.explain["offers"])


if __name__ == "__main__":
    unittest.main()
