"""T102 — the v2 capstone demo runs end to end, keyless, hitting every step."""

from __future__ import annotations

import os
import tempfile

import pytest

os.environ.setdefault("KF_MODEL_MODE", "extractive")


@pytest.fixture(autouse=True)
def _pin_data_root():
    prev = os.environ.get("KF_DATA_ROOT")
    os.environ["KF_DATA_ROOT"] = tempfile.mkdtemp(prefix="kf-demo-v2-test-")
    try:
        yield
    finally:
        if prev is None:
            os.environ.pop("KF_DATA_ROOT", None)
        else:
            os.environ["KF_DATA_ROOT"] = prev


def test_demo_v2_narrative_end_to_end():
    from scripts import demo_v2

    summary = demo_v2.run(out=lambda *a, **k: None)
    # T91/T92 — a cited keyless answer
    assert summary["keyless_answer"]["cited"] is True
    # T93 — the citation expands to its paragraph neighbours
    assert summary["citation_expand"] is True
    # T94 — a fenced code block
    assert summary["code_snippet"] is True
    # T97 — the exact in-progress count from the board column, with freshness
    assert "4 issues in the In Progress column" in summary["jira_in_progress"]
    assert "as of" in summary["jira_in_progress"]
    # T99 — the two-source verification agrees
    assert summary["cross_source_verdict"] == "agree"
    # T96 — ROI + reconciled observability
    assert summary["roi"]["hours_saved"] > 0
    assert summary["observability_ok"] is True
    # T100 — the answer's concepts are lit for the galaxy to flash
    assert summary["galaxy_activated"] >= 1
