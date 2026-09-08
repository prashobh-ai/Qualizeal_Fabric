"""GraphStore adapter over the SQLite graph tables (managed graph DB in cloud)."""
from __future__ import annotations

from ..contracts.types import GraphEdge, GraphNode
from ..stores.repositories import GraphRepo


class SqlGraphStore:
    def __init__(self, repo: GraphRepo):
        self.repo = repo

    def upsert_nodes(self, tenant: str, nodes: list[GraphNode]) -> None:
        for n in nodes:
            self.repo.upsert_node(n)

    def upsert_edges(self, tenant: str, edges: list[GraphEdge]) -> None:
        for e in edges:
            self.repo.upsert_edge(e)

    def neighbors(self, tenant: str, node_id: str, hops: int = 1) -> list[dict]:
        seen = {node_id}
        frontier = [node_id]
        edges: list[dict] = []
        for _ in range(max(1, hops)):
            nxt = []
            for nid in frontier:
                for e in self.repo.neighbors(tenant, nid):
                    edges.append(e)
                    for other in (e["src"], e["dst"]):
                        if other not in seen:
                            seen.add(other)
                            nxt.append(other)
            frontier = nxt
        return edges

    def resolve(self, tenant: str, mention: str, type_: str):
        return self.repo.resolve(tenant, mention.strip().lower(), type_)
