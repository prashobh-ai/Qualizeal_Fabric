"""T94 — code answers show the cited function in a fenced block.

A code question is answered from the matching symbol: a short prose line, then
the cited function verbatim in a fenced block tagged with its language, then a
line-anchored citation (GitHub ``#L`` URL). Code is never paraphrased, and a
persona depth cap bounds the number of blocks. Builds on the T25 code path.
"""

from __future__ import annotations

import unittest

from knowledge_fabric.answer.service import AnswerService
from knowledge_fabric.contracts.types import AnswerKind, CoordinateKind
from knowledge_fabric.ingestion.intake import IngestWorker, Intake
from knowledge_fabric.tenants import demo

CF = "cf"

_SRC = '''\
def retry(fn, attempts=3):
    """Retry a callable up to ``attempts`` times before giving up."""
    last = None
    for _ in range(attempts):
        try:
            return fn()
        except Exception as exc:
            last = exc
    raise last


def healthcheck():
    """Return True when the service is ready."""
    return True
'''


class TestCodeSnippet(unittest.TestCase):
    def setUp(self):
        from tests.util import seeded

        self.p = seeded([CF], model_mode="extractive")
        intake = Intake(self.p)
        worker = IngestWorker(self.p, intake)
        worker.intake = intake
        intake.submit(
            intake.canonical(
                CF,
                "github",
                "github://acme/widgets/pkg/widget.py",
                "widget.py",
                _SRC.encode(),
                mime="text/x-python;code",
                acl=["public"],
            )
        )
        worker.drain()
        self.svc = AnswerService(self.p)
        self.asker = demo.principal_for(self.p, CF, "asker.public")

    def test_answer_has_a_language_tagged_fenced_block_and_line_anchor(self):
        a = self.svc.ask(self.asker, "where is the retry logic")
        self.assertEqual(a.kind, AnswerKind.ANSWER)
        # a fenced block tagged with the language (```python), verbatim code
        self.assertIn("```python", a.answer_text)
        self.assertIn("def retry(fn, attempts=3):", a.answer_text)
        # the citation is a code coordinate, line-anchored to GitHub
        c = a.citations[0]
        self.assertEqual(c.coordinate.kind, CoordinateKind.SYMBOL_LINE)
        loc = c.coordinate.locator
        self.assertIn("#L", loc["url"])
        self.assertEqual(loc["symbol"], "retry")

    def test_at_most_two_code_blocks(self):
        a = self.svc.ask(self.asker, "show the retry and healthcheck functions")
        self.assertLessEqual(a.answer_text.count("```") // 2, 2)


if __name__ == "__main__":
    unittest.main()
