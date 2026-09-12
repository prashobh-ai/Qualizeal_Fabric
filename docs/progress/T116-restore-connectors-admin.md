# T116 — restore the connectors admin + independent panel loads

The Admin page showed `Request failed — not found: /admin/connectors`, the
connector cards were gone, and the ROI/Service/Observability panels rendered
empty — one broken admin load cascaded and blanked the rest.

## Fixes

- **No cascade.** `loadAll` now runs every panel loader through
  `Promise.allSettled` instead of `await loadConnectors()` then early-returning on
  its failure. Each loader (`loadConnectors`, `loadOverview`/ROI,
  `loadSLA`/service, `loadObservability`, …) already catches independently and
  keeps its own empty state, so a 403 or a transient error on one panel never
  blanks the others.
- **Five cards always render.** `connectors.admin.list_all` already returns one
  row per canonical source (`website/files/github/jira/confluence`) with
  `enabled`, `allow`, `config`, `scopes` — even when never synced (a default
  row). `GET /admin/connectors` joins each row's `health` and now also carries
  `interval_s`, so the "Refresh every" value renders on a never-synced card.
- **Add-source affordance.** A visible bar above the cards lists the five source
  types; clicking one scrolls to that card and focuses its allow-list input
  (with a brief flash), so a reviewer can paste a URL into the right card without
  hunting. (The URL-aware placeholders land in T118.)
- **Clean 403 for non-admins.** `GET /admin/connectors` is behind
  `_require("admin")`, which returns a JSON 403; the client renders "sign in as
  admin" per panel, never a red route-not-found banner.

## Verification

`tests/test_admin_connectors.py` (live HTTP harness): the five cards always
render with full rows (`enabled/allow/config/scopes/interval_s/health`) plus
`schedules`; a non-admin gets a clean `403` with a JSON message, not a
"not found". Full `pytest` green; `ruff` clean; showcase build + parity 11/11.
