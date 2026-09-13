# T129 (Phase 1) — Interactive connectors on the static demo

The Admin → Connectors toggle / Save / Sync / Delete were honest no-ops on the
static GitHub Pages showcase ("action acknowledged, no server-side state"). Now
they are **real in-browser switches** — the demo behaves exactly like a live
server, and the mock is invisible.

## What changed (client-side, per visitor)

`scripts/showcase/engine.js` holds a connector state layer, persisted to
`localStorage`, applied over the baked cards on load:

- **Toggle (enable/disable)** — flips the source on/off; a disabled source is
  **excluded from retrieval and from the facts/count answers**, so deactivating
  it genuinely removes its answers (verified: "what is QMentisAI" answers, then
  clarifies once Website is off, then answers again when re-enabled).
- **Save** — persists the allow-list + refresh interval + enabled state and
  returns the shaped `{status, connector}` the card expects.
- **Sync now** — updates the card's last-run / freshness / items and returns
  `{status:'ok', pulled, ingested, tombstoned, dataset_version}` so the toast
  reads real ("pulled N, ingested N").
- **Run due refreshes** — marks every enabled source refreshed.
- **Delete** (new button) — removes the source from the card list and
  deactivates its answers; a re-add brings it back. `POST /admin/connectors/delete`.

`GET /admin/connectors` now returns the visitor's **live** state (hiding a deleted
source), so a reload shows exactly what they changed. The baked sources remain a
permanent hidden preload — a `localStorage` clear restores the full demo.

## Website is one activatable group (no duplicate)

The QualiZeal product / service briefs are the organisation's own published
information (www.qualizeal.com), so the build now groups them under the
**`website`** source (real qualizeal.com citation URLs) instead of duplicating
them under GitHub. GitHub now holds the code (its cursor drives the GitHub card).
So Website is one activate/deactivate group holding the org knowledge, exactly
like the other sources.

## Mechanics

- `_export_index` tags each index passage with its `source`, so the browser BM25
  skips a disabled source's passages.
- When any source is off, the baked-answer cache and fuzzy lookup are bypassed
  (they don't know a source is off) and the question is answered by live
  retrieval, which respects the toggle. With everything enabled (the default),
  the fast baked path is unchanged.
- `factsAnswer` returns nothing for a disabled source's count (no "6
  repositories" once GitHub is off).

## Verification

- Node toggle harness: toggle persists; a disabled source stops answering and a
  re-enable restores it; Save/Sync/Delete return realistic payloads.
- `test_t32_parity`, `test_t47_surfaces` green; `verify_showcase --dir` passes;
  `ruff` clean; `engine.js` / `admin_ui__js.js` syntax-checked under Node.

## Not in this phase (see Phases 2–3)

- Live tokens/cost from the real key (build-with-key + live meter).
- PPT/PDF/Excel upload that commits to the repo via a GitHub Action.
