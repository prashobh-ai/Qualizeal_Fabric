"""Shared test helpers.

The product `seed()` ships no documents (L0.2); documents enter a fabric
only via a corpus load. Tests get a populated fabric by loading the
synthetic corpus fixture into a **test-only** fabric id (`test-fabric` by
default). The synthetic corpus never touches the `qualizeal` product
fabric.
"""

import os

from knowledge_fabric.app import Platform
from knowledge_fabric.tenants import demo
from tests.fixtures import synthetic_corpus

# Default test fabric id — a test-only fabric, never the product `qualizeal`.
T = synthetic_corpus.TEST_FABRIC


def seeded(tenants=None, model_mode="mock"):
    """Return an in-memory Platform with the synthetic corpus loaded into each
    requested tenant. Defaults to a single `test-fabric`."""
    os.environ["KF_MODEL_MODE"] = model_mode
    p = Platform(db_path=":memory:", blob_root="./data/test-blobs")
    # Product fabric config (budget, users) — no documents.
    demo.seed(p)
    tenants = list(tenants or [T])
    # The first tenant gets the full corpus + bank + connectors; any further
    # tenants get a minimal isolation corpus (no connectors) so cross-tenant
    # isolation tests see a clean second fabric.
    for i, tenant in enumerate(tenants):
        if i == 0:
            synthetic_corpus.load_into(p, tenant)
        else:
            synthetic_corpus.load_isolation(p, tenant)
    return p
