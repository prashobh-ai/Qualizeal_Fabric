"""T52 — the open-source LLM fallback, shown honestly.

The fallback is always available and never invents facts. It composes a fluent
grounded answer from the evidence the caller already selected, records a $0
ledger row, and its provider badge names exactly what ran. Heavy deps are
absent here, so the deterministic extractive-NLG path is exercised. The ledger
writes under a temp ``KF_DATA_ROOT``, kept out of the repo's ``data/``.
"""

from __future__ import annotations

import os
import tempfile
import unittest
from unittest import mock

import pytest

_TMP = tempfile.mkdtemp(prefix="kf-oss-")
os.environ["KF_DATA_ROOT"] = _TMP

from knowledge_fabric.adapters import model, oss_model  # noqa: E402
from knowledge_fabric.telemetry import api_ledger  # noqa: E402


@pytest.fixture(autouse=True)
def _pin_data_root():
    """Own KF_DATA_ROOT at RUNTIME for every test here, not just at import, so
    this suite's $0 ledger rows land in its own tempdir and never leak into
    another module's ledger (whichever module imports last otherwise wins the
    process env — see the mirror fixture in test_t35_t36_provider_ledger)."""
    prev = os.environ.get("KF_DATA_ROOT")
    os.environ["KF_DATA_ROOT"] = _TMP
    try:
        yield
    finally:
        if prev is None:
            os.environ.pop("KF_DATA_ROOT", None)
        else:
            os.environ["KF_DATA_ROOT"] = prev


_EVIDENCE = (
    "The service provides automated testing across many repositories [1]. "
    "It also runs quality checks on every pull request [2]."
)


def _messages(user: str) -> list[dict]:
    return [
        {"role": "system", "content": "Rephrase the cited evidence faithfully; add nothing."},
        {"role": "user", "content": user},
    ]


class _EnvMode(unittest.TestCase):
    KEYS = ("KF_MODEL_MODE", "ANTHROPIC_API_KEY")

    def setUp(self):
        self._snap = {k: os.environ.get(k) for k in self.KEYS}
        for k in self.KEYS:
            os.environ.pop(k, None)

    def tearDown(self):
        for k, v in self._snap.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v


class TestOssMode(_EnvMode):
    def test_build_returns_oss_client(self):
        os.environ["KF_MODEL_MODE"] = "oss"
        client = model.build_model_client()
        self.assertIsInstance(client, oss_model.OSSModelClient)
        self.assertTrue(client.available())

    def test_complete_is_fluent_grounded_and_free(self):
        os.environ["KF_MODEL_MODE"] = "oss"
        client = model.build_model_client()
        out = client.complete(
            "test-fabric", "fast", _messages(_EVIDENCE), {"purpose": "answer_bake"}
        )
        self.assertTrue(out["text"].strip())
        self.assertTrue(out["model_name"].startswith("Open-source"))
        self.assertGreater(out["usage"]["in"], 0)
        self.assertGreater(out["usage"]["out"], 0)
        self.assertEqual(out["cost"], 0.0)
        # The composer only reuses evidence — the answer stays grounded.
        self.assertIn("testing", out["text"].lower())


class TestEntityGuard(unittest.TestCase):
    def test_complete_never_introduces_an_unseen_entity(self):
        client = oss_model.OSSModelClient()
        out = client.complete("test-fabric", "fast", _messages(_EVIDENCE), {})
        self.assertNotIn("Zzyzx", out["text"])

    def test_guard_drops_a_sentence_with_an_unseen_entity(self):
        evidence = "The tool ships quality checks today."
        hallucinated = [
            "The tool ships quality checks today.",
            "The tool was written in Zzyzx last week.",
        ]
        kept = oss_model.entity_guard(hallucinated, evidence)
        self.assertIn("The tool ships quality checks today.", kept)
        self.assertFalse(any("Zzyzx" in s for s in kept))


class TestAutoMode(_EnvMode):
    def test_auto_without_key_falls_through_to_oss(self):
        os.environ["KF_MODEL_MODE"] = "auto"
        client = model.build_model_client()
        self.assertIsInstance(client, oss_model.OSSModelClient)

    def test_auto_prefers_anthropic_when_available(self):
        os.environ["KF_MODEL_MODE"] = "auto"
        with mock.patch.object(model.AnthropicModelClient, "available", lambda self: True):
            client = model.build_model_client()
        self.assertIsInstance(client, model.AnthropicModelClient)


class TestLedger(unittest.TestCase):
    def test_messages_writes_a_zero_cost_open_source_row(self):
        client = oss_model.OSSModelClient()
        body = {
            "model": "oss",
            "max_tokens": 256,
            "messages": [{"role": "user", "content": _EVIDENCE}],
        }
        resp = client.messages(body, purpose="answer_bake")
        self.assertTrue(resp["content"][0]["text"].strip())
        self.assertTrue(resp["model"].startswith("Open-source"))
        rows = [r for r in api_ledger.rows(1) if str(r.get("model", "")).startswith("Open-source")]
        self.assertTrue(rows)
        self.assertTrue(all(r["cost_usd"] == 0 for r in rows))


if __name__ == "__main__":
    unittest.main()
