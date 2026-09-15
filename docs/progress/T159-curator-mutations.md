# T159 — the Curator's mutations are real, persisted-per-visitor changes

_The Curator UI's three write actions used to return a no-op ack ("action
acknowledged (no server-side state on Pages)"), so a curator's change vanished on
the next panel load — the panels looked read-only. This makes them behave like the
connectors already do: the change persists in the visitor's browser and the very
next panel GET reflects it._

## What was actually broken (empirical finding)

The instructions listed "six dead Curator routes". Probing the shipped engine showed
that is **stale**: the four curator **reads** — `/curator/curation-modes`,
`/curator/review`, `/curator/quality`, `/curator/registry` — are baked by
`build_showcase` (`CURATOR_GETS`) and already served. Only the three **writes** were
no-op acks. So this PR is scoped precisely to making those three mutations real and
persisted; the reads were left as-is.

## What it does

`scripts/showcase/engine.js` — a per-visitor overlay in `localStorage` (the same
pattern as `kf.connectors` / `kf.uploads`), applied on top of the baked reads:

- **`kf.curation`** holds `{modes, decided}`; **`kf.registry`** holds
  `{toggles, upserts}`.
- **`setCurationMode(source, mode)`** records a per-source (or global `*`/`default`)
  curation mode; **`mergeCurationModes(baked)`** folds it onto the baked
  `{default, sources, allowed}` so the panel shows the chosen mode on reload.
- **`decideReview(reviewId, action)`** records a queue decision;
  **`mergeReview(baked)`** drops decided items from `items`, so an accepted/rejected
  review disappears from the queue.
- **`mutateRegistry(body)`** handles `enable` / `disable` (a per-id toggle) and
  `upsert` (a new/edited known-question entry); **`mergeRegistry(baked)`** applies the
  toggles and upserts onto `entries` — a disabled entry reads `enabled:false`, and an
  upserted entry appears in the list.

The three POST handlers (`/curator/curation-mode`, `/curator/review-decision`,
`/curator/registry`) now call these, ledger a `curation` event (so the timeline and
audit reflect the action), and return the mutation result instead of the generic ack.
The three GET handlers overlay the persisted change before responding.

Everything is client-side and invisible: no network, no server state — consistent
with the static-Pages constraint. On a fresh browser the panels read exactly the
baked defaults.

## Gate

- `scripts/showcase/curator_runner.js` — drives the **shipped** `engine.js` under a
  Node browser shim, signs in as the curator, and for each mutation does
  **POST-then-GET** through the same engine, printing before/after observations.
- `tests/unit/test_curator_routes.py` — builds a small showcase, runs the runner, and
  asserts: the four reads answer for the curator with their expected keys; a
  curation-mode switch to `manual` persists to `sources`; a registry disable flips
  `enabled` to false on re-GET; an upsert appears on re-GET; and a review decision
  acks and any decided item drops from the queue (the small build bakes an empty
  queue, which the runner handles gracefully).

## Verification

`node --check` clean on `engine.js` and `curator_runner.js`; ruff + format clean;
`test_curator_routes` (6) passes; full `build_showcase` + `verify_showcase` clean with
the T166 live-LLM gate still satisfied.

## Not in this PR (the rest of the series)

Golden Q&A (T157), the analytics chart series (T156) and the repeat-cache / cost-saved
panel (T158) are PR #3; the user-management form with role/designation dropdowns
(T160) and the dashboards wiring for tokens/cost/model (T155/T167) follow.
