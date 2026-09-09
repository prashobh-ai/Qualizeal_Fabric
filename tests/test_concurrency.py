"""Concurrency correctness (Runbook Section 5): no cross-tenant leakage,
no request-context bleed, unique traces — the footgun tests."""
import threading
import unittest

from knowledge_fabric.answer.service import AnswerService
from knowledge_fabric.tenants import demo
from tests.util import seeded


class TestConcurrency(unittest.TestCase):
    def setUp(self):
        self.p = seeded(["qualizeal", "isolation-check"])
        self.svc = AnswerService(self.p)

    def test_mixed_tenant_no_leakage_and_unique_traces(self):
        principals = [
            (demo.principal_for(self.p, "qualizeal", "asker.public"),
             "what must a release achieve before promotion?", "qualizeal"),
            (demo.principal_for(self.p, "isolation-check", "asker.public"),
             "what is required before boarding begins?", "isolation-check"),
        ]
        results = []
        lock = threading.Lock()

        def fire(idx):
            prin, q, tenant = principals[idx % 2]
            a = self.svc.ask(prin, q)
            leaked = False
            for c in a.citations:
                d = self.p.documents.get(tenant, c.document_id)
                if d is None:      # citation not resolvable within this tenant => leak
                    leaked = True
            with lock:
                results.append((tenant, a.trajectory_id, leaked, a.kind.value))

        threads = [threading.Thread(target=fire, args=(i,)) for i in range(60)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        self.assertEqual(len(results), 60)
        self.assertFalse(any(r[2] for r in results), "cross-tenant citation leak detected")
        traj_ids = [r[1] for r in results]
        self.assertEqual(len(set(traj_ids)), len(traj_ids), "trace ids must be unique per request")

    def test_audit_subject_matches_requester_under_load(self):
        prins = [demo.principal_for(self.p, "qualizeal", u)
                 for u in ("asker.public", "curator")]
        out = []

        def fire(i):
            prin = prins[i % 2]
            a = self.svc.ask(prin, "coverage acceptance criteria")
            rows = self.p.audit.for_trace("qualizeal", a.trajectory_id)
            out.append((prin.subject, rows[0]["subject"] if rows else None))

        threads = [threading.Thread(target=fire, args=(i,)) for i in range(40)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        for expected, got in out:
            self.assertEqual(expected, got, "request context bled across threads")


if __name__ == "__main__":
    unittest.main()
