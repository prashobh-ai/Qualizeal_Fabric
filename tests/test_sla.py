"""T86 — the business SLA & path panel.

Drives a spread of answers (fast known questions and a reasoning question) plus
an explain request, then asserts the Service-levels report computes the headline
SLA line, the fast-vs-agent split, per-persona and per-data-type time-to-answer,
the explain-request rate, and cost per answer.
"""

from __future__ import annotations

import os
import unittest

from knowledge_fabric.answer.service import AnswerService
from knowledge_fabric.telemetry import sla
from knowledge_fabric.tenants import demo
from tests.util import T, seeded


class TestServiceLevels(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        os.environ["KF_MODEL_MODE"] = "extractive"
        cls.p = seeded([T], model_mode="extractive")
        cls.svc = AnswerService(cls.p)
        dev = demo.principal_for(cls.p, T, "asker.public")
        dev.designation = "Senior Developer"
        biz = demo.principal_for(cls.p, T, "asker.public")
        biz.designation = "Delivery Manager"
        # a spread of fast, grounded answers across two personas
        fast_qs = [
            "what must a release achieve before promotion?",
            "what is the acceptance criteria for coverage?",
            "which requirement has a traceability gap?",
        ]
        for q in fast_qs:
            cls.svc.ask(dev, q)
            cls.svc.ask(biz, q)
        # a reasoning (agent) question, and an explain request on a fast trace
        cls.svc.ask(dev, "compare the test strategy and the defect policy")
        a = cls.svc.ask(dev, "what must a release achieve before promotion?")
        cls.svc.explain(dev, a.trajectory_id)
        cls.rep = sla.service_levels(cls.p, T)

    def test_headline_has_sla_and_reading_line(self):
        h = self.rep["headline"]
        self.assertIn("target 1–3 s", h["sla_line"])
        self.assertIn("fast by design", h["reading_line"])
        self.assertGreater(self.rep["n_answers"], 0)
        # shares are a partition
        self.assertAlmostEqual(h["fast_share"] + h["agent_share"], 1.0, places=3)

    def test_fast_and_agent_split(self):
        # the reasoning question is classified agent; the known questions fast
        self.assertGreater(self.rep["headline"]["fast_share"], 0.0)
        self.assertGreater(self.rep["headline"]["agent_share"], 0.0)

    def test_explain_rate_recorded(self):
        # one explain over N answers → a positive, bounded rate
        self.assertGreater(self.rep["headline"]["explain_rate"], 0.0)
        self.assertLessEqual(self.rep["headline"]["explain_rate"], 1.0)

    def test_per_persona_and_per_data_type(self):
        personas = {r["persona"] for r in self.rep["by_persona"]}
        self.assertIn("developer", personas)
        self.assertIn("delivery", personas)
        for r in self.rep["by_persona"]:
            self.assertGreaterEqual(r["p95_ms"], r["p50_ms"])
            self.assertIn("cost_per_answer", r)
        self.assertTrue(self.rep["by_data_type"])
        for r in self.rep["by_data_type"]:
            self.assertIn("data_type", r)
            self.assertGreaterEqual(r["n"], 1)


if __name__ == "__main__":
    unittest.main()
