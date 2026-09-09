"""Stage-2 Section A — multistep + conditional reasoning planner/executor."""
import json
import unittest

from knowledge_fabric.answer import reasoning as R
from knowledge_fabric.contracts.types import (
    Answer, AnswerKind, Citation, Coordinate, CoordinateKind,
)


def _cit(doc, pid, n=1):
    return Citation(document_id=doc, document_title=f"Doc {doc}",
                    coordinate=Coordinate(CoordinateKind.PAGE_PARAGRAPH, {"page": 1, "paragraph": n}),
                    passage_id=pid, snippet=f"snippet {pid}")


def _ans(text, cits, g=0.8, conf=0.8, kind=AnswerKind.ANSWER, clarify=None):
    return Answer(kind, text, cits, conf, "traj_x", 0.0, 0, "none", grounding_score=g,
                  clarify_back=clarify, tenant="qualizeal")


def _gap(text="The corpus does not contain enough grounded evidence to answer this."):
    return _ans(text, [], g=0.2, conf=0.1, kind=AnswerKind.GAP)


def _clarify(text="Which release do you mean?"):
    return _ans(text, [], g=0.3, conf=0.3, kind=AnswerKind.CLARIFY, clarify=text)


def _router(table, default=None):
    """Fake governed ask_fn: first table key contained in the sub-question wins."""
    calls = []

    def ask(q):
        calls.append(q)
        for key, ans in table:
            if key.lower() in q.lower():
                return ans
        return default or _gap()
    ask.calls = calls
    return ask


class PlanShapes(unittest.TestCase):
    def test_single_fallback(self):
        p = R.plan("What is the smoke test?")
        self.assertEqual(p["mode"], "single")
        self.assertEqual([s["kind"] for s in p["steps"]], ["lookup"])
        self.assertEqual(p["steps"][0]["question"], "What is the smoke test?")
        self.assertEqual(p["complexity"], "simple")

    def test_conjunction_multistep(self):
        p = R.plan("What are the entry criteria and what are the exit criteria for regression?")
        self.assertEqual(p["mode"], "multistep")
        kinds = [s["kind"] for s in p["steps"]]
        self.assertEqual(kinds, ["lookup", "lookup", "synthesize"])
        self.assertEqual(p["steps"][0]["question"], "What are the entry criteria?")
        self.assertEqual(p["steps"][1]["question"], "What are the exit criteria for regression?")
        self.assertEqual(p["steps"][1]["depends_on"], [])          # 'and' → independent
        self.assertEqual(p["steps"][2]["depends_on"], ["s1", "s2"])
        self.assertIn("and", p["signals"]["connectives"])

    def test_ordered_then_multistep(self):
        p = R.plan("List the P1 defects, then who approves the release, after that what is the SLA?")
        self.assertEqual(p["mode"], "multistep")
        asks = [s for s in p["steps"] if s["kind"] == "lookup"]
        self.assertEqual(len(asks), 3)
        self.assertEqual(asks[1]["depends_on"], ["s1"])              # 'then' → sequential
        self.assertEqual(asks[2]["depends_on"], ["s2"])
        self.assertTrue(p["signals"]["ordered"])

    def test_noun_conjunction_is_not_split(self):
        p = R.plan("What are the entry and exit criteria?")
        self.assertEqual(p["mode"], "single")

    def test_if_then_otherwise(self):
        p = R.plan("If the release is blocked then what is the escalation path, otherwise who signs off?")
        self.assertEqual(p["mode"], "conditional")
        s1, s2, s3, s4 = p["steps"]
        self.assertEqual(s1["kind"], "condition")
        self.assertEqual(s1["question"], "Is the release blocked?")
        self.assertEqual(s1["branch"], {"if_true": "s2", "if_false": "s3"})
        self.assertEqual(s2["question"], "What is the escalation path?")
        self.assertEqual(s3["question"], "Who signs off?")
        self.assertEqual(s4["kind"], "synthesize")
        self.assertTrue(p["signals"]["has_otherwise"])

    def test_if_comma_without_otherwise(self):
        p = R.plan("If a P1 defect is open, what is the escalation path?")
        self.assertEqual(p["mode"], "conditional")
        self.assertEqual(p["steps"][0]["branch"], {"if_true": "s2", "if_false": None})
        self.assertEqual(p["steps"][0]["question"], "Is a P1 defect open?")

    def test_when_conditional(self):
        p = R.plan("When the build is not signed, does the release proceed?")
        self.assertEqual(p["mode"], "conditional")
        self.assertEqual(p["signals"]["form"], "when")
        self.assertEqual(p["steps"][0]["clause"], "the build is not signed")
        self.assertEqual(p["steps"][1]["question"], "Does the release proceed?")

    def test_trailing_if(self):
        p = R.plan("What is the escalation path if the release is blocked?")
        self.assertEqual(p["mode"], "conditional")
        self.assertEqual(p["steps"][0]["question"], "Is the release blocked?")
        # 'what happens if …' is a plain lookup, not a checkable branch
        self.assertEqual(R.plan("What happens if a defect is found?")["mode"], "single")

    def test_compare_forms(self):
        for q in ("Compare smoke testing and regression testing",
                  "What is the difference between smoke testing and regression testing?",
                  "Smoke testing vs regression testing",
                  "How does smoke testing differ from regression testing?"):
            p = R.plan(q)
            self.assertEqual(p["mode"], "compare", q)
            self.assertEqual([s["kind"] for s in p["steps"]], ["lookup", "lookup", "compare"], q)
            self.assertEqual(p["steps"][2]["depends_on"], ["s1", "s2"])
            self.assertIn("regression testing", p["signals"]["b"].lower())

    def test_compare_distributes_shared_head(self):
        p = R.plan("Compare the entry criteria for smoke testing and regression testing")
        self.assertEqual(p["steps"][0]["question"], "What is the entry criteria for smoke testing?")
        self.assertEqual(p["steps"][1]["question"], "What is the entry criteria for regression testing?")

    def test_plan_is_deterministic_and_serialisable(self):
        q = "If the release is blocked then what is the escalation path, otherwise who signs off?"
        a, b = R.plan(q), R.plan(q)
        self.assertEqual(a, b)
        json.dumps(a)   # no non-serialisable content


class ExecuteMultistep(unittest.TestCase):
    def test_merge_and_renumber_citations(self):
        ask = _router([
            ("entry criteria", _ans("Entry needs a green build [1]. Entry needs signed release notes [2].",
                                    [_cit("d1", "p1"), _cit("d1", "p2")], g=0.8, conf=0.9)),
            ("exit criteria", _ans("Exit needs zero P1 defects [1]. Exit needs signed release notes [2].",
                                   [_cit("d2", "p9"), _cit("d1", "p2")], g=0.7, conf=0.7)),
        ])
        p = R.plan("What are the entry criteria and what are the exit criteria?")
        r = R.execute(p, ask)
        self.assertEqual(r["kind"], "answer")
        self.assertEqual(ask.calls, ["What are the entry criteria?", "What are the exit criteria?"])
        # p2 cited by both steps → de-duplicated, numbered once
        self.assertEqual([c.passage_id for c in r["citations"]], ["p1", "p2", "p9"])
        self.assertEqual(r["steps"][1]["answer_text"],
                         "Exit needs zero P1 defects [3]. Exit needs signed release notes [2].")
        self.assertEqual(r["steps"][1]["citation_numbers"], [3, 2])
        self.assertEqual(r["final_text"],
                         "Entry needs a green build [1]. Entry needs signed release notes [2]. "
                         "Exit needs zero P1 defects [3]. Exit needs signed release notes [2].")
        # final text is made only of step sentences
        for sent in R._sentences(r["final_text"]):
            self.assertTrue(any(sent in s["answer_text"] for s in r["steps"]), sent)
        self.assertEqual(r["confidence"], 0.7)      # min(0.9, 0.7) × coverage 2/2
        self.assertEqual(r["complexity"], "medium")
        self.assertEqual(r["steps"][2]["kind"], "synthesize")
        self.assertEqual(r["steps"][2]["kind_result"]["merged_steps"], ["s1", "s2"])

    def test_gap_step_propagates_honestly(self):
        ask = _router([("entry criteria", _ans("Entry needs a green build [1].", [_cit("d1", "p1")]))])
        r = R.execute(R.plan("What are the entry criteria and what are the exit criteria?"), ask)
        self.assertEqual(r["kind"], "gap")
        self.assertEqual(r["gap_step"], "s2")
        self.assertIn("Step 2 (“What are the exit criteria?”) lacked evidence", r["final_text"])
        self.assertIn("Partial result. Entry needs a green build [1].", r["final_text"])
        self.assertEqual([c.passage_id for c in r["citations"]], ["p1"])
        self.assertLess(r["confidence"], 0.5)

    def test_clarify_step_propagates(self):
        ask = _router([("entry criteria", _clarify("Which release do you mean?"))],
                      default=_ans("Exit needs zero P1 defects [1].", [_cit("d2", "p9")]))
        r = R.execute(R.plan("What are the entry criteria and what are the exit criteria?"), ask)
        self.assertEqual(r["kind"], "clarify")
        self.assertEqual(r["clarify_back"], "Which release do you mean?")
        self.assertIn("Step 1", r["final_text"])

    def test_single_mode_passthrough(self):
        ask = _router([("smoke", _ans("Smoke runs on every build [1].", [_cit("d3", "p3")], conf=0.66))])
        r = R.execute(R.plan("What is the smoke test?"), ask)
        self.assertEqual(r["mode"], "single")
        self.assertEqual(r["final_text"], "Smoke runs on every build [1].")
        self.assertEqual(r["confidence"], 0.66)
        self.assertEqual(len(r["steps"]), 1)


class ExecuteConditional(unittest.TestCase):
    Q = "If the release is blocked then what is the escalation path, otherwise who signs off?"

    def test_true_branch_selected(self):
        ask = _router([
            ("release blocked", _ans("The release is blocked while any P1 defect is open [1].",
                                     [_cit("d1", "p1")], g=0.8, conf=0.8)),
            ("escalation path", _ans("Escalate to the QA lead within 4 hours [1].", [_cit("d2", "p2")], conf=0.9)),
            ("signs off", _ans("The release manager signs off [1].", [_cit("d3", "p3")])),
        ])
        r = R.execute(R.plan(self.Q), ask)
        self.assertEqual(r["kind"], "answer")
        s1, s2, s3, s4 = r["steps"]
        self.assertIs(s1["kind_result"]["condition"], True)
        self.assertFalse(s2["skipped"])
        self.assertTrue(s3["skipped"])
        self.assertEqual(s3["kind_result"]["reason"], "branch not selected")
        self.assertNotIn("Who signs off?", ask.calls)          # skipped branch never asked
        self.assertIn("Escalate to the QA lead within 4 hours [2].", r["final_text"])
        self.assertIn("holds based on the evidence", r["final_text"])
        self.assertEqual([c.passage_id for c in r["citations"]], ["p1", "p2"])
        self.assertEqual(r["confidence"], 0.8)                  # min(0.8, 0.9) × 2/2

    def test_false_branch_selected_by_negation(self):
        ask = _router([
            ("release blocked", _ans("The release is not blocked; all P1 defects are closed [1].",
                                     [_cit("d1", "p1")])),
            ("escalation path", _ans("Escalate to the QA lead [1].", [_cit("d2", "p2")])),
            ("signs off", _ans("The release manager signs off [1].", [_cit("d3", "p3")])),
        ])
        r = R.execute(R.plan(self.Q), ask)
        s1, s2, s3, _ = r["steps"]
        self.assertIs(s1["kind_result"]["condition"], False)
        self.assertTrue(s1["kind_result"]["negated"])
        self.assertTrue(s2["skipped"])
        self.assertFalse(s3["skipped"])
        self.assertIn("does not hold", r["final_text"])
        self.assertIn("The release manager signs off [2].", r["final_text"])
        self.assertNotIn("Escalate", r["final_text"])

    def test_false_without_otherwise_branch(self):
        ask = _router([("P1 defect open", _ans("No P1 defect is open on the current build [1].",
                                               [_cit("d1", "p1")]))])
        r = R.execute(R.plan("If a P1 defect is open, what is the escalation path?"), ask)
        self.assertEqual(r["kind"], "answer")
        self.assertIs(r["steps"][0]["kind_result"]["condition"], False)
        self.assertTrue(r["steps"][1]["skipped"])
        self.assertIn("No alternative branch was given", r["final_text"])
        self.assertEqual(len(ask.calls), 1)

    def test_undecidable_condition_becomes_clarify(self):
        ask = _router([("release blocked", _ans("Regression suites run nightly [1].", [_cit("d1", "p1")]))],
                      default=_ans("x [1].", [_cit("d9", "p9")]))
        r = R.execute(R.plan(self.Q), ask)
        self.assertEqual(r["kind"], "clarify")
        self.assertIsNone(r["steps"][0]["kind_result"]["condition"])
        self.assertTrue(r["steps"][1]["skipped"] and r["steps"][2]["skipped"])
        self.assertIn("could not decide whether “the release is blocked” holds", r["final_text"])
        self.assertIn("Please confirm", r["clarify_back"])
        self.assertEqual(len(ask.calls), 1)

    def test_low_grounding_is_undecidable(self):
        ask = _router([("release blocked", _ans("The release is blocked [1].", [_cit("d1", "p1")], g=0.3))],
                      default=_ans("x [1].", [_cit("d9", "p9")]))
        r = R.execute(R.plan(self.Q), ask)
        self.assertEqual(r["kind"], "clarify")
        self.assertIn("below threshold", r["steps"][0]["kind_result"]["reason"])

    def test_gap_on_condition(self):
        r = R.execute(R.plan(self.Q), _router([]))
        self.assertEqual(r["kind"], "gap")
        self.assertEqual(r["gap_step"], "s1")
        self.assertIn("Step 1 (“Is the release blocked?”) lacked evidence", r["final_text"])

    def test_negated_condition_clause_polarity(self):
        # condition "the build is not signed" + evidence "builds are not signed" → both negated → True
        ask = _router([("build not signed", _ans("Nightly builds are not signed [1].", [_cit("d1", "p1")])),
                       ("release proceed", _ans("The release does not proceed without a signed build [1].",
                                                [_cit("d2", "p2")]))])
        r = R.execute(R.plan("When the build is not signed, does the release proceed?"), ask)
        self.assertIs(r["steps"][0]["kind_result"]["condition"], True)
        self.assertIn("The release does not proceed", r["final_text"])


class ExecuteCompare(unittest.TestCase):
    def test_compare_side_by_side(self):
        ask = _router([
            ("smoke testing", _ans("Smoke testing runs a short suite on every build [1].", [_cit("d1", "p1")], conf=0.8)),
            ("regression testing", _ans("Regression testing runs the full suite nightly [1].", [_cit("d2", "p2")], conf=0.6)),
        ])
        r = R.execute(R.plan("What is the difference between smoke testing and regression testing?"), ask)
        self.assertEqual(r["kind"], "answer")
        self.assertEqual(ask.calls, ["What is smoke testing?", "What is regression testing?"])
        self.assertEqual(r["final_text"],
                         "Smoke testing: Smoke testing runs a short suite on every build [1]. "
                         "Regression testing: Regression testing runs the full suite nightly [2].")
        kr = r["steps"][2]["kind_result"]
        self.assertIn("suite", kr["shared_terms"])
        self.assertIn("short", kr["only_a"]); self.assertIn("nightly", kr["only_b"])
        self.assertTrue(kr["both_answered"])
        self.assertEqual(r["confidence"], 0.6)
        self.assertEqual(r["complexity"], "complex")

    def test_compare_with_missing_side_is_gap(self):
        ask = _router([("smoke testing", _ans("Smoke runs on every build [1].", [_cit("d1", "p1")]))])
        r = R.execute(R.plan("Compare smoke testing and regression testing"), ask)
        self.assertEqual(r["kind"], "gap")
        self.assertEqual(r["gap_step"], "s2")


class ComplexityAndExplain(unittest.TestCase):
    def test_labels(self):
        self.assertEqual(R.complexity("What is the smoke test?"), "simple")
        self.assertEqual(R.complexity("Why does the regression suite fail?"), "medium")
        self.assertEqual(R.complexity("What are the entry criteria and what are the exit criteria?"), "medium")
        self.assertEqual(R.complexity("List the P1 defects, then who approves the release, after that what is the SLA?"),
                         "complex")
        self.assertEqual(R.complexity("If the release is blocked, what is the escalation path?"), "complex")
        self.assertEqual(R.complexity("Compare smoke testing and regression testing"), "complex")
        self.assertEqual(R.complexity("Why does the regression suite fail and how is it fixed?"), "complex")

    def test_long_question_earns_a_point(self):
        long_q = "What is the exact procedure we follow for the smoke test of a mobile build on the shared device farm?"
        self.assertGreaterEqual(R.plan(long_q)["signals"]["words"], R.LONG_WORDS)
        self.assertEqual(R.complexity(long_q), "medium")

    def test_complexity_uses_given_plan(self):
        p = R.plan("Compare smoke testing and regression testing")
        self.assertEqual(R.complexity("ignored", p), "complex")
        self.assertEqual(R.complexity("ignored", {"mode": "single", "steps": [{"kind": "lookup"}]}), "simple")

    def test_explain_one_sentence_per_mode(self):
        for q, word in (("What is the smoke test?", "Single lookup"),
                        ("What is X and what is Y?", "Multistep"),
                        ("If the release is blocked, what is the escalation path?", "Conditional"),
                        ("Compare smoke testing and regression testing", "Comparison")):
            e = R.explain(R.plan(q))
            self.assertTrue(e.startswith(word), e)
            self.assertEqual(e.count(". "), 0, e)   # one sentence

    def test_to_surface_shape(self):
        ask = _router([("smoke", _ans("Smoke runs on every build [1].", [_cit("d3", "p3")]))])
        surf = R.to_surface(R.execute(R.plan("What is the smoke test?"), ask))
        self.assertEqual(set(surf["steps"][0]), {"id", "question", "kind", "answer_text", "grounding",
                                                 "condition", "skipped", "reason"})
        json.dumps(surf)


if __name__ == "__main__":
    unittest.main()
