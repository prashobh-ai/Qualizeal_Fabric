# T133 — Admin & Curator panels relive from the ledger

_Implements instructions.md **T131** (serve/relive the panels from the ledger)._

The Admin and Curator panels are baked (served via `pickGet`), so they rendered
real-but-frozen values. Each now folds the live event ledger (T132) over the
baked payload, filtered to the active connector set, so every number moves as the
visitor asks, syncs, toggles or curates. Each merge **clones + overwrites** the
baked shape (never dropping a field a renderer reads); an empty window keeps the
baked baseline rather than a placeholder.

## Routes made live (`scripts/showcase/engine.js`)

| Route | Live behaviour |
|---|---|
| `/admin/overview` | value / spend / ratio from the live meter (T130) **plus** the saved ROI knobs |
| `/admin/models` | tokens / cost / per-model / per-day / recent-calls from the meter (T130) |
| `/admin/observability` | traces list, answered/clarified/gap, error rate, p50/p95 from `answer` events; `?trace_id=` returns the span waterfall |
| `/admin/service-levels` | headline p50/p95, fast/agent share, cost/answer, and the by-persona & by-data-type tables from `answer` events |
| `/admin/runs` | pipeline-run list synthesised from `sync` events (was baked `null`) |
| `/admin/audit` | audit trail = the whole ledger, newest first (was baked `null`) |
| `/admin/sources` | per-source enabled + items + last-run from connectors + `activeDocCountBySource` + `sync` events |
| `/curator/quality` | readiness rings scale with the active fraction (a source toggle drops them); `gaps` counts live gap/clarify questions |
| `/curator/gaps` | live gap/clarify questions grouped by topic, prepended to the baked list |
| `/curator/timeline` | month stacks from `curation` / `sync` / `source_toggle` events |

## ROI knobs persist

`/admin/settings` (GET/POST) is backed by `localStorage: kf.settings`, so the
ROI-save button really changes the minutes-saved / loaded-rate that drive the
value and spend math — reflected immediately in `/admin/overview` (even before the
first live question).

## Verification

Node harness: observability answers/traces > 0 after asking; service-levels
`n_answers` grows; a Sync adds a pipeline run (0→1); the audit trail carries the
events; ROI settings persist into the overview payload. Regression suites
(parity, surfaces, showcase, stage2_ui, facts, uploads) green; `verify_showcase`
passes.
