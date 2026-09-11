"""T51 — the real-physics galaxy payload builder.

Exercises knowledge_fabric/health/galaxy.py over a small in-memory fabric with
a hand-built graph and one recorded answer trace, then smoke-checks the browser
module surfaces/static/vendor/galaxy.js (it must parse under node, define
KFGalaxy, and carry no external URLs).
"""

from __future__ import annotations

import os
import shutil
import subprocess
import unittest

os.environ["KF_MODEL_MODE"] = "extractive"

from knowledge_fabric.app import Platform  # noqa: E402
from knowledge_fabric.contracts.types import (  # noqa: E402
    Coordinate,
    CoordinateKind,
    Document,
    GraphEdge,
    GraphNode,
    Passage,
    Provenance,
    now_ms,
)
from knowledge_fabric.health import galaxy  # noqa: E402

TENANT = "test-galaxy"
TRACE = "trace_galaxy_1"

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
GALAXY_JS = os.path.join(
    REPO_ROOT, "knowledge_fabric", "surfaces", "static", "vendor", "galaxy.js"
)

_BUCKETS = {"Concept", "Product", "Service", "Repository", "Person"}


def _node(nid, key, type_):
    return GraphNode(id=nid, tenant=TENANT, canonical_key=key, type=type_, labels=[key])


def _edge(eid, src, dst, relation, weight):
    return GraphEdge(id=eid, tenant=TENANT, src=src, dst=dst, relation=relation, weight=weight)


def _passage(pid, doc_id, text):
    return Passage(
        id=pid,
        tenant=TENANT,
        document_id=doc_id,
        text=text,
        abstract=text[:40],
        overview=text,
        coordinate=Coordinate(CoordinateKind.PAGE_PARAGRAPH, {"page": 1, "paragraph": 1}),
        provenance=Provenance("hash_" + pid, "files", "v1"),
    )


def _fabric_with_graph():
    """A fresh platform seeded with a six-node graph, one passage and one
    recorded answer span whose trajectory lights three of the nodes."""
    p = Platform(db_path=":memory:", blob_root="./data/test-blobs")
    p.policy.set_budget(TENANT, 5.0)

    for node in [
        _node("n_req", "requirement", "Requirement"),
        _node("n_tc", "test case", "TestCase"),
        _node("n_rel", "release", "Release"),
        _node("n_def", "defect", "Defect"),
        _node("n_repo", "kf-platform repository", "Repository"),
        _node("n_person", "release owner", "Person"),
    ]:
        p.graph_repo.upsert_node(node)

    for edge in [
        _edge("e1", "n_req", "n_tc", "verifies", 0.9),
        _edge("e2", "n_tc", "n_rel", "covers", 0.8),
        _edge("e3", "n_def", "n_rel", "blocks", 0.7),
        _edge("e4", "n_req", "n_repo", "depends_on", 0.5),
        _edge("e5", "n_person", "n_req", "owns", 0.0),
    ]:
        p.graph_repo.upsert_edge(edge)

    p.documents.upsert(
        Document(
            id="d_strategy",
            tenant=TENANT,
            source="files",
            source_version="v1",
            content_hash="hash_doc",
            type="text",
            language="en",
            title="Test Strategy",
            uri="file://qa/test-strategy.md",
            ingested_at=now_ms(),
        )
    )
    pas = _passage(
        "pas_1",
        "d_strategy",
        "Every requirement is verified by a test case before the release.",
    )
    p.passages.add(pas, 1, ["public"])

    # the answer span the galaxy activates from: two passage-text hits
    # (requirement, test case) plus one graph-expansion key (release).
    p.telemetry.record(
        "answer",
        {
            "trace_id": TRACE,
            "tenant": TENANT,
            "name": "answer",
            "trajectory": {"selected": ["pas_1"], "graph_node_keys": ["release"]},
            "sources": [{"document_id": "d_strategy"}],
        },
    )
    return p


class TestGalaxyBuilder(unittest.TestCase):
    def setUp(self):
        self.p = _fabric_with_graph()

    def test_payload_shape_and_activation(self):
        pl = galaxy.build_payload(self.p, TENANT, TRACE)

        # nodes carry deg / type / docs
        self.assertEqual(len(pl["nodes"]), 6)
        by_id = {n["id"]: n for n in pl["nodes"]}
        for n in pl["nodes"]:
            self.assertIn("deg", n)
            self.assertIn("type", n)
            self.assertIn("docs", n)
            self.assertIsInstance(n["deg"], int)
            self.assertIsInstance(n["docs"], int)
            self.assertIn(n["type"], _BUCKETS)

        # degree from the tenant graph
        self.assertEqual(by_id["n_req"]["deg"], 3)
        self.assertEqual(by_id["n_tc"]["deg"], 2)

        # type mapping onto the five buckets
        self.assertEqual(by_id["n_repo"]["type"], "Repository")
        self.assertEqual(by_id["n_person"]["type"], "Person")
        self.assertEqual(by_id["n_rel"]["type"], "Product")  # "release" hint
        self.assertEqual(by_id["n_req"]["type"], "Concept")  # unknown -> Concept

        # docs: the passage mentions requirement + test case
        self.assertGreaterEqual(by_id["n_req"]["docs"], 1)
        self.assertGreaterEqual(by_id["n_tc"]["docs"], 1)

        # edges carry relation + weight; a 0-weight edge defaults to 1.0
        by_edge = {(e["from"], e["to"]): e for e in pl["edges"]}
        self.assertEqual(len(pl["edges"]), 5)
        for e in pl["edges"]:
            self.assertIn("relation", e)
            self.assertIn("weight", e)
            self.assertTrue(e["relation"])
        self.assertEqual(by_edge[("n_req", "n_tc")]["weight"], 0.9)
        self.assertEqual(by_edge[("n_person", "n_req")]["weight"], 1.0)

        # activation is exactly the trace's concept set
        activated = set(pl["activated_ids"])
        self.assertEqual(activated, {"n_req", "n_tc", "n_rel"})
        self.assertLessEqual(activated, {n["id"] for n in pl["nodes"]})

        # halo is one hop from an activated node and disjoint from it
        halo = set(pl["halo_ids"])
        self.assertEqual(halo, {"n_repo", "n_person", "n_def"})
        self.assertTrue(halo.isdisjoint(activated))

        # stats
        self.assertEqual(pl["stats"]["nodes"], 6)
        self.assertEqual(pl["stats"]["edges"], 5)
        self.assertEqual(pl["stats"]["activated"], 3)

    def test_empty_state_and_top_concepts(self):
        # no trace -> no activation, graph still present
        none_pl = galaxy.build_payload(self.p, TENANT, None)
        self.assertEqual(none_pl["activated_ids"], [])
        self.assertEqual(none_pl["halo_ids"], [])
        self.assertEqual(len(none_pl["nodes"]), 6)

        # an unknown trace must not raise and must not activate anything
        unknown_pl = galaxy.build_payload(self.p, TENANT, "does-not-exist")
        self.assertEqual(unknown_pl["activated_ids"], [])

        # top concepts rank by degree
        tops = galaxy.top_concepts(self.p, TENANT)
        self.assertTrue(tops)
        self.assertEqual(tops[0]["id"], "n_req")
        self.assertEqual(tops[0]["deg"], 3)
        for row in tops:
            self.assertIn(row["type"], _BUCKETS)
            self.assertIn("docs", row)

    def test_node_detail(self):
        detail = galaxy.node_detail(self.p, TENANT, "n_req")
        self.assertIsNotNone(detail)
        self.assertEqual(detail["id"], "n_req")
        self.assertEqual(detail["name"], "requirement")
        self.assertIn(detail["type"], _BUCKETS)
        self.assertGreaterEqual(detail["docs"], 1)
        self.assertTrue(detail["passages"])
        first = detail["passages"][0]
        for k in ("text", "doc_id", "uri"):
            self.assertIn(k, first)
        self.assertEqual(first["uri"], "file://qa/test-strategy.md")

        self.assertIsNone(galaxy.node_detail(self.p, TENANT, "not-a-node"))

    def test_no_graph_is_tolerated(self):
        p = Platform(db_path=":memory:", blob_root="./data/test-blobs")
        p.policy.set_budget("empty-tenant", 5.0)
        pl = galaxy.build_payload(p, "empty-tenant", TRACE)
        self.assertEqual(pl["nodes"], [])
        self.assertEqual(pl["edges"], [])
        self.assertEqual(pl["activated_ids"], [])
        self.assertEqual(pl["halo_ids"], [])
        self.assertEqual(pl["stats"]["nodes"], 0)
        self.assertEqual(pl["stats"]["edges"], 0)
        self.assertEqual(pl["stats"]["activated"], 0)
        self.assertEqual(galaxy.top_concepts(p, "empty-tenant"), [])
        self.assertIsNone(galaxy.node_detail(p, "empty-tenant", "n_req"))


class TestGalaxyJs(unittest.TestCase):
    def setUp(self):
        with open(GALAXY_JS, encoding="utf-8") as fh:
            self.src = fh.read()

    def test_parses_under_node(self):
        node = shutil.which("node")
        if not node:
            self.skipTest("node runtime not available")
        # new Function(src) throws on a syntax error but never runs the IIFE.
        proc = subprocess.run(
            [
                node,
                "-e",
                "var fs=require('fs');new Function(fs.readFileSync(process.argv[1],'utf8'));",
                GALAXY_JS,
            ],
            capture_output=True,
            text=True,
        )
        self.assertEqual(proc.returncode, 0, proc.stderr)

    def test_defines_module_and_tokens(self):
        self.assertIn("KFGalaxy", self.src)
        self.assertIn("barnesHut", self.src)
        self.assertIn("#F53E5A", self.src)
        self.assertIn("easeInOutQuad", self.src)

    def test_no_external_urls(self):
        self.assertNotIn("http://", self.src)
        self.assertNotIn("https://", self.src)


if __name__ == "__main__":
    unittest.main()
