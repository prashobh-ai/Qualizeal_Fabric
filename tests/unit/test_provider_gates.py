"""T147 + T148 — the verify gates bite only when a provider/source actually verified.

Pure unit tests (no build): they exercise the honest-degradation rules directly, so
they run in every profile and prove the gates never take the live demo offline under
the current unfunded key / unapproved org, yet do catch a real regression.
"""

from __future__ import annotations

import os
import tempfile
import unittest

os.environ.setdefault("KF_MODEL_MODE", "off")

from scripts import build_showcase, verify_showcase  # noqa: E402


class TestProviderManifest(unittest.TestCase):
    def test_current_provider_key_from_mode(self):
        for mode, want in [
            ("", "open-source"),
            ("extractive", "open-source"),
            ("off", "open-source"),
            ("anthropic", "anthropic"),
            ("openai", "openai"),
        ]:
            old = os.environ.get("KF_MODEL_MODE")
            os.environ["KF_MODEL_MODE"] = mode
            try:
                self.assertEqual(build_showcase._current_provider_key(), want, mode)
            finally:
                if old is None:
                    os.environ.pop("KF_MODEL_MODE", None)
                else:
                    os.environ["KF_MODEL_MODE"] = old

    def test_manifest_marks_others_unavailable_with_reason(self):
        m = build_showcase._providers_manifest("open-source")
        by = {p["key"]: p for p in m["providers"]}
        self.assertTrue(by["open-source"]["available"])
        self.assertFalse(by["anthropic"]["available"])
        self.assertIn("ANTHROPIC_API_KEY", by["anthropic"]["reason"])
        self.assertFalse(by["openai"]["available"])
        self.assertIn("OPENAI_API_KEY", by["openai"]["reason"])


class TestProviderBakeGate(unittest.TestCase):
    def _snap(self, current="open-source"):
        return {"providers": build_showcase._providers_manifest(current)}

    def test_available_provider_needs_a_baked_set(self):
        with tempfile.TemporaryDirectory() as d:
            # open-source is available but no answers/open-source dir → error
            errs = verify_showcase._provider_bake_errors(d, self._snap())
            self.assertTrue(any("open-source" in e for e in errs), errs)
            # create one baked file → no error
            os.makedirs(os.path.join(d, "answers", "open-source"))
            open(os.path.join(d, "answers", "open-source", "abc.json"), "w").close()
            self.assertEqual(verify_showcase._provider_bake_errors(d, self._snap()), [])

    def test_unavailable_provider_never_errors(self):
        # anthropic/openai are unavailable this build → their empty set is fine
        with tempfile.TemporaryDirectory() as d:
            os.makedirs(os.path.join(d, "answers", "open-source"))
            open(os.path.join(d, "answers", "open-source", "abc.json"), "w").close()
            self.assertEqual(verify_showcase._provider_bake_errors(d, self._snap()), [])


class TestSourceIngestGate(unittest.TestCase):
    def test_absent_preflight_is_never_an_error(self):
        snap = {"preflight": {"absent": True}, "facts": {}}
        self.assertEqual(verify_showcase._source_ingest_errors(snap), [])

    def test_skipped_or_failed_source_never_errors(self):
        snap = {
            "preflight": {
                "results": [
                    {"key": "github", "ok": False, "skipped": True},  # no secret
                    {"key": "jira", "ok": False, "skipped": False, "detail": "403"},  # org approval
                ]
            },
            "facts": {},
        }
        self.assertEqual(verify_showcase._source_ingest_errors(snap), [])

    def test_verified_source_must_have_facts(self):
        snap = {
            "preflight": {"results": [{"key": "github", "ok": True, "skipped": False}]},
            "facts": {},  # verified but no repositories → error
        }
        errs = verify_showcase._source_ingest_errors(snap)
        self.assertTrue(any("github" in e for e in errs), errs)
        # with facts present → no error
        snap["facts"] = {"repositories": ["a/b"]}
        self.assertEqual(verify_showcase._source_ingest_errors(snap), [])


if __name__ == "__main__":
    unittest.main()
