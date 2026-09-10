"""T32 — static parity: the showcase answers like the server.

`scripts/parity_check.py` runs the ACTUAL shipped `engine.js` over a
production-shaped snapshot (under a Node browser shim) and asserts it agrees
with the Python `AnswerService` on every reader-visible classification —
kind, the coreference rewrite (T26), the persona lens (T27), citation/discovery/
code shape. This test runs that check end-to-end when Node is available (the CI
`mcp` job has it; it skips cleanly elsewhere), and unit-tests the comparison
logic without Node.
"""

from __future__ import annotations

import os
import shutil
import unittest

os.environ.setdefault("KF_MODEL_MODE", "off")

from scripts import parity_check


class TestParityCompare(unittest.TestCase):
    """The comparison contract — no Node needed."""

    def test_norm_matches_engine_key(self):
        self.assertEqual(
            parity_check._norm("What about QMentisAI pricing?"), "what about qmentisai pricing"
        )

    def test_identical_answers_agree(self):
        srv = {
            "kind": "answer",
            "understood_as": None,
            "role_view": {
                "lens": "builder",
                "persona": "developer",
                "depth": "full",
                "emphasis": "code",
            },
            "citations": [{}],
            "why": {"level_name": "lookup"},
            "answer_text": "x [1]",
        }
        self.assertEqual(
            parity_check._cmp(
                "c", srv, dict(srv, has_code=False, level_name="lookup", citations=1)
            ),
            [],
        )

    def test_lens_divergence_is_caught(self):
        srv = {
            "kind": "answer",
            "understood_as": None,
            "role_view": {
                "lens": "builder",
                "persona": "developer",
                "depth": "full",
                "emphasis": "code",
            },
            "citations": [{}],
            "why": {},
            "answer_text": "",
        }
        eng = {
            "kind": "answer",
            "understood_as": None,
            "role_view": {
                "lens": "executive",
                "persona": "executive",
                "depth": "headline",
                "emphasis": "authority",
            },
            "citations": 1,
            "has_code": False,
            "level_name": None,
        }
        diffs = parity_check._cmp("c", srv, eng)
        self.assertTrue(any("role_view.lens" in d for d in diffs))

    def test_coreference_divergence_is_caught(self):
        srv = {
            "kind": "answer",
            "understood_as": "what about QMentisAI pricing",
            "role_view": {},
            "citations": [],
            "why": {},
            "answer_text": "",
        }
        eng = {
            "kind": "answer",
            "understood_as": None,
            "role_view": {},
            "citations": 0,
            "has_code": False,
            "level_name": None,
        }
        diffs = parity_check._cmp("c", srv, eng)
        self.assertTrue(any("understood_as" in d for d in diffs))


@unittest.skipIf(shutil.which("node") is None, "node not available")
class TestStaticParityEndToEnd(unittest.TestCase):
    def test_engine_agrees_with_server(self):
        result = parity_check.run_parity()
        self.assertTrue(result["passed"], parity_check.format_report(result))
        self.assertEqual(result["passing"], result["total"])
        self.assertGreaterEqual(result["total"], 10)


if __name__ == "__main__":
    unittest.main()
