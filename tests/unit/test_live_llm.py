"""T166 — the live, complexity-routed Claude call is wired into the engine and gated.

Build-free static checks (the real live answer can only be exercised in a browser
with a funded key, injected at deploy):

* the shipped ``engine.js`` carries the Anthropic endpoint, the browser-access
  header, the three allowed model ids, the ``pickModel`` router and the inline
  prices — and no answer path still emits ``"demo model"``;
* ``prices.json`` prices all three routed models;
* ``verify_showcase`` flags a built engine that is missing the wiring (so a
  regression that drops the live call fails the deploy).
"""

from __future__ import annotations

import os
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
ENGINE = os.path.join(ROOT, "scripts", "showcase", "engine.js")
PRICES = os.path.join(ROOT, "knowledge_fabric", "telemetry", "prices.json")
MODELS = ("claude-haiku-4-5", "claude-sonnet-4-6", "claude-sonnet-5")


class TestEngineWiring(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        with open(ENGINE, encoding="utf-8") as fh:
            cls.eng = fh.read()

    def test_anthropic_endpoint_and_header(self):
        self.assertIn("api.anthropic.com/v1/messages", self.eng)
        self.assertIn("anthropic-dangerous-direct-browser-access", self.eng)

    def test_all_three_models_and_router(self):
        for mid in MODELS:
            self.assertIn(mid, self.eng, mid)
        self.assertIn("function pickModel(", self.eng)
        self.assertIn("function llmCompose(", self.eng)

    def test_key_is_a_placeholder_not_a_real_key(self):
        # The committed source must never carry a real key.
        self.assertIn("__ANTHROPIC_BROWSER_KEY__", self.eng)
        self.assertNotIn("sk-ant-", self.eng)

    def test_no_demo_model_label_remains(self):
        self.assertNotIn("demo model", self.eng)


class TestPrices(unittest.TestCase):
    def test_prices_cover_all_routed_models(self):
        import json

        prices = json.load(open(PRICES, encoding="utf-8"))
        for mid in MODELS:
            self.assertIn(mid, prices, mid)
            self.assertGreater(prices[mid]["input"], 0)
            self.assertGreater(prices[mid]["output"], 0)


class TestVerifyGate(unittest.TestCase):
    """The deploy gate bites when the live wiring is dropped from the built engine."""

    def _verify_engine(self, body: str) -> list[str]:
        from scripts import verify_showcase

        with tempfile.TemporaryDirectory() as d:
            with open(os.path.join(d, "engine.js"), "w", encoding="utf-8") as fh:
                fh.write(body)
            return [e for e in verify_showcase.verify(d) if "(T166)" in e]

    def test_wired_engine_passes_the_t166_gate(self):
        good = (
            "fetch('https://api.anthropic.com/v1/messages');\n"
            "function pickModel(l){return 'claude-haiku-4-5'"
            "||'claude-sonnet-4-6'||'claude-sonnet-5';}\n"
        )
        self.assertEqual(self._verify_engine(good), [])

    def test_missing_endpoint_fails(self):
        bad = "function pickModel(){} claude-haiku-4-5 claude-sonnet-4-6 claude-sonnet-5"
        errs = self._verify_engine(bad)
        self.assertTrue(any("endpoint" in e for e in errs), errs)

    def test_demo_model_label_fails(self):
        bad = (
            "https://api.anthropic.com/v1/messages function pickModel(){}"
            " claude-haiku-4-5 claude-sonnet-4-6 claude-sonnet-5 model_name='demo model'"
        )
        errs = self._verify_engine(bad)
        self.assertTrue(any("demo model" in e for e in errs), errs)


if __name__ == "__main__":
    unittest.main()
