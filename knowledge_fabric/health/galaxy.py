"""Real-physics galaxy payload builder (L2.4).

Pure Python, no browser. Turns the tenant knowledge graph into a node/edge
payload the vis-network galaxy renders, marking the nodes an answer activated
(coral) and their one-hop halo (blue) so an asker can see which slice of the
graph produced the answer. The browser module lives in
``scripts/showcase/galaxy.js``; this module carries no rendering concern and is
fully testable without a DOM.

Activation reuses the signal the compact ``/api/galaxy`` handler already
derives from the answer span's persisted trajectory
(``attrs.trajectory.{selected, graph_node_keys}``): the graph nodes whose name
appears in the passages the answer retrieved, plus any graph-expansion keys.
Everything here is read-only and tolerant of a tenant with no graph at all
(it returns empty lists, it never raises).
"""

from __future__ import annotations

import json
from typing import Any

# The five galaxy buckets. Every stored node ``type`` is mapped onto one of
# them; an unrecognised type falls back to "Concept".
_BUCKETS = ("Concept", "Product", "Service", "Repository", "Person")

# Substring hints (checked against the node type and its labels, lower-cased)
# that pull a stored domain type into one of the non-Concept buckets. Order
# matters: the first bucket whose hint matches wins.
_TYPE_HINTS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("Repository", ("repository", "repo", "codebase", "package")),
    ("Person", ("person", "author", "contributor", "team", "owner", "user")),
    ("Service", ("service", "api", "endpoint", "connector", "system")),
    ("Product", ("product", "release", "component", "platform", "module", "feature")),
)


# --------------------------------------------------------------------------
# Small tolerant store readers
# --------------------------------------------------------------------------
def _query(platform, sql: str, params: tuple) -> list[dict]:
    db = getattr(platform, "db", None)
    if db is None:
        return []
    try:
        return [dict(r) for r in db.query(sql, params)]
    except Exception:
        return []


def _load_nodes(platform, tenant: str) -> list[dict]:
    if not tenant:
        return []
    return _query(platform, "SELECT * FROM graph_nodes WHERE tenant=?", (tenant,))


def _load_edges(platform, tenant: str) -> list[dict]:
    if not tenant:
        return []
    return _query(platform, "SELECT * FROM graph_edges WHERE tenant=?", (tenant,))


def _labels(row: dict) -> list[str]:
    try:
        val = json.loads(row.get("labels") or "[]")
    except Exception:
        return []
    return [str(x) for x in val] if isinstance(val, list) else []


def _label(row: dict) -> str:
    labels = _labels(row)
    return (labels[0] if labels else "") or row.get("canonical_key") or row.get("id") or ""


def _node_type(row: dict) -> str:
    raw = (row.get("type") or "").strip()
    if raw in _BUCKETS:
        return raw
    hay = (raw + " " + " ".join(_labels(row))).lower()
    for bucket, hints in _TYPE_HINTS:
        for hint in hints:
            if hint in hay:
                return bucket
    return "Concept"


def _weight(edge: dict) -> float:
    weight = edge.get("weight")
    if weight:
        return float(weight)
    contextual = edge.get("contextual_weight")
    if contextual:
        return float(contextual)
    return 1.0


def _degree(edges: list[dict], node_ids: set[str]) -> dict[str, int]:
    deg: dict[str, int] = {}
    for edge in edges:
        src, dst = edge.get("src"), edge.get("dst")
        if src in node_ids and dst in node_ids:
            deg[src] = deg.get(src, 0) + 1
            deg[dst] = deg.get(dst, 0) + 1
    return deg


def _docs_by_node(platform, tenant: str, nodes: list[dict]) -> dict[str, int]:
    """Best-effort count of the documents whose passages mention each node.

    Zero for every node when the passage store is empty or unreadable — the
    caller treats a missing count as "unknown" (0), never as an error.
    """
    out = {n["id"]: 0 for n in nodes}
    passages = _query(platform, "SELECT document_id, text FROM passages WHERE tenant=?", (tenant,))
    if not passages:
        return out
    corpus = [(r.get("document_id"), (r.get("text") or "").lower()) for r in passages]
    for node in nodes:
        key = (node.get("canonical_key") or "").lower()
        if len(key) < 4:
            continue
        docs = {doc_id for doc_id, text in corpus if doc_id and key in text}
        out[node["id"]] = len(docs)
    return out


def _activated_ids(platform, tenant: str, trace_id: str, nodes: list[dict]) -> set[str]:
    """The nodes the answer for ``trace_id`` used, mirroring the /api/galaxy rule.

    A node lights up when its canonical key appears in the text of the passages
    the answer retrieved (``trajectory.selected``), or when it is a graph
    expansion key (``trajectory.graph_node_keys``). Empty when no such answer
    span exists.
    """
    if not trace_id:
        return set()
    try:
        spans = platform.telemetry.trace(trace_id)
    except Exception:
        return set()
    ans = next(
        (s for s in spans if s.get("name") == "answer" and s.get("tenant") == tenant),
        None,
    )
    if not ans:
        return set()
    try:
        traj = (json.loads(ans.get("attrs") or "{}") or {}).get("trajectory") or {}
    except Exception:
        traj = {}
    selected = traj.get("selected") or []
    node_keys = traj.get("graph_node_keys") or []

    by_key: dict[str, str] = {}
    for node in nodes:
        canonical = node.get("canonical_key") or ""
        by_key.setdefault(canonical, node["id"])
        by_key.setdefault(canonical.lower(), node["id"])

    texts = ""
    if selected:
        marks = ",".join("?" * len(selected))
        for row in _query(
            platform,
            f"SELECT text FROM passages WHERE tenant=? AND id IN ({marks})",
            (tenant, *selected),
        ):
            texts += " " + (row.get("text") or "").lower()

    active: set[str] = set()
    for node in nodes:
        key = (node.get("canonical_key") or "").lower()
        if len(key) >= 4 and key in texts:
            active.add(node["id"])
    for key in node_keys:
        nid = by_key.get(key) or by_key.get(str(key).lower())
        if nid:
            active.add(nid)
    return active


def _halo_ids(activated: set[str], edges: list[dict], node_ids: set[str]) -> set[str]:
    """Nodes exactly one hop from any activated node, excluding the activated set."""
    halo: set[str] = set()
    for edge in edges:
        src, dst = edge.get("src"), edge.get("dst")
        if src in activated and dst in node_ids:
            halo.add(dst)
        if dst in activated and src in node_ids:
            halo.add(src)
    return halo - activated


# --------------------------------------------------------------------------
# Public API
# --------------------------------------------------------------------------
def build_payload(platform, tenant: str, trace_id: str | None = None) -> dict[str, Any]:
    """Assemble the full-graph galaxy payload for a tenant, lit for one trace.

    Returns nodes (id/label/deg/type/docs), edges (from/to/relation/weight),
    the activated node ids, their one-hop halo, and a small stats block. When
    ``trace_id`` is falsy or names no answer, ``activated_ids`` and ``halo_ids``
    come back empty but the graph is still returned so the client can render the
    dimmed top-concept empty state.
    """
    trace_id = trace_id or ""
    node_rows = _load_nodes(platform, tenant)
    edge_rows = _load_edges(platform, tenant)
    node_ids = {n["id"] for n in node_rows}
    degree = _degree(edge_rows, node_ids)
    docs = _docs_by_node(platform, tenant, node_rows)

    nodes = [
        {
            "id": n["id"],
            "label": _label(n),
            "deg": degree.get(n["id"], 0),
            "type": _node_type(n),
            "docs": docs.get(n["id"], 0),
        }
        for n in node_rows
    ]
    edges = [
        {
            "from": e["src"],
            "to": e["dst"],
            "relation": e.get("relation") or "related",
            "weight": _weight(e),
        }
        for e in edge_rows
        if e.get("src") in node_ids and e.get("dst") in node_ids
    ]

    activated = _activated_ids(platform, tenant, trace_id, node_rows) & node_ids
    halo = _halo_ids(activated, edge_rows, node_ids)
    return {
        "trace_id": trace_id,
        "nodes": nodes,
        "edges": edges,
        "activated_ids": sorted(activated),
        "halo_ids": sorted(halo),
        "stats": {
            "nodes": len(nodes),
            "edges": len(edges),
            "relationships": len(edges),
            "activated": len(activated),
            "halo": len(halo),
            "hops": 1 if halo else 0,
        },
    }


def node_detail(platform, tenant: str, node_id: str) -> dict[str, Any] | None:
    """One node's side-sheet: its name, bucket type, doc count and the passages
    that mention it (capped at eight). ``None`` when the node is unknown."""
    node_rows = _load_nodes(platform, tenant)
    row = next((n for n in node_rows if n.get("id") == node_id), None)
    if row is None:
        return None
    key = (row.get("canonical_key") or "").lower()
    passages: list[dict] = []
    if len(key) >= 4:
        rows = _query(
            platform,
            (
                "SELECT p.text AS text, p.document_id AS doc_id, d.uri AS uri "
                "FROM passages p LEFT JOIN documents d "
                "ON d.tenant = p.tenant AND d.id = p.document_id "
                "WHERE p.tenant = ?"
            ),
            (tenant,),
        )
        for r in rows:
            text = r.get("text") or ""
            if key in text.lower():
                passages.append(
                    {"text": text, "doc_id": r.get("doc_id") or "", "uri": r.get("uri") or ""}
                )
            if len(passages) >= 8:
                break
    docs = _docs_by_node(platform, tenant, [row]).get(node_id, 0)
    return {
        "id": row["id"],
        "name": _label(row),
        "type": _node_type(row),
        "docs": docs,
        "passages": passages,
    }


def top_concepts(platform, tenant: str, n: int = 40) -> list[dict]:
    """The tenant's highest-degree nodes, for the galaxy empty state."""
    node_rows = _load_nodes(platform, tenant)
    edge_rows = _load_edges(platform, tenant)
    node_ids = {r["id"] for r in node_rows}
    degree = _degree(edge_rows, node_ids)
    docs = _docs_by_node(platform, tenant, node_rows)
    ranked = sorted(node_rows, key=lambda r: (degree.get(r["id"], 0), r["id"]), reverse=True)
    return [
        {
            "id": r["id"],
            "label": _label(r),
            "deg": degree.get(r["id"], 0),
            "type": _node_type(r),
            "docs": docs.get(r["id"], 0),
        }
        for r in ranked[: max(0, n)]
    ]
