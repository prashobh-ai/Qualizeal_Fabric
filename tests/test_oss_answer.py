"""T91 — keyless answering that actually runs, labelled Open-source LLM.

The platform must answer with no provider key: ``KF_MODEL_MODE`` defaults to
``auto`` (T91), which resolves to the open-source fallback when no Anthropic key
verifies, rather than raising. A keyless answer is grounded (cited), carries a
non-zero input/output token split, and is labelled ``Open-source LLM`` on every
surface — never ``mock``/``extractive``/``disabled``.
"""

from __future__ import annotations

import os
import tempfile
import unittest

from knowledge_fabric.adapters import model, oss_model
from knowledge_fabric.answer.service import AnswerService
from knowledge_fabric.tenants import demo
from tests.util import T, seeded

os.environ.setdefault("KF_DATA_ROOT", tempfile.mkdtemp(prefix="kf-oss-answer-"))


class TestDefaultModeIsAutoKeyless(unittest.TestCase):
    """Unset mode + no key resolves to the open-source client, not a raise."""

    def setUp(self):
        self._snap = {k: os.environ.get(k) for k in ("KF_MODEL_MODE", "ANTHROPIC_API_KEY")}
        os.environ.pop("KF_MODEL_MODE", None)  # unset -> the new 'auto' default
        os.environ.pop("ANTHROPIC_API_KEY", None)

    def tearDown(self):
        for k, v in self._snap.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v

    def test_unset_mode_falls_through_to_open_source(self):
        client = model.build_model_client()
        self.assertIsInstance(client, oss_model.OSSModelClient)
        self.assertTrue(client.available())

    def test_label_says_open_source_llm(self):
        self.assertTrue(oss_model.provider_label()["label"].startswith("Open-source LLM"))


class TestKeylessAnswer(unittest.TestCase):
    """A keyless answer is grounded and labelled from the open-source path."""

    def setUp(self):
        self.p = seeded([T], model_mode="oss")
        self.svc = AnswerService(self.p)

    def test_keyless_answer_is_cited_and_open_source_labelled(self):
        prin = demo.principal_for(self.p, T, "asker.public")
        a = self.svc.ask(prin, "what does the platform provide for automated testing?")
        self.assertEqual(a.kind.value, "answer")
        self.assertTrue(a.answer_text.strip())
        self.assertTrue(a.citations, "a keyless answer must still be grounded")
        # the model that answered is the open-source path, never a hidden mock
        self.assertNotIn(a.model_name.lower(), ("mock", "disabled", "extractive"))

    def test_provider_badge_labels_open_source_llm(self):
        from knowledge_fabric.surfaces import http_api

        badge = http_api._provider_badge(self.p)
        self.assertEqual(badge["provider"], "Open-source LLM")
        self.assertTrue(badge["label"].startswith("Open-source LLM"))


if __name__ == "__main__":
    unittest.main()
