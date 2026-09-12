"""T83 — the audience coverage matrix and its gate.

Runs the coverage evaluation over the self-contained coverage corpus and asserts
the matrix is complete and green: every ✓ cell of a held data type answers,
grounded and cited, through the governed path — and the gate blocks any coral
held cell. This test IS the CI gate the article asks for.
"""

from __future__ import annotations

import os
import tempfile
import unittest

import pytest

from eval import coverage as cov  # noqa: E402
from knowledge_fabric.answer.service import AnswerService  # noqa: E402
from knowledge_fabric.app import Platform  # noqa: E402
from knowledge_fabric.tenants import demo  # noqa: E402


@pytest.fixture(autouse=True)
def _pin_data_root():
    prev = os.environ.get("KF_DATA_ROOT")
    os.environ["KF_DATA_ROOT"] = tempfile.mkdtemp(prefix="kf-coverage-test-")
    try:
        yield
    finally:
        if prev is None:
            os.environ.pop("KF_DATA_ROOT", None)
        else:
            os.environ["KF_DATA_ROOT"] = prev


class TestCoverageMatrix(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        os.environ["KF_MODEL_MODE"] = "extractive"
        cls.p = Platform(db_path=":memory:", blob_root=tempfile.mkdtemp(prefix="kf-cov-blobs-"))
        demo.seed(cls.p)
        cls.held = cov.build_fabric(cls.p, cov.COVERAGE_TENANT)
        cls.svc = AnswerService(cls.p)
        cls.rep = cov.evaluate(cls.p, cls.svc, cov.COVERAGE_TENANT, cls.held)

    def test_gate_no_coral_held_cell(self):
        # the article's gate: no coral cell for a held data type
        self.assertEqual(self.rep["coral_held"], [], f"coral held cells: {self.rep['coral_held']}")
        self.assertTrue(self.rep["passed"])

    def test_every_held_cell_is_green(self):
        # each ✓ cell of a held data type answers, grounded and cited, for its persona
        for m in self.rep["matrix"]:
            if not m["held"]:
                continue
            for aud, c in m["cells"].items():
                self.assertEqual(
                    c["status"], "green", f"{m['data_type']}/{aud} is {c['status']}, not green"
                )

    def test_matrix_spans_every_data_type_and_persona(self):
        dts = {m["data_type"] for m in self.rep["matrix"]}
        self.assertEqual(dts, set(cov.DATA_TYPES))
        # every persona in the matrix is a known coverage persona
        for m in self.rep["matrix"]:
            for aud in m["cells"]:
                self.assertIn(aud, cov.PERSONAS)

    def test_a_broken_cell_would_fail_the_gate(self):
        # feed a question that cannot ground (no such document) for a held cell,
        # and prove the gate turns coral — the regression guard actually guards.
        broken = [
            {
                "id": "cov.documents.broken",
                "data_type": "documents",
                "personas": ["business"],
                "question": "what does the quarterly zephyr saturn revenue memo conclude",
            }
        ]
        rep = cov.evaluate(self.p, self.svc, cov.COVERAGE_TENANT, self.held, rows=broken)
        docs = next(m for m in rep["matrix"] if m["data_type"] == "documents")
        self.assertEqual(docs["cells"]["business"]["status"], "coral")
        self.assertFalse(rep["passed"])


if __name__ == "__main__":
    unittest.main()
