"""Corpus policy (T01 stub; full policy lands at T05).

The corpus policy is one of the invariants every CI job enforces: nothing
synthetic ever reaches the product fabric, and identifiers in test fixtures are
drawn only from ranges the issuing authorities reserve for documentation, so a
fixture can never resolve to a real entity.

This module is the CI hook named in ``.github/workflows/ci.yml``. It asserts
the two properties that already hold today; T05 extends it to the real
web-scrape corpus (source allow-list, per-source licence, no invented data).
"""

from __future__ import annotations

from tests.fixtures import synthetic_corpus as sc

# The single product fabric; synthetic fixtures must never target it.
PRODUCT_FABRIC = "qualizeal"


def test_synthetic_corpus_is_confined_to_a_test_fabric():
    assert sc.TEST_FABRIC != PRODUCT_FABRIC
    assert sc.TEST_FABRIC == "test-fabric"


def test_fixture_identifiers_are_documentation_safe():
    # validate_identifiers() returns the list of identifiers that could resolve
    # to a real entity; the fixture corpus must yield none.
    assert sc.validate_identifiers() == []
