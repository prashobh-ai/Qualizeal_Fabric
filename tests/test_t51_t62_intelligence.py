"""T60 / T62 — the knowledge-intelligence MCP tools and the capstone demo.

The individual builders (galaxy, graph insights, curation, timing) are covered
by their own suites; this file covers the three new *governed MCP tools* (their
contract and honesty) and runs the `demo_intelligence` script as a smoke gate.
"""

from __future__ import annotations

import os
import unittest

os.environ.setdefault("KF_MODEL_MODE", "extractive")

from knowledge_fabric.mcp import server as mcp  # noqa: E402
from tests.util import T, seeded  # noqa: E402


class TestIntelligenceTools(unittest.TestCase):
    def setUp(self):
        self.p = seeded([T], model_mode="extractive")

    def test_provider_status_is_honest(self):
        out = mcp.tool_provider_status(self.p, T)["result"]
        # extractive floor: no live model, so it must NOT claim Claude.
        self.assertEqual(out["provider"], "Extractive")
        self.assertFalse(out["available"])
        self.assertTrue(out["fallback"])
        self.assertEqual(set(out), {"provider", "model", "available", "fallback"})

    def test_fabric_communities_shape(self):
        out = mcp.tool_fabric_communities(self.p, T)
        self.assertIn("communities", out["result"])
        self.assertEqual(out["citations"], [])
        for c in out["result"]["communities"]:
            self.assertIn("size", c)
            self.assertGreaterEqual(c["cohesion"], 0.0)
            self.assertLessEqual(c["cohesion"], 1.0)

    def test_knowledge_gaps_shape(self):
        out = mcp.tool_knowledge_gaps(self.p, T)["result"]
        self.assertIn("gaps", out)
        self.assertIn("surprising", out)
        self.assertIsInstance(out["gaps"], list)
        self.assertIsInstance(out["surprising"], list)

    def test_all_three_tools_registered(self):
        for name in ("provider_status", "fabric_communities", "knowledge_gaps"):
            self.assertIn(name, mcp.TOOL_NAMES)


class TestIntelligenceDemo(unittest.TestCase):
    def test_demo_runs_clean(self):
        import importlib.util
        import pathlib

        root = pathlib.Path(__file__).resolve().parents[1]
        spec = importlib.util.spec_from_file_location(
            "demo_intelligence", root / "scripts" / "demo_intelligence.py"
        )
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        # The demo's own smoke gate: 0 only if the review item was held and then
        # answerable after acceptance (T53), on a real graph (T57).
        self.assertEqual(mod.main(), 0)


if __name__ == "__main__":
    unittest.main()
