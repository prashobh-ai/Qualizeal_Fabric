# T115 — fix the connector Save/Sync crash

The Admin **Save** threw `Cannot read properties of undefined (reading 'enabled')`
and **Sync** printed `… · undefined`. Root cause: two naming schemes for the same
connector on one screen (`github` vs `jira_live`), a server that could raise
instead of returning a body, and a client that read `.enabled` off a possibly-
undefined response. Fixed on all three layers.

## One source key per connector, everywhere

Five canonical keys — `website`, `files`, `github`, `jira`, `confluence` — used by
the card, the API, `connectors.admin`, the scheduler and the registry:

- **`registry.py`**: `github` and `jira` resolve to the **live** connector
  classes for real syncs; a new `REPLAY` map + `build()` selects the replay
  classes when `records=` is injected (offline demos and tests) — so a real sync
  uses the live client and a test uses its records. `github_live`/`jira_live` are
  gone as registry keys; the live class *names* are unchanged.
- **`admin.py`**: `KNOWN_SOURCES` + `is_known()`; `ALLOW_KEYS` drops the `_live`
  duplicates.
- The connector `sync()` entrypoints (`github_live.sync`, `jira_live.sync`) and
  `connectors.provisioning` now read/write config and schedule under the
  canonical `github` / `jira` keys. Document identity (`source_name`) is
  unchanged, so ingested documents keep their provenance.

`list_all` therefore yields exactly one card per source — no `jira_live` /
`github_live` duplicate cards.

## The endpoint never raises to the client

`POST /admin/connectors` now:
- returns `200 {status:"error", message:"unknown source …", connector:<row>, health:{}}`
  for an unknown source (not a 500);
- wraps `upsert` + schedule + `health_for` in a try/except that returns the same
  shaped error body on any failure;
- returns `200 {status:"ok", connector:<row>, health:…}` on success, where `row`
  always carries `enabled`, `source`, `allow`, `interval_s`.

## The client reads every field defensively

`admin_ui__js.js`:
- **Save**: `const c = out&&out.connector ? out.connector : {}`; on
  `out.status==='error'` it shows `out.message`, else `<source> saved · enabled`.
- **Sync**: builds the toast with `num(out&&out.pulled)` … and
  `(out&&out.status)||'done'`, so it never prints `undefined`.
- **Both**: `loadConnectors()` (and the runs/audit reloads) run in a `finally`
  (guarded), so the card always reflects the true server state after either
  action, even on the error branch.

## Gate

| Check | Result |
|---|---|
| Save on `github` | 200, `status:"ok"`, `connector.enabled` present — no JS crash |
| Save on `jira` without secrets | `jira saved · enabled`, no `undefined`/crash |
| One card per source (`website/files/github/jira/confluence`) | no `jira_live`/`github_live` cards |
| Unknown source | 200 with `status:"error"` and a message, never a 500 |

## Verification

`tests/test_connector_save.py`: the five canonical keys only; `github`/`jira`
resolve to live classes, and `records=` selects the replay classes; the Save
endpoint returns a shaped `ok` body with `enabled`; an unknown source is a shaped
`error` (200, not 500); the connector listing has one card per source and no
`_live` duplicates. Full `pytest` green; `ruff` clean; showcase build + parity
pass.
