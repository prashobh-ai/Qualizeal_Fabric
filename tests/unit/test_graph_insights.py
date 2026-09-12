"""Unit tests for graph insights (T57) and the two-step ingest outline (T58).

The graph is built directly as plain node/edge dicts (two dense triangle
clusters joined by a bridge, plus a deliberately sparse star) so the
signal maths can be asserted by hand without spinning up a full platform.
"""

from __future__ import annotations

import math

from knowledge_fabric.health import graph_insights as gi


def _node(nid: str, type_: str = "Concept", key: str | None = None) -> dict:
    return {"id": nid, "type": type_, "canonical_key": key or nid, "labels": [key or nid]}


def _edge(src: str, dst: str, relation: str = "relates_to") -> dict:
    return {"src": src, "dst": dst, "relation": relation}


def _build_graph():
    """Two triangles (A, B) + bridge + a 14-node sparse star component."""
    nodes = [
        _node("a1", key="Alpha"),
        _node("a2", key="Beta"),
        _node("a3", key="Gamma"),
        _node("b1", key="Delta"),
        _node("b2", key="Epsilon"),
        _node("b3", key="Zeta"),
        _node("bridge", key="Bridge"),
    ]
    edges = [
        _edge("a1", "a2"),
        _edge("a2", "a3"),
        _edge("a1", "a3"),
        _edge("b1", "b2"),
        _edge("b2", "b3"),
        _edge("b1", "b3"),
        _edge("bridge", "a1"),
        _edge("bridge", "b1"),
    ]
    # Sparse star: hub s0 + 13 leaves, its own connected component.
    nodes.append(_node("s0", key="Sparse Hub"))
    for i in range(1, 14):
        nodes.append(_node(f"s{i}", key=f"Leaf {i}"))
        edges.append(_edge("s0", f"s{i}"))

    docs_by_node = {
        "a1": {"docA"},  # single source, degree 3 -> gap
        "a2": {"docA", "docA2", "docA3"},
        "a3": {"docA", "docA2", "docA3"},
        "b1": {"docB"},
        "b2": {"docB"},
        "b3": {"docB"},
        "bridge": {"docA", "docB"},
    }
    for i in range(14):
        docs_by_node[f"s{i}"] = {"docS"}
    return nodes, edges, docs_by_node


def test_type_affinity_matrix():
    assert gi.type_affinity("Product", "Service") == 1.0
    assert gi.type_affinity("Concept", "Concept") == 0.5
    assert gi.type_affinity("Person", "Product") == 0.15
    assert gi.type_affinity("Person", "Person") == 0.15
    assert gi.type_affinity("Product", "Concept") == 0.4


def test_adamic_adar_hand_case():
    # a1 and b1 share exactly one neighbour, "bridge", whose degree is 2.
    nodes, edges, _ = _build_graph()
    adj = gi._adjacency(edges)
    assert adj["bridge"] == {"a1", "b1"}
    assert gi.adamic_adar(adj, "a1", "b1") == 1.0 / math.log(2)

    # Two shared neighbours, each of degree 2 -> 2 * 1/log(2).
    diamond = gi._adjacency([_edge("p", "q"), _edge("p", "r"), _edge("s", "q"), _edge("s", "r")])
    assert gi.adamic_adar(diamond, "p", "s") == 2.0 / math.log(2)

    # No shared neighbours -> zero.
    assert gi.adamic_adar(adj, "a2", "b2") == 0.0


def test_communities_and_cohesion():
    nodes, edges, _ = _build_graph()
    comm = gi.communities(nodes, edges)

    # Every node is assigned and cohesion is a valid fraction.
    assert set(comm) == {n["id"] for n in nodes}
    community_ids = {info["community"] for info in comm.values()}
    assert len(community_ids) >= 2
    for info in comm.values():
        assert 0.0 <= info["cohesion"] <= 1.0

    # The dense triangle B stays together and reads as cohesive. Louvain may
    # fold the bridge node into the community, so assert "dense", not exactly 1.
    assert comm["b1"]["community"] == comm["b2"]["community"] == comm["b3"]["community"]
    assert comm["b1"]["cohesion"] >= 0.6

    # The sparse star is flagged low-cohesion (14 nodes, cohesion ~= 0.143).
    assert comm["s0"].get("flag") == "low-cohesion"
    assert comm["s0"]["cohesion"] < gi._LOW_COHESION
    # Dense clusters are not flagged.
    assert "flag" not in comm["b1"]


def test_relevance_edges_blend_ranks_related_over_unrelated():
    nodes, edges, docs = _build_graph()
    ranked = gi.relevance_edges(nodes, edges, docs, limit=500)
    by_pair = {frozenset((r["a"], r["b"])): r for r in ranked}

    related = by_pair[frozenset(("a2", "a3"))]  # direct + shared docs + shared nbr
    weak = by_pair[frozenset(("a1", "b1"))]  # only a shared neighbour, no docs/link

    assert related["signals"]["direct"] == 1.0
    assert related["signals"]["source_overlap"] == 1.0
    assert weak["signals"]["direct"] == 0.0
    assert weak["signals"]["source_overlap"] == 0.0
    assert related["weight"] > weak["weight"]

    # Adamic-Adar recorded on the pair matches the hand computation.
    assert weak["signals"]["adamic_adar"] == round(1.0 / math.log(2), 6)


def test_surprising_connections_finds_cross_community_bridge():
    nodes, edges, _ = _build_graph()
    comm = gi.communities(nodes, edges)
    surprising = gi.surprising_connections(nodes, edges, comm)

    pairs = {frozenset((s["a"], s["b"])) for s in surprising}
    assert frozenset(("a1", "b1")) in pairs

    top = surprising[0]
    # The bridge pair has the highest Adamic-Adar and no direct link.
    assert frozenset((top["a"], top["b"])) == frozenset(("a1", "b1"))
    assert comm[top["a"]]["community"] != comm[top["b"]]["community"]
    # Directly-linked pairs never appear.
    assert frozenset(("a1", "a2")) not in pairs


def test_knowledge_gaps_flags_single_source_high_degree():
    nodes, edges, docs = _build_graph()
    gaps = gi.knowledge_gaps(nodes, edges, docs)

    single = [g for g in gaps if g["kind"] == "single-source-concept"]
    labels = {g["label"] for g in single}
    assert "Alpha" in labels  # a1: degree 3, one source document
    alpha = next(g for g in single if g["label"] == "Alpha")
    assert alpha["suggest_tags"] == ["Alpha"]

    # The sparse star (many concepts, one doc) is a thin community.
    thin = [g for g in gaps if g["kind"] == "thin-community"]
    assert thin
    assert all(g["suggest_tags"] for g in thin)


def test_outline_extracts_headings_entities_claims():
    text = "# Head\nAcme ships Widgets. Widgets rely on the Gadget platform."
    result = gi.outline(text)

    assert "Head" in result["headings"]
    assert "Acme" in result["entities"]
    assert "Widgets" in result["entities"]
    # Sentence-opening stopwords are not treated as entities.
    assert "The" not in result["entities"]

    assert len(result["claims"]) >= 2
    first = result["claims"][0]
    assert first["text"].startswith("Acme ships Widgets")
    assert "source_hint" in first
    assert all("source_hint" in c and c["text"] for c in result["claims"])


def test_insights_payload_from_platform():
    from tests.util import T, seeded

    p = seeded(model_mode="extractive")
    payload = gi.insights(p, T)

    assert set(payload) == {"communities", "surprising", "gaps"}
    assert isinstance(payload["communities"], list)
    assert isinstance(payload["surprising"], list)
    assert isinstance(payload["gaps"], list)
    for c in payload["communities"]:
        assert 0.0 <= c["cohesion"] <= 1.0
        assert c["size"] >= 1
