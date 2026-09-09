"""Shared test helpers.

`seeded()` builds an in-memory Platform seeded with the single product
tenant (`qualizeal`) plus, on request, a bare-minimum second tenant used
solely to exercise the tenant-guard mechanism (invariant I5). The second
tenant is never user-visible — it exists only inside these test fixtures.
"""
import os

from knowledge_fabric.app import Platform
from knowledge_fabric.contracts.types import Principal
from knowledge_fabric.ingestion.intake import Intake, IngestWorker
from knowledge_fabric.tenants import demo


def _seed_isolation_tenant(p: Platform, tenant: str) -> None:
    """Seed a bare corpus in a second tenant to exercise I5 isolation."""
    p.policy.set_budget(tenant, 5.0)
    intake = Intake(p)
    worker = IngestWorker(p, intake)
    # A short public and a short restricted doc — enough for the ACL and
    # authority/versioning tests to have something to select.
    intake.submit(intake.canonical(
        tenant, "files", "file://ops/turnaround.md", "Aircraft Turnaround Procedure",
        b"# Turnaround Procedure\n\nGround crew requires a completed walkaround inspection "
        b"before boarding begins. The procedure complies with the operator's airworthiness "
        b"maintenance program.\n",
        mime="text/markdown", acl=["public"], ontology="aviation-ops"))
    intake.submit(intake.canonical(
        tenant, "files", "file://ops/inspection-log.csv", "Daily Inspection Log",
        b"aircraft,system,check,result\nNW-101,hydraulics,pre-flight,pass\n"
        b"NW-102,hydraulics,pre-flight,defer\n",
        mime="text/csv", acl=["public"], ontology="aviation-ops"))
    worker.drain()
    # No DEMO_USERS mutation: demo.principal_for() falls back to the role
    # template for tenants outside the shipped directory (test-only path).


def seeded(tenants=None, model_mode="mock"):
    os.environ["KF_MODEL_MODE"] = model_mode
    p = Platform(db_path=":memory:", blob_root="./data/test-blobs")
    tenants = tenants or ["qualizeal", "isolation-check"]
    # Seed the product tenant(s) that live in DEMO_TENANTS via the normal path;
    # anything else is a test-only isolation fixture.
    product = [t for t in tenants if any(cfg.tenant == t for cfg in demo.DEMO_TENANTS)]
    demo.seed(p, product)
    for t in tenants:
        if t not in product:
            _seed_isolation_tenant(p, t)
    return p
