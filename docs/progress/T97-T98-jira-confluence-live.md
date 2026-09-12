# T97–T98 — the V1 Platform Jira board and Confluence, configured and answered

The Jira and Confluence connectors were already real; what remained was to
**configure** them for QualiZeal's AI CoE and to make the board's own workflow
answerable. This PR adds reproducible configuration, the board facts shape, and
board-column-aware answering. Additive — no live secrets required to build or
test.

## T97 — the V1 Platform board

- **Board facts.** `jira_live` gained `board_id` config. On sync it fetches the
  board **columns** (`/rest/agile/1.0/board/{id}/configuration`, status ids
  resolved to names via `/rest/api/3/status`) and the **active sprint with its
  dates** (`.../sprint?state=active`), and writes them into
  `facts.json["jira_projects"][KEY]`:

  ```jsonc
  { "issues": {…}, "sprint": {"name","state","start","end"},
    "board": {"id":34,"name":"V1 Platform",
              "columns":[{"name":"In Progress","statuses":["In Progress","In Review"]}, …]},
    "as_of", "window" }
  ```

  (Jira facts are keyed under `jira_projects` throughout the codebase — the
  answer path, surfaces and MCP all read that key — so we keep it rather than
  the instructions' shorthand `["jira"]`.)

- **In-progress, exact, from the board's definition.** A board column is a named
  *group* of statuses. `aggregate._jira_column_count` answers "how many tasks are
  in progress on the board" as the **sum of `by_status` over that column's
  statuses** (e.g. `In Progress + In Review`), not a single status literal —
  stamped with freshness and citing the board. `live_jql` still answers current
  questions when the facts are stale.

## T98 — Confluence

- Pages ingest as `text/html` documents → paragraph passages, cited at the page
  web URL; a citation **expands to its paragraph** via `passages.context` (T93).
- `facts.json["confluence_spaces"][KEY] = {pages, last_updated, as_of}`, so "how
  many pages in space `<KEY>`" answers from facts with freshness.
- `live_cql` serves the agent's ad-hoc current search. Only the company's own
  spaces are ingested — never the Atlassian marketing page.

## Reproducible, auditable configuration

- **`connectors/provisioning.py`** — `configure_jira_v1` / `configure_confluence`
  upsert only the non-secret settings (site URL, board id, allow-lists, 15-minute
  interval) through `connectors.admin` (audited); `jira_ready` / `confluence_ready`
  report what is configured and which secrets are missing **without leaking a
  value**. Secrets (`JIRA_TOKEN`, `CONFLUENCE_TOKEN`, emails, URLs) stay in the
  environment.
- **`scripts/configure_sources.py`** applies it in one command and prints the
  readiness lines. `docs/CONNECTORS.md` documents secrets, settings and the
  facts shapes.

The audience coverage matrix (T83) already carries Jira and Confluence rows with
their own corpus docs, so both data types are exercised per persona.

## Verification

- `tests/test_jira_board.py` — a fake Atlassian transport serves the board
  config, dated active sprint, status list and a V1 issue set; asserts the board
  columns + sprint dates land in facts, the in-progress count is the exact column
  sum with freshness, and provisioning is reproducible and leak-free.
- `tests/test_confluence_live.py` — a fake Confluence transport serves the CoE
  space and pages; asserts pages ingest with facts, the page count answers from
  facts, a page paragraph **expands to its neighbours**, `live_cql` serves the
  agent, and provisioning is reproducible.
- Full `pytest` green; `ruff` clean; showcase build + parity unaffected (additive
  connector/answer changes, no baked-endpoint changes).
