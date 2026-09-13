# T130 (Phase 2) — Live tokens & cost on the static demo

The user asked for the tokens/cost figures to **show live values as we consume**
the Anthropic key we saved, and chose "Build with the key + live in-browser
meter". This phase delivers both halves.

## Half 1 — the build already bakes REAL provider consumption (no change needed)

`.github/workflows/showcase.yml` runs the provider check and, when it passes,
builds with `KF_MODEL_MODE=anthropic` and the `ANTHROPIC_API_KEY` secret. The
build then drives the demo Q&A through the **real** model, so the baked
`snapshot.json` carries real tokens and real cost in `/admin/models`
(`consumption.totals`) and the ROI page. The `$0.0000` seen earlier was the old
build made with a rejected key — the next build with the valid key bakes real
numbers automatically. The key is used only at **build time** on the runner; it
is never written into the snapshot and never reaches the page.

## Half 2 — the live in-browser meter (per visitor)

On the static page every visitor question is answered by the open-source /
extractive path, which still counts real tokens and imputes its self-hosted
compute cost onto each answer (`tokens_in` / `tokens_out` / `cost` — T125). A
small meter in `scripts/showcase/engine.js` accumulates those per browser
(`localStorage` key `kf.consumption`) and folds them over the baked payloads so
the Admin panels **grow as the visitor asks**:

- `bumpMeter(a)` runs after each delivered answer (clarifies and gaps carry no
  tokens, so they don't count). It sums calls, input/output tokens and cost, and
  keeps per-model, per-day and recent-call rows.
- `GET /admin/models` → `mergeModels` adds the live deltas onto
  `consumption.totals` (calls / input / output / cost), folds the live rows into
  `by_model` and `by_day`, and puts the live calls at the head of `last_calls`.
  The Models telemetry totals mirror the same deltas.
- `GET /admin/overview` → `mergeOverview` recomputes the ROI page live: questions
  answered, tokens, `value_delivered_usd` (the same token volume at the baked
  frontier per-Mtok rate), `spend_usd`, and the ratio — plus the secondary
  labour view and questions-per-user.

Both merges are pure reads over the baked snapshot: repeated GETs are
idempotent (no double-counting), and a `localStorage` clear restores the baked
figures. **No Anthropic key is ever shipped to the page** — the meter only ever
sums what an answer already carries.

## Verification

- Node meter harness (loads the real `engine.js`, signs in with the baked admin
  token): before any question Models totals and ROI read the baked figures;
  after asking, calls / tokens / cost and the ROI value / spend / ratio all grow;
  a second GET is identical (no double-count).
- `ruff` + `ruff format --check` clean; `engine.js` `node --check` passes and
  carries no external URLs.
- `test_t32_parity`, `test_t47_surfaces`, `test_t126_facts_counts` green;
  `verify_showcase --dir` passes on a fresh build.

## Not in this phase (see Phase 3)

- PPT/PDF/Excel upload that commits to the repo via a GitHub Action (T131).
