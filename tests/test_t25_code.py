"""T25 — code into the fabric: AST symbol chunking + the identifier tier.

The converter chunks source by symbol (function / method / class) with a rich,
line-anchored locator; a code question is answered directly from the matching
function at Level 1, cited to ``path#L<start>-L<end>``. All offline, no model.
"""

from __future__ import annotations

import unittest

from knowledge_fabric.adapters.converter import DoclingLite
from knowledge_fabric.answer.service import AnswerService
from knowledge_fabric.contracts.types import AnswerKind, CoordinateKind, RawItem
from knowledge_fabric.ingestion.intake import IngestWorker, Intake
from knowledge_fabric.tenants import demo
from tests.util import seeded

_SRC = '''\
"""Widget module."""


def retry(fn, attempts=3):
    """Retry fn up to attempts times with backoff."""
    for i in range(attempts):
        try:
            return fn()
        except Exception:
            continue


class Store:
    """A tiny key-value store."""

    def write(self, key, value):
        """Persist value under key in the database."""
        self._db[key] = value
'''


def _raw(path, src):
    return RawItem(
        tenant="t",
        source="github",
        source_version="1",
        uri=f"github://acme/widgets/{path}",
        mime="text/x-python;code",
        title=path.rsplit("/", 1)[-1],
        bytes_=src.encode(),
        language="en",
    )


class TestConverterSymbols(unittest.TestCase):
    def setUp(self):
        self.cv = DoclingLite().convert(_raw("pkg/widget.py", _SRC))

    def test_one_passage_per_symbol_with_line_range(self):
        syms = {r.coordinate.locator["qualified"] for r in self.cv.regions}
        # top-level function, the class, and the method each get a passage
        self.assertIn("pkg.widget.retry", syms)
        self.assertIn("pkg.widget.Store", syms)
        self.assertIn("pkg.widget.Store.write", syms)
        for r in self.cv.regions:
            self.assertEqual(r.coordinate.kind, CoordinateKind.SYMBOL_LINE)
            loc = r.coordinate.locator
            self.assertLessEqual(loc["start_line"], loc["end_line"])

    def test_github_line_anchored_url(self):
        retry = next(r for r in self.cv.regions if r.coordinate.locator["symbol"] == "retry")
        loc = retry.coordinate.locator
        self.assertEqual(
            loc["url"],
            f"https://github.com/acme/widgets/blob/main/pkg/widget.py#L{loc['start_line']}-L{loc['end_line']}",
        )

    def test_summary_line_is_deterministic_and_names_the_docstring(self):
        write = next(
            r for r in self.cv.regions if r.coordinate.locator["qualified"].endswith("Store.write")
        )
        summary = write.coordinate.locator["summary_line"]
        self.assertIn("Store.write", summary)
        self.assertIn("Persist value under key", summary)

    def test_syntax_error_falls_back_to_windows(self):
        cv = DoclingLite().convert(_raw("bad.py", "def broken(:\n  pass\n"))
        self.assertTrue(cv.regions)  # no crash; window fallback still yields passages
        self.assertEqual(cv.regions[0].coordinate.kind, CoordinateKind.SYMBOL_LINE)


class TestCodeAnswer(unittest.TestCase):
    def setUp(self):
        self.p = seeded(["cf"])
        intake, worker = Intake(self.p), IngestWorker(self.p, None)
        worker.intake = intake
        intake.submit(intake.canonical("cf", "github", "github://acme/widgets/pkg/widget.py",
                                       "widget.py", _SRC.encode(), mime="text/x-python;code",
                                       acl=["public"]))
        worker.drain()
        self.svc = AnswerService(self.p)
        self.asker = demo.principal_for(self.p, "cf", "asker.public")

    def test_code_question_cites_the_function_with_line_anchor(self):
        a = self.svc.ask(self.asker, "where is the retry logic")
        self.assertEqual(a.kind, AnswerKind.ANSWER)
        self.assertEqual(a.level, 1)  # identifier hit routes to Level 1
        self.assertIn("```", a.answer_text)  # a fenced code block, verbatim
        self.assertTrue(a.citations)
        loc = a.citations[0].coordinate.locator
        self.assertIn("#L", loc["url"])
        self.assertEqual(loc["symbol"], "retry")

    def test_never_paraphrases_code(self):
        a = self.svc.ask(self.asker, "where is the retry logic")
        self.assertIn("def retry(fn, attempts=3):", a.answer_text)


if __name__ == "__main__":
    unittest.main()
