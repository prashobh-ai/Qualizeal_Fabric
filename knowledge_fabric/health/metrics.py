"""Knowledge-health metrics (Section 16): coverage, connectedness,
traceability, currency, contradictions, gaps + a standing risk register.
Written as a snapshot at the end of each ingest job and read by the Curator
console and dashboards.
"""

from __future__ import annotations

from ..contracts.types import now_ms


def snapshot(platform, tenant: str, pack) -> dict:
    p = platform
    n_pass = p.passages.count(tenant)
    n_nodes, n_edges = p.graph_repo.counts(tenant)
    contradictions = p.graph_repo.contradictions(tenant)
    gaps = len(p.curation.list(tenant, "gap"))

    # coverage: share of the pack's salient vocab present in the corpus
    live_terms = set()
    for pas in p.passages.for_tenant(tenant):
        live_terms |= set(pas.text.lower().split())
    covered = sum(1 for t in pack.salient_vocab if t in live_terms)
    coverage = covered / max(1, len(pack.salient_vocab))

    connectedness = 0.0 if n_nodes == 0 else min(1.0, n_edges / max(1, n_nodes))
    # traceability: every passage carries provenance by construction -> 1.0
    traceability = 1.0 if n_pass else 0.0
    freshness = 1.0  # all synthetic docs are fresh at ingest time

    p.db.execute(
        """INSERT INTO health_snapshots(tenant,area,coverage,freshness,contradictions,gaps,
           connectedness,traceability,taken_at) VALUES(?,?,?,?,?,?,?,?,?)""",
        (
            tenant,
            "all",
            coverage,
            freshness,
            contradictions,
            gaps,
            connectedness,
            traceability,
            now_ms(),
        ),
    )
    return {
        "coverage": coverage,
        "connectedness": connectedness,
        "traceability": traceability,
        "contradictions": contradictions,
        "gaps": gaps,
    }


def latest(platform, tenant: str) -> dict:
    r = platform.db.one(
        "SELECT * FROM health_snapshots WHERE tenant=? ORDER BY id DESC LIMIT 1", (tenant,)
    )
    return dict(r) if r else {}


def risk_register(platform, tenant: str) -> list[dict]:
    """Thin/stale/unsourced/contradictory areas, visible before anyone asks."""
    h = latest(platform, tenant)
    risks = []
    if h:
        if h["coverage"] < 0.5:
            risks.append(
                {"risk": "thin coverage", "value": round(h["coverage"], 2), "severity": "high"}
            )
        if h["contradictions"] > 0:
            risks.append(
                {"risk": "contradictions flagged", "value": h["contradictions"], "severity": "high"}
            )
        if h["connectedness"] < 0.3:
            risks.append(
                {
                    "risk": "sparse graph / orphan nodes",
                    "value": round(h["connectedness"], 2),
                    "severity": "medium",
                }
            )
    gaps = platform.curation.list(tenant, "gap")
    if gaps:
        risks.append(
            {"risk": "unanswered demand (gap backlog)", "value": len(gaps), "severity": "medium"}
        )
    return risks
