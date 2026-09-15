# T158 + T156 — repeat cache, cost saved, and live analytics (PR #3 of the live-LLM series)

_Makes the cost-effectiveness story real and visible: a repeated question is served from
cache at `$0`, every cheap answer books the top-tier cost it avoided, and the dashboard's
charts grow as the visitor asks instead of sitting at a frozen build-time snapshot._

## T158 — repeat cache + token cost saved

`scripts/showcase/engine.js`:

- **Per-visitor answer cache** keyed on `(normalised question, active-source
  fingerprint)`. The fingerprint folds in every enabled source and the selected
  provider (`activeFingerprint()`), so toggling or deleting a cited source — or
  switching provider — changes the key and invalidates the entry. A repeat within the
  same active set returns the cached answer at **Level 0, `$0`, model `cache`,
  `cache_hit:true`** — no retrieval, no model call.
- **Cost-saved ledger.** Every delivered answer books
  `cost_saved = what the top tier (Sonnet 5) would have cost for the same tokens −
  what was actually spent`, tagged by bucket:
  - a cache hit → **`repeat-cache`** (the whole top-tier cost avoided);
  - any answer served below the top tier (extractive, Haiku, Sonnet 4.6) →
    **`level-selection`**;
  - (golden → `golden`, wired in the T157 PR).
  The event ledger, `bumpUsage`, `usageFromEvents` and the meter already carried
  `cache_hit` / `cost_saved` fields — this is the code that finally sets them, so every
  panel that sums them moves.

The operations-lens card already renders `cache_hit` / `cost` / `cost_saved`, so a cache
hit reads "served from cache · $0" with the saving shown.

## T156 — live analytics: the charts grow as you ask

The series charts already live on the `/dashboard` page (`dashboard__dashboard_html.html`
— `bars` / `donut` / `line` SVG primitives over `/api/analytics`). The gap was that
`engine.js` served `/api/analytics` **baked-only**, so nothing moved on the static site.

- **`mergeAnalytics(baked, window)`** folds the visitor's own in-window answer events
  (the `kf.events` ledger, disjoint from the baked server spans, so the fold is purely
  additive) onto the baked payload: answer count, tokens, `total_cost`,
  `total_cost_saved`, `cache_hit_rate`, `routing_by_level`, `routing_by_complexity`,
  `savings_by_technique`, `models_used`, `per_user`, `by_language`, latency percentiles,
  and a rising **timeseries tail** carrying per-bucket `latency_ms` and `complexity`.
  Windows: 24h hourly, 7d/all daily. `/api/analytics` now returns the merged payload.
- **`telemetry.py`** — the baked per-bucket series now also carries `latency_ms` (avg)
  and a `by_level` split, so the latency-over-time and complexity charts are consistent
  baked + live.
- **Dashboard** — added the T156 charts the primitives support: a **model-distribution
  donut**, a **complexity-mix** bar, **questions-by-user** and **spend-by-user** bars, a
  **total-questions-answered** headline, and a **latency (avg ms) over window** line. The
  existing cost-saved line and savings-by-technique bars now show the T158 buckets and
  grow live. The window switch (24h / 7d / all) already re-queries and redraws.

## Gate

- `scripts/showcase/cache_runner.js` + `tests/unit/test_repeat_cache.py` — POST-then-GET
  through the shipped engine: a repeat is a cache hit at `$0` model `cache`; `cost_saved`
  is booked and the live usage `cost_saved` accumulates; toggling a source off
  invalidates the cache (next ask is not a hit).
- `scripts/showcase/analytics_runner.js` + `tests/unit/test_analytics_live.py` — asking
  raises the analytics answer count and cost-saved, marks the payload `live_merged`, adds
  the `repeat-cache` / `level-selection` savings buckets, grows the timeseries with a
  latency-bearing tail, and the shipped dashboard page carries the new chart calls.
- `test_ws_prove` / `test_t30_telemetry` / `test_roi` still pass (the analytics series
  change only adds keys).

## Verification

`node --check` clean (engine + both runners); ruff + format clean; the T156/T158 gates
pass; `test_ws_prove` + `test_t30_telemetry` + `test_roi` (20) pass; full
`build_showcase` + `verify_showcase` clean.

## Not in this PR (the rest of the series)

Golden Q&A (T157) is the next PR (it books its own `golden` savings bucket, already
anticipated here); the user-management form (T160) follows.
