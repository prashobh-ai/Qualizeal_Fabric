# Configuring the live sources (Jira & Confluence)

The Jira and Confluence connectors are **real** (`connectors/jira_live.py`,
`connectors/confluence.py`): read-only REST clients that ingest into the one
governed fabric, write pinned facts, and expose ad-hoc queries to the agent
(`live_jql`, `live_cql`). This page is how you *configure* them for QualiZeal's
AI CoE — reproducibly, with secrets kept out of the repo.

## Secrets (environment only)

Set these in the environment (CI secrets / a local `.env`); they are **never**
written to the connector config or committed:

| Source | Variables |
| --- | --- |
| Jira | `JIRA_URL`, `JIRA_EMAIL`, `JIRA_TOKEN` (an Atlassian API token; basic auth is `email:token`) |
| Confluence | `CONFLUENCE_URL` (the same site's `/wiki`), `CONFLUENCE_EMAIL`, `CONFLUENCE_TOKEN` |

## Non-secret settings (committed, auditable)

Everything else — site URL, the V1 board id, the project/space allow-lists, the
refresh interval — is stored per tenant in `connector_config` through
`connectors.admin`, so every change is audited. Apply it in one command:

```bash
python scripts/configure_sources.py --tenant qualizeal \
    --board 34 --project V1 \
    --confluence-spaces AICOE,ENG      # or set CONFLUENCE_SPACES
```

This writes:

- **`jira_live`** → `{url: https://qualizeal-team-aicoe.atlassian.net,
  board_id: 34, interval: "15m"}`, allow-list `["V1"]`. `sprint_field` is left
  unset so the connector resolves it from the board configuration at sync time.
- **`confluence`** → `{url: .../wiki}`, allow-list the CoE space keys.

`configure_sources.py` prints a **readiness line** per source: what is
configured and which secrets, if any, are still missing — without ever printing
a secret's value. The defaults live in `connectors/provisioning.py`
(`V1_BOARD_ID`, `COE_SITE`, …) and are overridable for another tenant, board or
site.

## What the V1 board sync produces (T97)

`facts.json["jira_projects"]["V1"]` (the codebase keys Jira facts under
`jira_projects`; the answer path, surfaces and MCP all read that key):

```jsonc
{
  "issues": { "total", "by_status", "by_priority", "by_type", "by_assignee" },
  "sprint": { "name", "state", "start", "end" },   // dated, from the Agile API
  "board":  { "id": 34, "name": "V1 Platform",
              "columns": [ { "name": "In Progress",
                             "statuses": ["In Progress", "In Review"] }, … ] },
  "as_of":  "…", "window": "updated >= -15m"
}
```

A **board column is a named group of statuses**, so "how many tasks are in
progress on the board" is answered as the *exact* sum of `by_status` over that
column's statuses (the board's own definition), stamped with freshness —
`aggregate._jira_column_count`. `live_jql` answers current questions when the
facts are stale.

## What the Confluence sync produces (T98)

Each page ingests as one `text/html` document → paragraph passages, cited at the
page's web URL; a citation **expands to its paragraph** through
`passages.context` (T93). Facts:
`facts.json["confluence_spaces"][KEY] = {pages, last_updated, as_of}`, so "how
many pages in space `<KEY>`" answers from facts. `live_cql` serves the agent's
ad-hoc current search. Do **not** ingest the Atlassian marketing page — only the
company's own spaces.

## Scheduling

`scripts/ingest.py --jira --confluence` runs a pull; each returns `skipped` with
a clear reason when its secrets are absent, so a keyless run never fails. Wire it
to the ingestion schedule alongside the other sources.
