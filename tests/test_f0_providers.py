"""F0.3 → T35 — Anthropic-first provider selection and doctor model-id check.

T35 replaced every silent mock fallback: an explicitly-configured but
unreachable provider raises ``ProviderUnavailable`` (e.g. ``anthropic`` with no
key). T91 makes ``auto`` the default, so an unset mode with no key answers via
the open-source fallback rather than raising; the loud failure is now reserved
for an explicit provider mode. The Anthropic API is never actually called:
`urllib.request.urlopen` is patched so tests run without a network (or a key).
"""

from __future__ import annotations

import json
import os
import tempfile
import unittest
from unittest import mock

from knowledge_fabric.adapters import model

# The ledger (T36) appends under the data root; keep tests out of the repo's data/.
os.environ.setdefault("KF_DATA_ROOT", tempfile.mkdtemp(prefix="kf-ledger-"))


class TestBuildModelClient(unittest.TestCase):
    """`KF_MODEL_MODE` selects the provider; ``auto`` is the default (T91) —
    a verified key wins, else the open-source fallback answers keyless. An
    explicit ``anthropic`` still REQUIRES its key (no silent mock fallback, T35)."""

    def setUp(self):
        # Snapshot and clear the four env vars this test touches.
        self._snap = {
            k: os.environ.get(k)
            for k in (
                "KF_MODEL_MODE",
                "ANTHROPIC_API_KEY",
                "OPENAI_API_KEY",
                "KF_MODEL_BASE_URL",
                "KF_MODEL_API_KEY",
            )
        }
        for k in self._snap:
            os.environ.pop(k, None)

    def tearDown(self):
        for k, v in self._snap.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v

    def test_default_without_key_answers_open_source(self):
        # T91 — the default is now ``auto``: with no key it falls through to the
        # open-source fallback (a usable keyless client) instead of raising.
        from knowledge_fabric.adapters import oss_model

        client = model.build_model_client()
        self.assertIsInstance(client, oss_model.OSSModelClient)
        self.assertTrue(client.available())

    def test_default_is_anthropic_when_key_set(self):
        # ``auto`` still prefers a verified provider key.
        os.environ["ANTHROPIC_API_KEY"] = "sk-ant-test"
        client = model.build_model_client()
        self.assertIsInstance(client, model.AnthropicModelClient)
        self.assertTrue(client.available())

    def test_off_and_extractive_disable(self):
        for mode in ("off", "extractive"):
            os.environ["KF_MODEL_MODE"] = mode
            self.assertIsInstance(model.build_model_client(), model.DisabledModelClient)

    def test_mock_is_explicit_only(self):
        os.environ["KF_MODEL_MODE"] = "mock"
        self.assertIsInstance(model.build_model_client(), model.MockModelClient)

    def test_explicit_anthropic_without_key_raises(self):
        os.environ["KF_MODEL_MODE"] = "anthropic"
        with self.assertRaises(model.ProviderUnavailable):
            model.build_model_client()

    def test_unknown_or_unconfigured_provider_raises(self):
        for mode in ("openai", "hosted", "bedrock", "vllm", "nonsense"):
            os.environ["KF_MODEL_MODE"] = mode
            with self.assertRaises(model.ProviderUnavailable):
                model.build_model_client()

    def test_provider_order_recorded(self):
        self.assertEqual(model.PROVIDER_ORDER, ("anthropic", "openai", "bedrock", "vllm"))


class TestAnthropicModelClient(unittest.TestCase):
    def setUp(self):
        self._key = os.environ.pop("ANTHROPIC_API_KEY", None)
        os.environ["ANTHROPIC_API_KEY"] = "sk-ant-test"

    def tearDown(self):
        if self._key is None:
            os.environ.pop("ANTHROPIC_API_KEY", None)
        else:
            os.environ["ANTHROPIC_API_KEY"] = self._key

    def test_complete_maps_system_and_reads_usage(self):
        canned = {
            "id": "msg_1",
            "type": "message",
            "role": "assistant",
            "model": "claude-sonnet-4-6",
            "content": [{"type": "text", "text": "OK."}],
            "usage": {
                "input_tokens": 10,
                "output_tokens": 3,
                "cache_read_input_tokens": 0,
                "cache_creation_input_tokens": 0,
            },
        }
        captured = {}

        def fake_urlopen(req, timeout=None):
            captured["url"] = req.full_url
            captured["headers"] = dict(req.headers)
            captured["body"] = json.loads(req.data.decode())
            return _Response(canned)

        client = model.AnthropicModelClient()
        with mock.patch("urllib.request.urlopen", fake_urlopen):
            out = client.complete(
                "test-fabric",
                "escalation",
                [
                    {"role": "system", "content": "You are helpful."},
                    {"role": "user", "content": "hi"},
                ],
                {"max_tokens": 128},
            )
        # The system role is lifted out of `messages` into top-level `system`
        # as a cached block (T43 prompt layout: stable prefix under cache_control).
        self.assertEqual(captured["body"]["system"][0]["text"], "You are helpful.")
        self.assertEqual(captured["body"]["system"][0]["cache_control"], {"type": "ephemeral"})
        self.assertEqual(captured["body"]["messages"], [{"role": "user", "content": "hi"}])
        # Model selection follows KF_MODEL_LARGE for the escalation tier.
        self.assertEqual(
            captured["body"]["model"], os.environ.get("KF_MODEL_LARGE", "claude-sonnet-4-6")
        )
        # Response shape flows through to the platform's expected dict.
        self.assertEqual(out["text"], "OK.")
        self.assertEqual(out["usage"]["in"], 10)
        self.assertEqual(out["usage"]["out"], 3)
        self.assertGreater(out["cost"], 0.0)  # priced from prices.json (T36)
        self.assertEqual(out["model_name"], "claude-sonnet-4-6")


class _Response:
    """Minimal urlopen-return stand-in (carries the `request-id` header)."""

    def __init__(self, payload):
        self._data = json.dumps(payload).encode()
        self.headers = {"request-id": "req_test_0001"}

    def __enter__(self):
        return self

    def __exit__(self, *_a):
        return False

    def read(self):
        return self._data


class TestDoctorModelIdCheck(unittest.TestCase):
    def setUp(self):
        self._snap = {
            k: os.environ.get(k)
            for k in ("ANTHROPIC_API_KEY", "OPENAI_API_KEY", "KF_MODEL_SMALL", "KF_MODEL_LARGE")
        }

    def tearDown(self):
        for k, v in self._snap.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v

    def test_skipped_when_no_keys(self):
        os.environ.pop("ANTHROPIC_API_KEY", None)
        os.environ.pop("OPENAI_API_KEY", None)
        from scripts import doctor

        rep = doctor.check_model_ids()
        self.assertEqual(rep["providers"]["anthropic"]["status"], "skipped")
        self.assertEqual(rep["providers"]["openai"]["status"], "skipped")

    def test_ok_when_ids_valid(self):
        os.environ["ANTHROPIC_API_KEY"] = "sk-ant-test"
        os.environ["KF_MODEL_SMALL"] = "claude-haiku-4-5"
        os.environ["KF_MODEL_LARGE"] = "claude-sonnet-4-6"
        payload = {
            "data": [
                {"id": "claude-haiku-4-5"},
                {"id": "claude-sonnet-4-6"},
                {"id": "claude-sonnet-5"},
            ]
        }
        with mock.patch("urllib.request.urlopen", lambda *_a, **_kw: _Response(payload)):
            from scripts import doctor

            rep = doctor.check_model_ids()
        self.assertEqual(rep["providers"]["anthropic"]["status"], "ok")
        self.assertTrue(rep["providers"]["anthropic"]["small_valid"])
        self.assertTrue(rep["providers"]["anthropic"]["large_valid"])

    def test_id_outside_allowed_set_is_refused(self):
        # T35: nothing outside {sonnet-4-6, haiku-4-5} is ever configured.
        os.environ["ANTHROPIC_API_KEY"] = "sk-ant-test"
        os.environ["KF_MODEL_SMALL"] = "claude-was-retired"
        os.environ["KF_MODEL_LARGE"] = "claude-sonnet-4-6"
        from scripts import doctor

        rep = doctor.check_model_ids()
        self.assertEqual(rep["providers"]["anthropic"]["status"], "not-allowed")

    def test_stale_when_allowed_id_not_listed_by_key(self):
        os.environ["ANTHROPIC_API_KEY"] = "sk-ant-test"
        os.environ["KF_MODEL_SMALL"] = "claude-haiku-4-5"
        os.environ["KF_MODEL_LARGE"] = "claude-sonnet-4-6"
        payload = {"data": [{"id": "claude-sonnet-4-6"}]}  # key does not list haiku
        with mock.patch("urllib.request.urlopen", lambda *_a, **_kw: _Response(payload)):
            from scripts import doctor

            rep = doctor.check_model_ids()
        self.assertEqual(rep["providers"]["anthropic"]["status"], "stale")
        self.assertFalse(rep["providers"]["anthropic"]["small_valid"])
        self.assertTrue(rep["providers"]["anthropic"]["large_valid"])


if __name__ == "__main__":
    unittest.main()
