"""T34 — the answering-intelligence demo (the T24–T33 capstone).

`scripts/demo_answering.py` narrates the whole answering track end to end on the
model-free floor. This test runs it in-process and asserts the story actually
holds — each beat is exercised against the real `AnswerService`, not just that
the script prints without crashing — and that the script's own hard gates
(quality suite + load SLOs) pass so it doubles as a smoke check.
"""

from __future__ import annotations

import io
import os
import unittest
from contextlib import redirect_stdout

os.environ.setdefault("KF_MODEL_MODE", "off")

from knowledge_fabric.contracts.types import AnswerKind
from scripts import demo_answering as demo


class TestDemoAnsweringBeats(unittest.TestCase):
    """Each narrated capability, checked against the real service on one fabric."""

    @classmethod
    def setUpClass(cls):
        from knowledge_fabric.answer.service import AnswerService

        cls.p = demo._build_fabric()
        cls.svc = AnswerService(cls.p)
        cls.asker = demo.demo.principal_for(cls.p, demo.TENANT, "asker.public")

    def test_floor_answers_and_declines(self):
        self.assertEqual(self.svc.ask(self.asker, "what is QMentisAI").kind, AnswerKind.ANSWER)
        self.assertNotEqual(
            self.svc.ask(self.asker, "what is the capital of France").kind, AnswerKind.ANSWER
        )

    def test_discovery_lists_assets(self):
        a = self.svc.ask(self.asker, "has anyone made sso and auth code which I can reuse")
        assets = (a.why or {}).get("discovery", [])
        self.assertGreaterEqual(len(assets), 2)
        self.assertTrue(any(x["kind"] == "code" for x in assets))

    def test_code_answer_carries_a_block(self):
        a = self.svc.ask(self.asker, "how does retry_call work")
        self.assertIn("```", a.answer_text or "")
        self.assertTrue(a.citations)

    def test_two_turn_coreference_resolves(self):
        a = self.svc.ask(
            self.asker,
            "what about its pricing",
            context={"turns": [{"question": "what is QMentisAI"}]},
        )
        self.assertIsNotNone(a.understood_as)
        self.assertIn("qmentisai", (a.understood_as or "").lower())

    def test_role_conditioning_differs_by_designation(self):
        views = {}
        for subject in ("developer", "tester", "cto"):
            prin = demo.demo.principal_for(self.p, demo.TENANT, subject)
            rv = self.svc.ask(prin, "what is QMentisAI").role_view or {}
            views[subject] = (rv.get("persona"), rv.get("depth"), rv.get("emphasis"))
        # distinct designations must produce distinct lenses (not one flat answer)
        self.assertEqual(len(set(views.values())), 3, views)

    def test_mcp_agent_uses_same_governed_path(self):
        from knowledge_fabric.mcp.server import _agent_principal

        agent = _agent_principal(self.p, demo.TENANT)
        self.assertTrue(agent.agent)
        a = self.svc.ask(agent, "what is QMentisAI")
        self.assertEqual(a.kind, AnswerKind.ANSWER)
        rows = self.p.audit.for_trace(demo.TENANT, a.trajectory_id)
        self.assertEqual(rows[0]["is_agent"], 1)  # audited as an agent, not a back door


class TestDemoAnsweringRuns(unittest.TestCase):
    """The whole script runs to a clean exit — its hard gates (quality + load) pass."""

    def test_main_exits_zero(self):
        buf = io.StringIO()
        with redirect_stdout(buf):
            rc = demo.main()
        out = buf.getvalue()
        self.assertEqual(rc, 0, out[-2000:])
        # the narrative reached its final beat
        self.assertIn("DONE — T24–T33", out)
        self.assertIn("SLOs HELD", out)


if __name__ == "__main__":
    unittest.main()
