# T157 — curator golden answers (PR #4 of the live-LLM series)

_A curator can lock in a verified answer for a question. The fabric then serves that
answer FIRST — before retrieval or any model call — at Level 0, $0, cited. It is the
cheapest, highest-trust tier: the curator's own words, delivered instantly._

## What it does

`scripts/showcase/engine.js`:

- **`goldenLookup(question)`** runs at the very top of the answer path, before the
  list / facts / retrieval tiers and before `maybeCompose`. It matches the question
  against the golden store by exact normalised text OR ≥ 0.9 token similarity (so a
  lightly reworded repeat still resolves), and returns the curated answer as
  **Level 0, `$0`, model `golden`, cited**, tagged `saved_bucket:"golden"` for the
  savings breakdown. The card reads "Answered from a curated golden answer."
- **CRUD routes**, persisted per visitor in `localStorage` (`kf.golden`; on a live
  deployment this is the governed store):
  - `GET /curator/golden` → `{ items: [...] }` (baked baseline + the visitor's saves,
    minus any deleted);
  - `POST /curator/golden` `{question, answer, citations?}` → upsert (editing is
    saving the same question again), author stamped from the caller;
  - `DELETE /curator/golden/{id}` (and `POST {action:"delete", id}`) → retire it,
    hiding even a baked golden.
  Each write ledgers a `curation` event.

## The two surfaces

- **Curator → Golden answers** panel (`curator_ui.py` + `curator_ui__js.js`): lists
  the golden entries, an add/edit form (question, answer, citations), and a delete
  action per row. Loaded with the rest of the curator panels.
- **Workspace → "Save as golden"** (`ask_ui__js.js` + css): on an answer, a curator
  (the curation lens) gets a **★ Save as golden** button next to the thumbs, which
  POSTs the turn's question + answer + cited titles to `/curator/golden`. A reader who
  is not a curator never sees it.

## Gate

- `scripts/showcase/golden_runner.js` + `tests/unit/test_golden_qa.py` — drive the
  shipped engine as a curator: saving persists (GET lists it); the question is then
  served from golden at Level 0, `$0`, model `golden`, cited; a reworded variant still
  matches while an unrelated question does not; deleting retires it and the question
  falls back to the normal path. Also asserts the built Curator page carries the
  Golden panel and the Workspace carries the "Save as golden" affordance.

## Verification

`node --check` clean (engine + both UI assets + the runner); ruff + format clean;
`test_golden_qa` passes; `test_showcase` + `test_t47_surfaces` pass; full
`build_showcase` + `verify_showcase` clean.

## Composes with the rest of the series

The `golden` savings bucket this PR emits is the third technique in the T158/T156
cost-saved breakdown (repeat-cache, level-selection, golden) — so once both land, a
golden hit shows up in the dashboard's savings-by-technique.
