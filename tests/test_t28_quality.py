"""T28 — answer-quality golden suite + CI gate.

The suite pins the answering behaviours the product promises (grounded answers,
resolvable citations, honest decline, code answers, discovery, two-turn context,
persona conditioning) as hard must-holds over the model-free path, and the gate
fails the build when any regresses. These tests prove the suite passes on a
healthy fabric AND that the gate actually blocks a degraded one.
"""

from __future__ import annotations

import os
import unittest

os.environ.setdefault("KF_MODEL_MODE", "off")

from knowledge_fabric.app import Platform
from knowledge_fabric.evaluation import quality


def _fresh_platform():
    return Platform(db_path=":memory:", blob_root="./data/test-blobs")


class TestQualitySuite(unittest.TestCase):
    def test_suite_passes_on_a_healthy_fabric(self):
        r = quality.run_quality_suite(_fresh_platform())
        self.assertTrue(r.passed, quality.format_report(r))
        self.assertEqual(r.passing, r.total)
        self.assertEqual(r.score, 1.0)

    def test_every_dimension_is_covered(self):
        dims = {c.dimension for c in quality.GOLDEN}
        # the answering surface areas T24–T27 shipped
        for expected in (
            "answering",
            "grounding",
            "citations",
            "decline",
            "code",
            "discovery",
            "context",
            "persona",
        ):
            self.assertIn(expected, dims, expected)

    def test_report_renders(self):
        r = quality.run_quality_suite(_fresh_platform())
        text = quality.format_report(r)
        self.assertIn("golden cases passed", text)
        self.assertIn("PASS", text)

    def test_gate_blocks_a_regression(self):
        # Prove the gate actually gates: corrupt the citation coordinates on the
        # eval fabric (the same hook the promotion gate uses), then run the suite
        # WITHOUT rebuilding. The "citations resolve to a place" case must fail,
        # so the gate reports FAIL.
        from knowledge_fabric.evaluation import gate

        p = _fresh_platform()
        quality.build_eval_fabric(p, quality.EVAL_TENANT)
        broke = gate.corrupt_citation_coordinates(p, quality.EVAL_TENANT)
        self.assertGreater(broke, 0)
        r = quality.run_quality_suite(p, build=False)
        self.assertFalse(r.passed, quality.format_report(r))
        self.assertTrue(any("citation" in f.lower() for f in r.failures), r.failures)

    def test_a_crashing_case_counts_as_a_failure(self):
        # A check that raises must be recorded as a failure, never silently pass.
        def _boom(ctx):
            raise RuntimeError("boom")

        original = list(quality.GOLDEN)
        try:
            quality.GOLDEN.append(quality.Case("synthetic", "always crashes", _boom))
            r = quality.run_quality_suite(_fresh_platform())
            self.assertFalse(r.passed)
            self.assertTrue(any("always crashes" in f for f in r.failures))
        finally:
            quality.GOLDEN[:] = original


class TestQualityGateCLI(unittest.TestCase):
    def test_cli_exit_zero_when_healthy(self):
        import scripts.quality_gate as gate

        self.assertEqual(gate.main([]), 0)
        self.assertEqual(gate.main(["--json"]), 0)


if __name__ == "__main__":
    unittest.main()
