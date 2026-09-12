# T118 — Connect the demo sources from a pasted URL (generic)

## Goal

The reviewer pastes exactly what the browser shows — a GitHub org/user or repo
URL, a Jira dashboard/board/project URL, a Confluence page/space URL, or a
website — and the same code path connects it, for QualiZeal or a personal
account alike. Nothing is hardcoded to any owner.

## What shipped

### 1. Generic URL parsing — `connectors/url_parse.py`

- `parse_source_url(raw)` classifies one pasted entry into
  `{"source", …fragment}`:
  - GitHub: `github.com/<owner>` → `{org}`; `github.com/<owner>/<repo>` (or bare
    `owner/repo`, `.git` tolerated) → `{repos}`.
  - Jira (`*.atlassian.net`, non-`/wiki/`): `/jira/dashboards/<id>` →
    `{dashboards}`; `/boards/<id>` → `{boards}`; `/browse/<KEY>-n` or
    `/projects/<KEY>` → `{projects}`; plus the site `url`.
  - Confluence (`/wiki/`): `/wiki/spaces/<KEY>/pages/<id>/…` → `{pages}`;
    `/wiki/spaces/<KEY>` → `{spaces}`; plus the site `url`.
  - Website: any other real URL → `{urls}`.
- `config_from_allow(source, entries)` folds a card's allow-list into that
  connector's config fragment. Plain identifiers still map to the native key,
  so an allow-list written before T118 is unchanged; a URL pasted into the
  wrong card is ignored, not misfiled.
- `PLACEHOLDERS` — the per-source add-source hint text.

### 2. Allow-list is URL-aware — `connectors/admin.py`

- `effective_config` folds the allow-list through `config_from_allow` (instead
  of writing one flat key), so a pasted URL becomes `org`/`repos`,
  `dashboards`/`boards`/`projects`, `pages`/`spaces` or `urls`. A parsed site
  `url` only fills in when the admin has not configured one explicitly.
- `credentials(source)` — required vs present secrets (env **or** connector
  config), so a card missing a required secret reports `configured: false`.
  `CREDENTIALS`: Jira/Confluence require their `*_URL/_EMAIL/_TOKEN`; GitHub's
  `KF_GITHUB_TOKEN` is *optional* (public works without it); website/files
  need none.

### 3. GitHub — token now optional (`connectors/github_live.py`)

- `_require()` no longer raises without a token; `_headers()` sends
  `Authorization` only when a token is present. Public repos of the pasted
  user/org ingest unauthenticated (rate-limited); an org-scoped token unlocks
  private org repos. `sync()` skips only when no scope (org/user/repo) is
  connected, never merely for a missing token. Org/user ingestion already flowed
  through `config.org` + `list_repositories()`.

### 4. Jira — dashboards and boards (`connectors/jira_live.py`)

- Config `dashboards=[id…]`, `boards=[id…]` alongside `projects`.
- `list_dashboards()` (`GET /rest/api/3/dashboard`, paginated) returns every
  dashboard the token's user can see, **including private ones** they own or are
  shared on.
- `dashboard_detail(id)` (+ `/gadget`) → the dashboard's name, owner and gadget
  titles, ingested as one document and a `facts.json["jira_dashboards"][id]`.
- `board_detail(id)` + `board_issues(id)` (`/rest/agile/1.0/board/{id}/issue`)
  ingest the board's issues (reusing the issue record) and a
  `facts.json["jira_boards"][id]` with its columns.
- `pull()` accepts any of projects/dashboards/boards; `sync()` skips only when
  none is connected.

### 5. Confluence — a single page (`connectors/confluence.py`)

- Config `pages=[id…]` alongside `spaces`.
- `pull_page(id)` (`GET /wiki/api/v2/pages/{id}?body-format=storage`) and
  `get_space(id)` ingest a pasted page directly (cited at its `webui` URL under
  the right space key), not only whole spaces. `pull()`/`sync()` accept spaces
  or pages.

### 6. Admin UI (`assets/admin_ui__js.js`)

- The allow-list input carries the URL-aware `placeholder` per source.
- A credential badge: **not configured · add `<secrets>`** (red) when a
  required secret is missing, **public only · add KF_GITHUB_TOKEN** (amber) for
  keyless GitHub, **configured** (green) otherwise — never a fake success.
- `GET /admin/connectors` now returns `placeholder` and `credentials` per card;
  `facts.load_facts()` carries the new `jira_dashboards` / `jira_boards` blocks.

## Verification

- `tests/test_connect_sources.py` (23 cases): URL parsing for every source +
  bare `owner/repo`; `config_from_allow` (URL, mixed, backward-compatible plain
  lists, wrong-source-ignored); `effective_config` URL-awareness (+ configured
  URL not overridden); credential status (Jira missing → not configured, via
  config → configured, website none, GitHub public-only); Jira `list_dashboards`
  + dashboard/board ingestion; Confluence `pull_page` + direct page ingestion;
  empty-allow-list raises.
- Updated `test_t37_t40_github_analysis` — the GitHub sync gate is now the scope,
  not the token.
- `ruff` clean; full suite green; showcase build + parity pass.

## Gate

- Pasting `github.com/QualiZeal` (org token) ingests org repos incl. private;
  `github.com/prashobh-ai` keyless ingests public repos.
- Pasting a Jira dashboard URL ingests the dashboards the token can see incl.
  private; a board URL ingests its issues.
- Pasting the `aicoe-genq` Confluence page URL ingests that one page.
- Pasting `qualizeal.com` crawls the site.
- A card missing its required secret shows `not configured · add <secrets>`.
