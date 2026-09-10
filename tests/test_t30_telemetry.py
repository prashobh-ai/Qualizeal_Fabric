"""T30 — telemetry fields for the answering features shipped since the spine.

The telemetry Explorer (T29) sliced role × level × model × language × complexity
× kind. T30 adds the dimensions the later work introduced as first-class
telemetry, stamped on the answer span and surfaced in ``telemetry.events()`` and
``telemetry.analytics()``:

- **persona** and **designation** (T27) — who asked, and the profile it mapped to;
- **scope** — the access tier (public / restricted);
- **context** — whether a follow-up was resolved from the conversation (T26).
"""

from __future__ import annotations

import os
import unittest

os.environ.setdefault("KF_MODEL_MODE", "mock")

from knowledge_fabric.answer.service import AnswerService
from knowledge_fabric.ingestion.intake import IngestWorker, Intake
from knowledge_fabric.tenants import demo
from tests.util import T, seeded

_QMENTIS = "# QMentisAI\n\nQMentisAI is a test platform. QMentisAI pricing is usage-based.\n"


class TestTelemetryFields(unittest.TestCase):
    def setUp(self):
        self.p = seeded([T])
        intake, worker = Intake(self.p), IngestWorker(self.p, None)
        worker.intake = intake
        intake.submit(
            intake.canonical(
                T,
                "internal",
                "internal://q/qm.md",
                "QMentisAI",
                _QMENTIS.encode(),
                mime="text/markdown",
                acl=["public"],
            )
        )
        worker.drain()
        self.svc = AnswerService(self.p)

    def _events(self):
        return self.p.telemetry.events(T)

    def test_events_carry_the_new_dimensions(self):
        self.svc.ask(demo.principal_for(self.p, T, "developer"), "what is QMentisAI")
        e = self._events()[-1]
        for dim in ("persona", "designation", "scope", "context"):
            self.assertIn(dim, e)
        self.assertEqual(e["persona"], "developer")
        self.assertEqual(e["designation"], "Developer")
        self.assertEqual(e["scope"], "public")
        self.assertEqual(e["context"], "direct")

    def test_persona_and_designation_vary_by_asker(self):
        self.svc.ask(demo.principal_for(self.p, T, "cto"), "what is QMentisAI")
        self.svc.ask(demo.principal_for(self.p, T, "tester"), "what is QMentisAI")
        rows = self._events()
        personas = {r["persona"] for r in rows}
        self.assertIn("executive", personas)
        self.assertIn("quality", personas)

    def test_scope_reflects_access_tier(self):
        self.svc.ask(demo.principal_for(self.p, T, "asker.restricted"), "what is QMentisAI")
        self.assertEqual(self._events()[-1]["scope"], "restricted")

    def test_context_resolved_is_recorded(self):
        # a two-turn follow-up whose pronoun resolves to the subject
        ctx = {"turns": [{"question": "what is QMentisAI"}]}
        a = self.svc.ask(
            demo.principal_for(self.p, T, "developer"), "what about its pricing", context=ctx
        )
        self.assertTrue(a.understood_as)  # T26 resolved it
        self.assertEqual(self._events()[-1]["context"], "resolved")

    def test_plain_ask_is_direct(self):
        self.svc.ask(demo.principal_for(self.p, T, "developer"), "what is QMentisAI")
        self.assertEqual(self._events()[-1]["context"], "direct")

    def test_analytics_rolls_up_persona_and_context(self):
        self.svc.ask(demo.principal_for(self.p, T, "developer"), "what is QMentisAI")
        self.svc.ask(
            demo.principal_for(self.p, T, "cto"),
            "what about its pricing",
            context={"turns": [{"question": "what is QMentisAI"}]},
        )
        a = self.p.telemetry.analytics(T, window="all")
        self.assertIn("answers_by_persona", a)
        self.assertIn("context_resolution_rate", a)
        self.assertGreater(a["context_resolution_rate"], 0.0)
        self.assertIn("developer", a["answers_by_persona"])

    def test_no_designation_is_general(self):
        self.svc.ask(demo.principal_for(self.p, T, "asker.public"), "what is QMentisAI")
        e = self._events()[-1]
        self.assertEqual(e["persona"], "general")
        self.assertEqual(e["designation"], "—")


if __name__ == "__main__":
    unittest.main()
