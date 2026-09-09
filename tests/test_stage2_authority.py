"""Stage-2 Section B: authoritative-source policy (ranks, weights, boost, citations)."""
import unittest

from knowledge_fabric.answer.service import AnswerService
from knowledge_fabric.contracts.types import Citation, Coordinate, CoordinateKind
from knowledge_fabric.governance import authority as auth
from knowledge_fabric.tenants import demo
from tests.util import seeded

T = "q-quality"


def _cite(doc: dict, passage_id: str = "pas_x") -> Citation:
    return Citation(doc["id"], doc["title"], Coordinate(CoordinateKind.PAGE_PARAGRAPH,
                                                        {"page": 1, "paragraph": 1}), passage_id, "…")


class AuthorityBase(unittest.TestCase):
    def setUp(self):
        self.p = seeded([T])
        self.docs = {d["source"]: d for d in self.p.documents.list(T)}   # one per source is enough
        self.by_uri = {d["uri"]: d for d in self.p.documents.list(T)}

    def _doc(self, source):
        return self.docs[source]


class TestRanksAndWeights(AuthorityBase):
    def test_defaults_and_weight_decay(self):
        self.assertEqual(auth.rank_for(self.p, T, "files"), 1)
        self.assertEqual(auth.rank_for(self.p, T, "jira"), 4)
        self.assertEqual(auth.rank_for(self.p, T, "unknown-source"), auth.UNKNOWN_RANK)
        self.assertEqual(auth.weight_for(self.p, T, "files"), 1.0)
        self.assertAlmostEqual(auth.weight_for(self.p, T, "confluence"), 0.8)
        self.assertAlmostEqual(auth.weight_for(self.p, T, "github"), 1 / 1.5, places=5)
        self.assertAlmostEqual(auth.weight_for(self.p, T, "jira"), 1 / 1.75, places=5)
        w = [auth.weight_for(self.p, T, s) for s in ("files", "confluence", "github", "jira")]
        self.assertEqual(w, sorted(w, reverse=True))

    def test_set_source_rank_overrides_per_tenant(self):
        auth.set_source_rank(self.p, T, "jira", 1)
        self.assertEqual(auth.rank_for(self.p, T, "jira"), 1)
        self.assertEqual(auth.weight_for(self.p, T, "jira"), 1.0)
        auth.set_source_rank(self.p, T, "jira", 3)                       # upsert
        self.assertEqual(auth.rank_for(self.p, T, "jira"), 3)
        self.assertEqual(auth.rank_for(self.p, "q-airlines", "jira"), 4)   # other tenant untouched
        with self.assertRaises(ValueError):
            auth.set_source_rank(self.p, T, "jira", 0)
        with self.assertRaises(ValueError):
            auth.set_source_rank(self.p, T, "", 1)
        with self.assertRaises(PermissionError):
            auth.set_source_rank(self.p, "", "jira", 1)

    def test_list_ranks_sorted_and_marks_overrides(self):
        auth.set_source_rank(self.p, T, "jira", 1)
        rows = auth.list_ranks(self.p, T)
        sources = {r["source"] for r in rows}
        self.assertTrue(set(auth.DEFAULT_RANKS) <= sources)
        self.assertIn("github", sources)                                  # seeded document sources
        ranks = [r["rank"] for r in rows]
        self.assertEqual(ranks, sorted(ranks))
        jira = next(r for r in rows if r["source"] == "jira")
        self.assertEqual((jira["rank"], jira["weight"], jira["overridden"]), (1, 1.0, True))
        files = next(r for r in rows if r["source"] == "files")
        self.assertFalse(files["overridden"])


class TestAuthoritativeFlag(AuthorityBase):
    def test_mark_and_unmark_are_audited(self):
        doc = self._doc("files")
        self.assertFalse(auth.is_authoritative(self.p, T, doc["id"]))
        n0 = len(self.p.audit.for_tenant(T, 500))
        auth.mark_authoritative(self.p, T, doc["id"], True, "curator")
        self.assertTrue(auth.is_authoritative(self.p, T, doc["id"]))
        auth.mark_authoritative(self.p, T, doc["id"], False, "curator")
        self.assertFalse(auth.is_authoritative(self.p, T, doc["id"]))
        entries = self.p.audit.for_tenant(T, 500)
        self.assertEqual(len(entries), n0 + 2)
        self.assertEqual([e["action"] for e in entries[:2]], ["authority.unmark", "authority.mark"])
        self.assertEqual(entries[0]["resource"], f"document:{doc['id']}")
        self.assertEqual(entries[0]["subject"], "curator")

    def test_mark_is_tenant_scoped_and_validated(self):
        doc = self._doc("files")
        with self.assertRaises(KeyError):
            auth.mark_authoritative(self.p, "q-airlines", doc["id"], True, "asker.public")
        self.assertFalse(auth.is_authoritative(self.p, T, doc["id"]))
        with self.assertRaises(KeyError):
            auth.mark_authoritative(self.p, T, "doc_missing", True, "curator")
        with self.assertRaises(ValueError):
            auth.mark_authoritative(self.p, T, doc["id"], True, "")
        self.assertFalse(auth.is_authoritative(self.p, "q-airlines", doc["id"]))


class TestBoost(AuthorityBase):
    def test_boost_reorders_by_source_weight_and_flag(self):
        files, jira, gh = self._doc("files"), self._doc("jira"), self._doc("github")
        fused = [("p_jira", 0.50), ("p_files", 0.45), ("p_gh", 0.40), ("p_unknown", 0.30)]
        pdoc = {"p_jira": (jira["id"], "jira"), "p_files": (files["id"], "files"),
                "p_gh": (gh["id"], "github")}
        out = auth.boost(self.p, T, fused, pdoc)
        scores = dict(out)
        self.assertAlmostEqual(scores["p_files"], 0.45)                 # rank 1 → ×1.0
        self.assertAlmostEqual(scores["p_jira"], 0.50 / 1.75, places=6)   # rank 4
        self.assertAlmostEqual(scores["p_gh"], 0.40 / 1.5, places=6)      # rank 3
        self.assertAlmostEqual(scores["p_unknown"], 0.30)               # not mapped → untouched
        self.assertEqual([pid for pid, _ in out], ["p_files", "p_unknown", "p_jira", "p_gh"])

        auth.mark_authoritative(self.p, T, gh["id"], True, "curator")
        out2 = auth.boost(self.p, T, fused, pdoc)
        self.assertAlmostEqual(dict(out2)["p_gh"], 0.40 / 1.5 * 1.5, places=6)
        self.assertEqual([pid for pid, _ in out2][:2], ["p_files", "p_gh"])

    def test_boost_is_stable_on_ties_and_empty(self):
        self.assertEqual(auth.boost(self.p, T, [], {}), [])
        fused = [("a", 0.5), ("b", 0.5), ("c", 0.5)]
        out = auth.boost(self.p, T, fused, {})
        self.assertEqual([pid for pid, _ in out], ["a", "b", "c"])


class TestCitationExplanations(AuthorityBase):
    def test_authoritative_source_picks_best_rank(self):
        files, jira = self._doc("files"), self._doc("jira")
        res = auth.authoritative_source(self.p, T, [_cite(jira, "p1"), _cite(files, "p2")])
        self.assertEqual(res["document_id"], files["id"])
        self.assertEqual(res["document_title"], files["title"])
        self.assertEqual(res["source"], "files")
        self.assertIn("rank", res["reason"])
        self.assertIn("'jira'", res["reason"])

    def test_authoritative_flag_beats_rank(self):
        files, jira = self._doc("files"), self._doc("jira")
        auth.mark_authoritative(self.p, T, jira["id"], True, "curator")
        res = auth.authoritative_source(self.p, T, [_cite(files, "p1"), _cite(jira, "p2")])
        self.assertEqual(res["document_id"], jira["id"])
        self.assertIn("marked authoritative", res["reason"])

    def test_authoritative_source_tie_goes_to_first_cited_and_single(self):
        docs = [d for d in self.p.documents.list(T) if d["source"] == "files"]
        self.assertGreaterEqual(len(docs), 2)
        res = auth.authoritative_source(self.p, T, [_cite(docs[1], "p1"), _cite(docs[0], "p2")])
        self.assertEqual(res["document_id"], docs[1]["id"])
        self.assertIn("cited first", res["reason"])
        single = auth.authoritative_source(self.p, T, [_cite(docs[0], "p2")])
        self.assertEqual(single["document_id"], docs[0]["id"])
        self.assertIn("only cited document", single["reason"])
        self.assertIsNone(auth.authoritative_source(self.p, T, []))

    def test_citations_from_other_tenant_are_ignored(self):
        files = self._doc("files")
        self.assertIsNone(auth.authoritative_source(self.p, "q-airlines", [_cite(files)]))
        self.assertEqual(auth.conflicts(self.p, "q-airlines", [_cite(files)]), [])

    def test_conflicts_between_sources_of_different_rank(self):
        files, jira, gh = self._doc("files"), self._doc("jira"), self._doc("github")
        cites = [_cite(jira, "p1"), _cite(files, "p2"), _cite(gh, "p3"), _cite(files, "p4")]
        out = auth.conflicts(self.p, T, cites)
        pairs = {(c["a"], c["b"]): c for c in out}
        self.assertEqual(len(out), 3)                     # jira/files, jira/github, files/github
        self.assertEqual(pairs[(jira["id"], files["id"])]["preferred"], files["id"])
        self.assertEqual(pairs[(jira["id"], gh["id"])]["preferred"], gh["id"])
        self.assertEqual(pairs[(files["id"], gh["id"])]["preferred"], files["id"])
        self.assertIn("outranks", out[0]["reason"])
        # same source → never a conflict; equal ranks → no conflict
        same = [d for d in self.p.documents.list(T) if d["source"] == "files"][:2]
        self.assertEqual(auth.conflicts(self.p, T, [_cite(same[0], "a"), _cite(same[1], "b")]), [])
        auth.set_source_rank(self.p, T, "jira", 1)
        self.assertEqual(auth.conflicts(self.p, T, [_cite(jira, "p1"), _cite(files, "p2")]), [])

    def test_conflict_reason_mentions_authoritative_flag(self):
        files, jira = self._doc("files"), self._doc("jira")
        auth.mark_authoritative(self.p, T, jira["id"], True, "curator")
        out = auth.conflicts(self.p, T, [_cite(files, "p1"), _cite(jira, "p2")])
        self.assertEqual(len(out), 1)
        self.assertEqual(out[0]["preferred"], jira["id"])
        self.assertIn("marked authoritative", out[0]["reason"])

    def test_works_on_real_answer_citations(self):
        svc = AnswerService(self.p)
        principal = demo.principal_for(self.p, T, "asker.public")
        ans = svc.ask(principal, "what must a release achieve before promotion?")
        self.assertTrue(ans.citations)
        res = auth.authoritative_source(self.p, T, ans.citations)
        self.assertIsNotNone(res)
        self.assertIn(res["document_id"], {c.document_id for c in ans.citations})
        for c in auth.conflicts(self.p, T, ans.citations):
            self.assertIn(c["preferred"], (c["a"], c["b"]))


if __name__ == "__main__":
    unittest.main()
