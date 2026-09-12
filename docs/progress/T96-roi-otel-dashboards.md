# T96 — ROI and OTel dashboards for leadership

Two Admin dashboards composed entirely from what the platform already records
(the telemetry spine, the SLA report, the ledger insights, the analytics
rollup) — so leadership and engineering read the same fabric two ways. Additive.

## Admin → Overview (ROI) — the leadership view

`telemetry/roi.overview(platform, tenant, window)`:
- **Value**: questions answered; **hours saved** = answered × minutes-saved /
  60; split by persona and role.
- **Cost**: total spend, **cost avoided** (routing + caching), cost per answer,
  projected monthly (daily burn × 30).
- **ROI**: value delivered = hours saved × loaded rate, the **ratio** vs spend
  (blank when spend is zero), and week-over-week answer growth.
- **Adoption**: active users, questions per user, WoW growth.
- **Quality**: trust average, citation coverage, negative-feedback rate.
- **Service**: p50/p95 latency, fast-vs-agent share, the headline SLA line.

Two **Settings** knobs drive the money math — `minutes_saved_per_question`
(default 8) and `loaded_rate_per_hour` (default 75) — persisted to
`data/roi_settings.json` (honours `KF_DATA_ROOT`), set from the panel and audited
(`GET`/`POST /admin/settings`).

## Admin → Observability (OTel) — the technical view

`telemetry/roi.observability` + `roi.waterfall`:
- A **recent-trace list** (subject, level, latency, cost, status); click a row
  for the **span waterfall** (`?trace_id=…`, read from the spine's spans, offset
  + duration per span).
- **Error rate** (gap/clarify share), latency **p50/p95**, and a
  **reconciliation** check: the analytics rollup and the raw span counters must
  agree on answer count and cost **within one percent** (they read the same
  spans) — surfaced as a pass/drift badge.

`GET /admin/overview`, `GET /admin/observability` (list; `?trace_id=` waterfall).
Both panels animate on load and carry `?`-style definition text; both themes via
the shared tokens. `telemetry.events()` gained a `trace_id` so a row links to its
waterfall.

## Optional `obs` profile (Grafana + Langfuse)

`deploy/compose` gains an **`obs`** profile — Grafana (Prometheus + Jaeger
datasources) and Langfuse (LLM-trace analytics) — fed by the **same** OTel
collector the `full` profile already runs. The built-in Observability page needs
none of it (it reads the spine directly), so `obs` is purely additive:
`make compose-up PROFILE=obs`.

## Verification

- `tests/test_roi.py`: settings defaults + validation; ROI math (hours saved,
  value, ratio) exact and settings-driven; adoption/quality reconcile with
  `analytics`; observability reconciliation within ±1%; the waterfall returns
  ordered spans for a real trace.
- Full `pytest` green; `ruff` clean; showcase build (bakes `/admin/overview`,
  `/admin/observability`, `/admin/settings`) + parity pass.
