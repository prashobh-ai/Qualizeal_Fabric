"""Graph insights over the tenant knowledge graph (T57) and a deterministic
two-step-ingest analyse outline (T58).

Concept reference: the read-outs here are reimplemented in our own code from
the *ideas* described in the ``llm_wiki`` project (GPL-3.0) — four-signal
relevance edges, community cohesion, surprising cross-domain links and gap
surfacing. No source was copied; every algorithm below is written from the
description only.

Everything is pure/stdlib apart from an optional ``networkx`` used for Louvain
community detection. When it is missing we fall back to connected components so
the module keeps working offline. Set ``KF_MODEL_MODE=extractive`` — nothing
here needs a model; ``outline`` is the deterministic analyse step and the
integrator wires the constrained generate step into the summary path (T38).
"""

from __future__ import annotations

import json
import math
import re
from collections import defaultdict
from itertools import combinations
from typing import Any

try:  # networkx is optional; guard so the module imports without it.
    import networkx as nx

    _HAVE_NX = True
except Exception:  # pragma: no cover - only hit when networkx is absent
    nx = None
    _HAVE_NX = False


# --------------------------------------------------------------------------
# Blend weights for the four relevance signals (documented, sum to 1.0).
# --------------------------------------------------------------------------
SIGNAL_WEIGHTS: dict[str, float] = {
    "direct": 0.40,  # a stated relation edge exists between the pair
    "source_overlap": 0.25,  # Jaccard over the documents mentioning each
    "adamic_adar": 0.20,  # shared-neighbour structural similarity
    "type_affinity": 0.15,  # fixed node-type affinity matrix
}

# Degree/document thresholds used by the gap and surprise read-outs.
_HIGH_DEGREE = 3
_LOW_COHESION = 0.15
_MIN_FLAG_SIZE = 3
_THIN_COMMUNITY_DOCS = 1


def _get(obj: Any, key: str, default: Any = None) -> Any:
    """Read ``key`` from either a mapping or an attribute object.

    Lets every function accept plain dicts or the ``GraphNode``/``GraphEdge``
    dataclasses interchangeably.
    """
    if isinstance(obj, dict):
        return obj.get(key, default)
    return getattr(obj, key, default)


# --------------------------------------------------------------------------
# Structural helpers
# --------------------------------------------------------------------------
def _adjacency(edges) -> dict[str, set[str]]:
    """Undirected neighbour sets over every edge (co-occurrence included)."""
    adj: dict[str, set[str]] = defaultdict(set)
    for e in edges:
        s, d = _get(e, "src"), _get(e, "dst")
        if not s or not d or s == d:
            continue
        adj[s].add(d)
        adj[d].add(s)
    return adj


def _stated_pairs(edges) -> set[frozenset[str]]:
    """Pairs joined by a *stated* relation (contextual ``co_occurs`` excluded)."""
    pairs: set[frozenset[str]] = set()
    for e in edges:
        s, d = _get(e, "src"), _get(e, "dst")
        if not s or not d or s == d:
            continue
        if _get(e, "relation") == "co_occurs":
            continue
        pairs.add(frozenset((s, d)))
    return pairs


def type_affinity(type_a: str | None, type_b: str | None) -> float:
    """Fixed affinity matrix between two node types.

    Product-Service is treated as strongly related, Concept-Concept as
    moderately related, anything touching a Person as weak, else a neutral
    default.
    """
    ta = (type_a or "").strip()
    tb = (type_b or "").strip()
    pair = {ta, tb}
    if "Person" in pair:
        return 0.15
    if pair == {"Product", "Service"}:
        return 1.0
    if ta == "Concept" and tb == "Concept":
        return 0.5
    return 0.4


def adamic_adar(adjacency: dict[str, set[str]], a: str, b: str) -> float:
    """Adamic-Adar index: sum over shared neighbours ``n`` of ``1/log(deg(n))``.

    Neighbours with degree <= 1 are skipped (``log(1) == 0``); the natural log
    is used, matching the classic definition.
    """
    shared = adjacency.get(a, set()) & adjacency.get(b, set())
    score = 0.0
    for n in shared:
        deg = len(adjacency.get(n, set()))
        if deg > 1:
            score += 1.0 / math.log(deg)
    return score


# --------------------------------------------------------------------------
# 1. Four-signal relevance edges
# --------------------------------------------------------------------------
def relevance_edges(nodes, edges, docs_by_node, limit: int = 64) -> list[dict]:
    """Score candidate concept pairs on four blended signals and keep the top.

    Returns ``[{"a", "b", "weight", "signals": {...}}]`` ordered by descending
    blended ``weight`` (used for galaxy edge thickness). Candidates are pairs
    that share a stated edge, a document, or a neighbour — pairs with no shared
    signal are not relevance edges and are skipped.
    """
    type_by = {_get(n, "id"): _get(n, "type") for n in nodes}
    docs = {k: set(v) for k, v in (docs_by_node or {}).items()}
    adj = _adjacency(edges)
    stated = _stated_pairs(edges)

    candidates: set[frozenset[str]] = set(stated)
    doc_to_nodes: dict[Any, set[str]] = defaultdict(set)
    for nid, ds in docs.items():
        for d in ds:
            doc_to_nodes[d].add(nid)
    for members in doc_to_nodes.values():
        for a, b in combinations(sorted(members), 2):
            candidates.add(frozenset((a, b)))
    for nbrs in adj.values():
        for a, b in combinations(sorted(nbrs), 2):
            candidates.add(frozenset((a, b)))

    out: list[dict] = []
    for pair in candidates:
        a, b = sorted(pair)
        direct = 1.0 if pair in stated else 0.0
        da, db = docs.get(a, set()), docs.get(b, set())
        union = da | db
        source_overlap = len(da & db) / len(union) if union else 0.0
        aa = adamic_adar(adj, a, b)
        aa_norm = aa / (aa + 1.0)  # saturating map onto [0, 1)
        affinity = type_affinity(type_by.get(a), type_by.get(b))
        weight = (
            SIGNAL_WEIGHTS["direct"] * direct
            + SIGNAL_WEIGHTS["source_overlap"] * source_overlap
            + SIGNAL_WEIGHTS["adamic_adar"] * aa_norm
            + SIGNAL_WEIGHTS["type_affinity"] * affinity
        )
        out.append(
            {
                "a": a,
                "b": b,
                "weight": round(weight, 6),
                "signals": {
                    "direct": direct,
                    "source_overlap": round(source_overlap, 6),
                    "adamic_adar": round(aa, 6),
                    "type_affinity": affinity,
                },
            }
        )
    out.sort(key=lambda r: r["weight"], reverse=True)
    return out[:limit]


# --------------------------------------------------------------------------
# 2. Community detection + cohesion
# --------------------------------------------------------------------------
def _connected_components(node_ids: set[str], adj: dict[str, set[str]]) -> list[set[str]]:
    """Deterministic connected components (fallback when networkx is absent)."""
    seen: set[str] = set()
    comps: list[set[str]] = []
    for start in sorted(node_ids):
        if start in seen:
            continue
        stack = [start]
        comp: set[str] = set()
        while stack:
            cur = stack.pop()
            if cur in seen:
                continue
            seen.add(cur)
            comp.add(cur)
            for nb in adj.get(cur, ()):
                if nb not in seen:
                    stack.append(nb)
        comps.append(comp)
    return comps


def _detect(node_ids: set[str], edges) -> list[set[str]]:
    """Partition nodes into communities via Louvain, else connected components."""
    adj = _adjacency(edges)
    if _HAVE_NX:
        graph = nx.Graph()
        graph.add_nodes_from(node_ids)
        for e in edges:
            s, d = _get(e, "src"), _get(e, "dst")
            if not s or not d or s == d:
                continue
            if s in node_ids and d in node_ids:
                graph.add_edge(s, d)
        try:
            comms = nx.community.louvain_communities(graph, seed=1)
            return [set(c) for c in comms]
        except Exception:  # pragma: no cover - defensive fallback
            return [set(c) for c in nx.connected_components(graph)]
    return _connected_components(node_ids, adj)


def communities(nodes, edges) -> dict[str, dict]:
    """Assign each node to a community with a cohesion score.

    ``cohesion`` = internal edges / possible internal edges (``n*(n-1)/2``) for
    the community. Communities with ``cohesion < 0.15`` and ``size >= 3`` carry
    a ``flag: "low-cohesion"`` marker.
    """
    node_ids = {_get(n, "id") for n in nodes if _get(n, "id")}
    groups = _detect(node_ids, edges)

    undirected: set[frozenset[str]] = set()
    for e in edges:
        s, d = _get(e, "src"), _get(e, "dst")
        if not s or not d or s == d:
            continue
        undirected.add(frozenset((s, d)))

    result: dict[str, dict] = {}
    for idx, group in enumerate(groups):
        n = len(group)
        possible = n * (n - 1) / 2
        internal = sum(1 for pair in undirected if pair <= group)
        cohesion = internal / possible if possible else 0.0
        low = cohesion < _LOW_COHESION and n >= _MIN_FLAG_SIZE
        for nid in group:
            info = {"community": idx, "cohesion": round(cohesion, 6)}
            if low:
                info["flag"] = "low-cohesion"
            result[nid] = info
    return result


# --------------------------------------------------------------------------
# 3. Graph insights: surprising links + knowledge gaps
# --------------------------------------------------------------------------
def surprising_connections(nodes, edges, communities_map=None, limit: int = 10) -> list[dict]:
    """Node pairs with high Adamic-Adar but no direct link across communities.

    These are candidate cross-domain bridges: structurally similar (many shared
    connectors) yet never stated and living in different communities. Ranked by
    descending Adamic-Adar.
    """
    if communities_map is None:
        communities_map = communities(nodes, edges)
    adj = _adjacency(edges)
    stated = _stated_pairs(edges)

    # Only pairs that share at least one neighbour can score above zero.
    candidates: set[frozenset[str]] = set()
    for nbrs in adj.values():
        for a, b in combinations(sorted(nbrs), 2):
            candidates.add(frozenset((a, b)))

    out: list[dict] = []
    for pair in candidates:
        a, b = sorted(pair)
        if pair in stated:
            continue
        ca = (communities_map.get(a) or {}).get("community")
        cb = (communities_map.get(b) or {}).get("community")
        if ca is None or cb is None or ca == cb:
            continue
        aa = adamic_adar(adj, a, b)
        if aa <= 0.0:
            continue
        shared = len(adj.get(a, set()) & adj.get(b, set()))
        out.append(
            {
                "a": a,
                "b": b,
                "adamic_adar": round(aa, 6),
                "reason": (
                    f"shares {shared} connector(s) but no stated link; "
                    f"bridges communities {ca} and {cb}"
                ),
            }
        )
    out.sort(key=lambda r: r["adamic_adar"], reverse=True)
    return out[:limit]


def knowledge_gaps(nodes, edges, docs_by_node, communities_map=None) -> list[dict]:
    """Surface thin communities and single-source high-degree concepts.

    Each gap carries ``suggest_tags`` so the Curator can seed a Bulk-add
    pre-tag. Two kinds are emitted: ``thin-community`` (a community backed by
    few documents) and ``single-source-concept`` (a well-connected concept
    resting on a single source).
    """
    if communities_map is None:
        communities_map = communities(nodes, edges)
    label_by = {
        _get(n, "id"): (_get(n, "canonical_key") or (_get(n, "labels") or [_get(n, "id")])[0])
        for n in nodes
    }
    docs = {k: set(v) for k, v in (docs_by_node or {}).items()}
    adj = _adjacency(edges)

    gaps: list[dict] = []

    # Thin communities: many concepts, few backing documents.
    members_by: dict[int, list[str]] = defaultdict(list)
    for nid, info in communities_map.items():
        members_by[info["community"]].append(nid)
    for cid in sorted(members_by):
        members = members_by[cid]
        if len(members) < _MIN_FLAG_SIZE:
            continue
        doc_union: set[Any] = set()
        for m in members:
            doc_union |= docs.get(m, set())
        if len(doc_union) <= _THIN_COMMUNITY_DOCS:
            tags = [label_by.get(m, m) for m in sorted(members)][:3]
            gaps.append(
                {
                    "kind": "thin-community",
                    "label": f"community-{cid}",
                    "detail": (
                        f"community of {len(members)} concepts backed by "
                        f"{len(doc_union)} document(s)"
                    ),
                    "suggest_tags": tags,
                }
            )

    # Single-source, high-degree concepts.
    for nid in sorted(label_by):
        degree = len(adj.get(nid, set()))
        n_docs = len(docs.get(nid, set()))
        if degree >= _HIGH_DEGREE and n_docs == 1:
            key = label_by.get(nid, nid)
            gaps.append(
                {
                    "kind": "single-source-concept",
                    "label": key,
                    "detail": (f"'{key}' links to {degree} concepts but rests on a single source"),
                    "suggest_tags": [key],
                }
            )
    return gaps


def _community_summary(communities_map: dict[str, dict], nodes) -> list[dict]:
    """Compact per-community roll-up for the Insights panel."""
    label_by = {_get(n, "id"): (_get(n, "canonical_key") or _get(n, "id")) for n in nodes}
    members_by: dict[int, list[str]] = defaultdict(list)
    info_by: dict[int, dict] = {}
    for nid, info in communities_map.items():
        members_by[info["community"]].append(nid)
        info_by[info["community"]] = info
    out: list[dict] = []
    for cid in sorted(members_by):
        members = members_by[cid]
        info = info_by[cid]
        out.append(
            {
                "community": cid,
                "size": len(members),
                "cohesion": info["cohesion"],
                "flag": info.get("flag"),
                "labels": [label_by.get(m, m) for m in sorted(members)][:8],
            }
        )
    return out


def _tenant_graph(platform, tenant: str) -> tuple[list[dict], list[dict], dict[str, set[str]]]:
    """Load the tenant graph as plain dicts plus a node -> documents map.

    Documents are identified by ``content_hash`` drawn from node provenance, so
    the source-overlap and gap read-outs need no extra joins.
    """
    node_rows = platform.db.query(
        "SELECT id, canonical_key, type, labels, provenance FROM graph_nodes WHERE tenant=?",
        (tenant,),
    )
    edge_rows = platform.db.query(
        "SELECT src, dst, relation FROM graph_edges WHERE tenant=?",
        (tenant,),
    )
    nodes: list[dict] = []
    docs_by_node: dict[str, set[str]] = {}
    for r in node_rows:
        try:
            labels = json.loads(r["labels"]) if r["labels"] else []
        except ValueError:
            labels = []
        try:
            prov = json.loads(r["provenance"]) if r["provenance"] else []
        except ValueError:
            prov = []
        hashes = {p.get("content_hash") for p in prov if p.get("content_hash")}
        nodes.append(
            {
                "id": r["id"],
                "canonical_key": r["canonical_key"],
                "type": r["type"],
                "labels": labels,
            }
        )
        docs_by_node[r["id"]] = hashes
    edges = [{"src": r["src"], "dst": r["dst"], "relation": r["relation"]} for r in edge_rows]
    return nodes, edges, docs_by_node


def insights(platform, tenant: str) -> dict:
    """Curator -> Insights payload built from the tenant graph.

    Returns communities (summarised), surprising cross-domain links and
    knowledge gaps.
    """
    nodes, edges, docs_by_node = _tenant_graph(platform, tenant)
    communities_map = communities(nodes, edges)
    return {
        "communities": _community_summary(communities_map, nodes),
        "surprising": surprising_connections(nodes, edges, communities_map),
        "gaps": knowledge_gaps(nodes, edges, docs_by_node, communities_map),
    }


# --------------------------------------------------------------------------
# 4. Two-step ingest outline (T58 concept) — the deterministic analyse step
# --------------------------------------------------------------------------
_HEADING_RE = re.compile(r"^\s{0,3}#{1,6}\s+(.*\S)\s*$")
_ENTITY_RE = re.compile(r"[A-Z][A-Za-z0-9]+(?:\s+[A-Z][A-Za-z0-9]+)*")
_SENTENCE_SPLIT = re.compile(r"(?<=[.!?])\s+")

# Common sentence-opening capitalised words that are not entities.
_CAP_STOPWORDS = frozenset(
    {
        "The",
        "This",
        "That",
        "These",
        "Those",
        "A",
        "An",
        "And",
        "But",
        "Or",
        "If",
        "When",
        "While",
        "It",
        "We",
        "You",
        "They",
        "He",
        "She",
        "His",
        "Her",
        "In",
        "On",
        "At",
        "For",
        "To",
        "Of",
        "As",
        "By",
        "With",
        "From",
        "So",
        "Then",
        "There",
        "Here",
        "Its",
        "Their",
        "Our",
        "Your",
    }
)


def _is_heading(line: str) -> str | None:
    """Return a heading's text (markdown ``#`` or an all-caps title) else None."""
    m = _HEADING_RE.match(line)
    if m:
        return m.group(1).strip()
    stripped = line.strip()
    words = stripped.split()
    if 1 <= len(words) <= 8 and stripped.upper() == stripped and any(c.isalpha() for c in stripped):
        return stripped
    return None


def outline(text: str) -> dict:
    """Extract a structured outline: headings, entities and sentence claims.

    Deterministic and model-free — this is the *analyse* half of two-step
    ingest. Each claim carries a ``source_hint`` locator; when a provider is
    available the integrator constrains the generated summary to these claims,
    swapping the hint for the real source passage id (T38).
    """
    lines = (text or "").splitlines()
    headings: list[str] = []
    body_lines: list[str] = []
    for line in lines:
        head = _is_heading(line)
        if head is not None:
            if head not in headings:
                headings.append(head)
        else:
            body_lines.append(line)

    body = "\n".join(body_lines)

    entities: list[str] = []
    for match in _ENTITY_RE.finditer(body):
        phrase = match.group(0).strip()
        if not phrase:
            continue
        if " " not in phrase and phrase in _CAP_STOPWORDS:
            continue
        if phrase not in entities:
            entities.append(phrase)

    claims: list[dict] = []
    idx = 0
    for raw_sentence in _SENTENCE_SPLIT.split(body.replace("\n", " ")):
        sentence = raw_sentence.strip()
        if len(sentence) < 2 or not any(c.isalpha() for c in sentence):
            continue
        claims.append({"text": sentence, "source_hint": f"s{idx}"})
        idx += 1

    return {"headings": headings, "entities": entities, "claims": claims}
