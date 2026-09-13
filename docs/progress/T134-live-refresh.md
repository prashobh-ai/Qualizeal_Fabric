# T134 — Live refresh across all three screens

_Implements instructions.md **T132** (cross-tab live refresh)._

One refresh bus every surface shares, so a question asked in the Workspace tab
moves the Admin tab's counters, ROI, models table and observability list without a
reload.

## What changed

- `knowledge_fabric/surfaces/assets/ui_common__runtime_js.js` exports
  `KF.onChange(fn)` and `KF.event(kind, row)`:
  - `onChange` subscribes to same-tab `kf:changed` (dispatched by the engine's
    `evAppend`) **and** cross-tab `storage` writes to the ledger keys
    (`kf.events` / `kf.ledger` / `kf.consumption` / `kf.connectors` / `kf.uploads`
    / `kf.settings`), debounced to 250 ms so a burst causes one repaint.
  - `KF.event` lets a page write into the same `kf.events` store and ping the bus.
- `admin_ui__js.js`: `KF.onChange(loadAll)` so every panel recomputes on any
  change; the silent 404-swallow in the coverage / SLA / observability loaders is
  removed — now the routes are served, a real error surfaces as a toast (401 while
  signed-out stays quiet).
- `curator_ui__js.js`: `KF.onChange(loadAll)` — the readiness rings, gaps and
  timeline recompute live.
- `ask_ui__js.js`: `KF.onChange` refreshes the corpus tiles and My-usage, so an
  Admin source toggle in another tab moves the Workspace counts.

## Verification

`node --check` clean on all four assets; the T132/T133 harness exercises the same
ledger the bus fires on. Regression suites green; `verify_showcase` passes.

## Deferred (follow-up PR, per the plan)

- instructions.md **T133** (button audit) → repo T135.
- instructions.md **T134** (session log) → repo T136.
- instructions.md **T135** (provider switch everywhere) → repo T137.
- instructions.md **T136** (seam / no-constant / counter-sync tests) → repo T138.
- The DOCX/PPTX live-upload work is a separate instruction file, not started here.
