"""T53 — the manual-review gate proven end to end at the answer path.

`test_curation_modes` proves a review item is absent from the *live set*.
This proves the stronger contract the spec asks for: a document held in
manual review never reaches an *asker's trace* — its passages are held out
of retrieval, so no citation, snippet, or graph hop can leak it — and that
accepting it makes the very same content answerable (a positive control, so
the gate is not simply a broken retriever).
"""

import unittest

from knowledge_fabric import curation
from knowledge_fabric.answer.search import discover
from knowledge_fabric.answer.service import AnswerService
from knowledge_fabric.ingestion.intake import IngestWorker, Intake
from knowledge_fabric.tenants import demo
from tests.util import seeded

T = "cf"

# A distinctive, self-contained subject that retrieval would strongly favour
# if it were live — so a leak would show up as a citation, not a near miss.
SUBJECT = "Zephyrine Telemetry Beacon"
BODY = (
    f"The {SUBJECT} is an internal diagnostics appliance. "
    f"It streams heartbeat frames every four seconds to the observability mesh. "
    f"Each {SUBJECT} unit is provisioned with a rotating attestation key. "
    f"Operators query the {SUBJECT} console to replay the last hour of frames."
)
QUESTION = f"What is the {SUBJECT}?"


class ReviewGateBase(unittest.TestCase):
    def setUp(self):
        self.p = seeded([T], model_mode="extractive")
        self.intake = Intake(self.p)
        self.svc = AnswerService(self.p)
        self.asker = demo.principal_for(self.p, T, "asker.public")
        curation.set_mode(self.p, T, "internal", "manual")

    def _ingest_under_review(self) -> tuple[dict, str]:
        raw = self.intake.canonical(
            T,
            "internal",
            "internal://z/beacon.md",
            SUBJECT,
            BODY.encode(),
            mime="text/markdown",
            acl=["public"],
        )
        self.intake.submit(raw)
        res = IngestWorker(self.p, self.intake).drain()[-1]
        doc = self.p.documents.get(T, res["document_id"])
        outcome = curation.on_ingest(self.p, T, doc, "internal")
        self.assertEqual(outcome["state"], "review")
        return doc, outcome["review_id"]

    def _cited_doc_ids(self, answer) -> set[str]:
        return {c.document_id for c in answer.citations}


class TestReviewGate(ReviewGateBase):
    def test_review_item_never_reaches_an_asker_trace(self):
        doc, _rid = self._ingest_under_review()

        a = self.svc.ask(self.asker, QUESTION)

        # The held document is never cited...
        self.assertNotIn(doc["id"], self._cited_doc_ids(a))
        # ...and its distinctive content never appears in the answer body.
        self.assertNotIn(SUBJECT.lower(), a.answer_text.lower())

    def test_review_item_is_absent_from_discovery(self):
        doc, _rid = self._ingest_under_review()

        # A capability/discovery query that would list the asset if it were live.
        d = discover(self.p, T, f"where can i find {SUBJECT}", ["public"])
        self.assertNotIn(doc["id"], {h.document_id for h in d.hits})

    def test_accept_makes_the_same_content_answerable(self):
        doc, rid = self._ingest_under_review()
        # Before acceptance: held out.
        before = self.svc.ask(self.asker, QUESTION)
        self.assertNotIn(doc["id"], self._cited_doc_ids(before))

        curation.accept(self.p, T, rid, actor="curator")

        after = self.svc.ask(self.asker, QUESTION)
        self.assertIn(doc["id"], self._cited_doc_ids(after))


if __name__ == "__main__":
    unittest.main()
