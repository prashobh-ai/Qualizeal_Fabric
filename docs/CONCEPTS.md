# Concept references

This platform reimplements ideas from a few open projects. Where a project's
**code** is used or vendored, it is a dependency in `ci/licence_manifest.json`
and credited in `THIRD_PARTY_NOTICES.md`. Where only an **idea** is used, the
project is credited here and no code is copied.

## Ideas reimplemented, no code copied

### llm_wiki (GPL-3.0)

`https://github.com/nashsu/llm_wiki`. GPL-3.0, so **no code is copied and it is
not a dependency**. The following concepts were reimplemented from their
public description, in original code, under this repository's own licence:

- Four-signal graph relevance — direct link, source overlap, Adamic-Adar,
  type affinity — blended into a single edge weight
  (`knowledge_fabric/health/graph_insights.py`).
- Community detection with a cohesion score, and low-cohesion flagging.
- Graph insights: surprising cross-domain connections and knowledge gaps.
- Two-step ingest: analyse to a structured outline, then generate a summary
  constrained to that outline with source-traceable claims.
- Async review with content-stable identifiers, so a rebuilt review queue
  keeps resolved items resolved (`knowledge_fabric/curation.py`).

## Ideas reimplemented, credited as a dependency

### token-meter (MIT)

`https://github.com/splunk/token-meter`. MIT. The telemetry concepts —
cost breakdown by phase, active-versus-idle time, waste, efficiency,
per-phase cost, provider quota snapshots and percentile timing — were
reimplemented in `knowledge_fabric/telemetry/insights.py`; the quota
registry pattern shapes the provider card. Credited in the manifest and
`THIRD_PARTY_NOTICES.md`.

## Code vendored

### vis-network (Apache-2.0 / MIT)

`https://github.com/visjs/vis-network`. The standalone UMD build is vendored
at `knowledge_fabric/surfaces/static/vendor/vis-network.min.js` and renders
the force-directed knowledge galaxy; the view half is
`knowledge_fabric/surfaces/static/vendor/galaxy.js`. No CDN; served
same-origin under `/static/vendor/`. Credited in the manifest and
`THIRD_PARTY_NOTICES.md`.
