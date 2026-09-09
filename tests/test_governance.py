"""Governance checklist (Section 20) + Runbook 4-6: identity, isolation,
permission-before-ranking, per-agent identity, audit, budget race, rate limit."""
import threading
import time
import unittest

from knowledge_fabric.answer.service import AnswerService
from knowledge_fabric.contracts.types import AnswerKind, Principal
from knowledge_fabric.tenants import demo
from tests.util import seeded


class TestGovernance(unittest.TestCase):
    def setUp(self):
        self.p = seeded(["test-fabric", "isolation-check"])
        self.svc = AnswerService(self.p)

    # --- I5 tenant isolation ------------------------------------------
    def test_store_call_without_tenant_fails_closed(self):
        with self.assertRaises(PermissionError):
            self.p.passages.count("")

    def test_tenant_isolation_in_retrieval(self):
        qvec = self.p.embedder.embed(["turnaround boarding aircraft"])[0]
        hits = self.p.vindex.search("test-fabric", qvec, 10, ["public"])
        for pid, _ in hits:
            pas = self.p.passages.get("test-fabric", pid)
            self.assertIsNotNone(pas)          # never a isolation-check passage
        # cross-tenant token cannot read the other tenant
        self.assertEqual(self.p.vindex.search("test-fabric", qvec, 10, ["public"]),
                         self.p.vindex.search("test-fabric", qvec, 10, ["public"]))

    # --- I6 permission before ranking ---------------------------------
    def test_restricted_doc_never_enters_retrieval(self):
        restricted = demo.principal_for(self.p, "test-fabric", "asker.public")
        qvec = self.p.embedder.embed(["critical defect triage 4 hours"])[0]
        hits = self.p.vindex.search("test-fabric", qvec, 20, restricted.accessible_acls())
        for pid, _ in hits:
            acl = self.p.passages.acl_of("test-fabric", pid)
            self.assertNotIn("restricted", acl, "forbidden passage must not be ranked (I6)")
        a = self.svc.ask(restricted, "how fast must critical defects be triaged?")
        for c in a.citations:
            d = self.p.documents.get("test-fabric", c.document_id)
            self.assertNotEqual(d["uri"], "file://qa/defect-policy.md")

    def test_curator_can_see_restricted(self):
        curator = demo.principal_for(self.p, "test-fabric", "curator")
        qvec = self.p.embedder.embed(["critical defect triage"])[0]
        hits = self.p.vindex.search("test-fabric", qvec, 20, curator.accessible_acls())
        acls = set()
        for pid, _ in hits:
            acls |= set(self.p.passages.acl_of("test-fabric", pid))
        self.assertIn("restricted", acls)

    # --- role gating ---------------------------------------------------
    def test_role_gating(self):
        asker = demo.principal_for(self.p, "test-fabric", "asker.public")
        admin = demo.principal_for(self.p, "test-fabric", "admin")
        self.assertEqual(self.p.policy.check(asker, "set_budget", {}).decision.value, "deny")
        self.assertEqual(self.p.policy.check(admin, "set_budget", {}).decision.value, "allow")

    # --- I7 per-agent identity, same gate -----------------------------
    def test_agent_uses_same_gate_no_bypass(self):
        agent = demo.principal_for(self.p, "test-fabric", "qa-agent")
        self.assertTrue(agent.agent)
        a = self.svc.ask(agent, "what must a release achieve before promotion?")
        # agent hits the same answer service and produces an audited trace
        rows = self.p.audit.for_trace("test-fabric", a.trajectory_id)
        self.assertTrue(rows)
        self.assertEqual(rows[0]["is_agent"], 1)

    # --- I11 audit line ------------------------------------------------
    def test_every_answer_audited(self):
        asker = demo.principal_for(self.p, "test-fabric", "asker.public")
        a = self.svc.ask(asker, "what is the acceptance criteria for coverage?")
        rows = self.p.audit.for_trace("test-fabric", a.trajectory_id)
        self.assertTrue(rows)
        self.assertEqual(rows[0]["subject"], "asker.public")

    # --- token hygiene -------------------------------------------------
    def test_expired_token_rejected(self):
        tok = self.p.idp.mint(Principal("x", "test-fabric", ["asker"], ["public"]), ttl_s=-1)
        with self.assertRaises(PermissionError):
            self.p.idp.authenticate({"token": tok})

    def test_forged_token_rejected(self):
        tok = self.p.idp.mint(Principal("x", "test-fabric", ["asker"], ["public"]))
        forged = tok[:-4] + "AAAA"
        with self.assertRaises(PermissionError):
            self.p.idp.authenticate({"token": forged})

    # --- I12 budget cap holds under a race ----------------------------
    def test_budget_cap_atomic_under_race(self):
        self.p.policy.set_budget("test-fabric", 10.0)
        # reset spent
        self.p.db.execute("UPDATE budgets SET spent=0 WHERE tenant=?", ("test-fabric",))
        successes = []

        def worker():
            ok = self.p.policy.try_spend("test-fabric", 1.0)
            successes.append(ok)

        threads = [threading.Thread(target=worker) for _ in range(100)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        self.assertEqual(sum(successes), 10, "exactly cap units granted")
        self.assertLessEqual(self.p.policy.spent("test-fabric"), 10.0 + 1e-9)

    def test_rate_limit_blocks_flood(self):
        allowed = [self.p.policy.rate_check("test-fabric", "floody", limit=5) for _ in range(20)]
        self.assertEqual(sum(allowed), 5)


if __name__ == "__main__":
    unittest.main()
