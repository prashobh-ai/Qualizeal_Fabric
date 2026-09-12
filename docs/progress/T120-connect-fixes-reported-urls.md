# T120 — Connect fixes for the reviewer's reported URLs

Three URLs the reviewer could not connect from the Admin UI:

1. `https://github.com/prashobh-ai`
2. `https://aicoe-genq.atlassian.net/wiki/spaces/~<key>/pages/1703938/Project+Plan?…`
3. `https://qualizeal-team-aicoe.atlassian.net/jira/dashboards`

## What was actually wrong

- **(1) GitHub personal user — real bug.** `GitHubLiveConnector.list_repositories`
  called `/orgs/<name>/repos` and, only if it came back empty, tried
  `/users/<name>/repos`. But `_rest_paged` did **not** pass `ok_missing`, so for
  a personal user (where `/orgs/<user>/repos` returns **404**) the call *raised*
  before the user fallback could run — the fallback was dead code. A pasted
  `github.com/<user>` URL therefore failed for every personal account.
- **(3) `/jira/dashboards` index — parsing gap.** `parse_source_url` only
  recognised a dashboard URL with an id (`…/dashboards/<id>`). The bare index
  produced no allow-list entry, so the Jira card saved but synced nothing.
- **(2) Confluence page — already correct.** The tilde personal-space key and
  the `?contentRecommendation=…&popular=true` query parse fine to
  `{pages: ["1703938"]}`; no code change needed. (Connecting it live still needs
  the `CONFLUENCE_*` secrets in the environment — see below.)

## Fixes

- `connectors/github_live.py`:
  - `_rest_paged(..., ok_missing=False)` — a 404 now returns `[]` instead of
    raising.
  - `list_repositories` tries `/orgs/<name>/repos` (ok_missing) then falls back
    to `/users/<name>/repos` (ok_missing), so an organisation and a personal
    user both resolve; neither raises on the other's 404.
- `connectors/url_parse.py`: `…/jira/dashboards` with no id →
  `{dashboards: ["*"]}` ("every dashboard the token can see").
- `connectors/jira_live.py`: a `*` in the dashboards allow-list is resolved at
  pull time via `list_dashboards()` (which returns every dashboard the token can
  see, including private ones), then each is ingested.

## Verification

- `tests/test_connect_sources.py`: the three reported URLs parse to actionable
  config; a personal GitHub user lists repos via the `/users/…` endpoint (fake
  transport with `/orgs/<user>` 404); a `*` dashboards allow-list ingests every
  dashboard the fake Jira returns.
- Full suite green; ruff + `ruff format --check` clean; showcase build + parity
  pass.

## Live connection still needs (environment, not code)

Parsing + ingestion now handle all three URL shapes. Actually pulling the data
live additionally requires, in the **running server** (not the static showcase,
whose connectors are baked no-ops):

- **GitHub** `github.com/prashobh-ai` — works keyless for public repos
  (rate-limited); a personal `KF_GITHUB_TOKEN` adds private repos + a higher
  rate limit. Network egress to `api.github.com` must be allowed.
- **Jira** `…/jira/dashboards` — needs `JIRA_URL`, `JIRA_EMAIL`, `JIRA_TOKEN`
  for a user who can see the dashboards (private ones included).
- **Confluence** page — needs `CONFLUENCE_URL`, `CONFLUENCE_EMAIL`,
  `CONFLUENCE_TOKEN` (its own site, independent of Jira).

A card missing its required secret shows `not configured · add <secrets>` (T118),
so the Admin UI states exactly what to add rather than failing silently.

## Static showcase is honest about live connect

The reviewer was adding sources on the **GitHub Pages** build
(`…github.io/Qualizeal_Fabric/admin/`), where Save popped "github saved ·
disabled" but nothing entered the fabric. GitHub Pages serves only static files
— there is no backend to run the connectors, no place to hold secrets, and a
browser cannot call the GitHub/Jira/Confluence APIs with the user's credentials.
So on the static build:

- `scripts/showcase/engine.js` now answers `POST /admin/connectors` and
  `/admin/sync` with `{status: "demo", message: …}` instead of a bare
  `{ok: true}` — the message says to run the server (`make serve`) and connect
  there with the source's secrets.
- `admin_ui__js.js` surfaces a `status: "demo"` response as a clear warn toast,
  so Save no longer reads as a successful (but inert) "saved".

To actually connect and ask over live sources, run the server build (not Pages):
`make serve`, sign in as admin, set the source's secrets in the environment,
paste the URL into its card, and Sync.
