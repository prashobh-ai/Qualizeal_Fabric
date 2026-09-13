# T132 — One event ledger, written on every action

_Implements instructions.md **T130** (Make Every Screen Live — the event ledger)._

Every surface (Workspace, Admin, Curator) now reads one append-only store, so a
thing done on any screen can recompute every panel on every screen.

## What changed (`scripts/showcase/engine.js`)

- `STATE.events` (`localStorage: kf.events`, one row per answer/action) and
  `STATE.ledger` (`kf.ledger`, one row per model call), loaded at init, capped at
  1000 rows each.
- `evAppend(kind, row)` appends an event and dispatches `kf:changed`;
  `ledgerAppend(row)` records a model call. Panels read **fresh** from
  localStorage (`events()` / `ledgerRows()`) so a sibling tab's writes are seen.
- `evAnswer(subject, designation, question, a)` runs on **every** delivered answer
  and clarify — it writes the row-level `answer` event (kind, level, provider,
  model, tokens, cost, latency, trust, citations, sources, path, session, trace)
  and, when tokens were spent, the model-call ledger row.
- Events are also written at: `/api/explain` (`explain`), `/feedback` &
  `/curator/feedback` (`feedback`), `/admin/sync` & `/admin/refresh/run-due`
  (`sync`), `/admin/connectors` save & delete (`source_toggle`),
  `/curator/decision`, `/curator/repository/delete` and uploads (`curation`).
- UI-side events (`ui_common.KF.event`) append to the **same** `kf.events` store,
  so voice/session events (later tasks) merge into one ledger.

## Fold-in — the active document set is the one source of truth

- `activePassages()` = index passages whose connector is on **and** whose document
  is not deactivated. `ridx()` builds from it (recomputing df/N/avgdl only when the
  set is reduced, so the default all-active ranking stays byte-identical to the
  server — parity preserved). Retrieval and ranking now read the active set, so a
  deactivated source genuinely stops answering (the seam a reviewer finds first).
- `corpusCounts()` computes the ten Workspace tiles from the active set (passages,
  documents, and the source tiles zero when their connector is off), and
  `activeDocCountBySource()` feeds the Admin sources card and Curator counts — one
  helper, so no two screens disagree. `/api/corpus` now serves live counts.

## Verification

Node harness against the real `engine.js`: `kf.events` and `kf.ledger` grow on
ask; corpus passages drop 2303→416 when Website is toggled off and the source
stops answering; re-enabling restores the counts. `test_t32_parity`,
`test_t47_surfaces`, `test_showcase`, `test_stage2_ui`, `test_t126`, `test_t131`
green; `verify_showcase` passes; `engine.js` `node --check` clean and free of
external URLs.
