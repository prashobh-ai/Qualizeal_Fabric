# T124 — Verify the keys actually pull

The secrets are set as GitHub Actions repo secrets (`KF_GITHUB_TOKEN`,
`JIRA_URL/EMAIL/TOKEN`, `CONFLUENCE_URL/EMAIL/TOKEN`, `ANTHROPIC_API_KEY`). This
proves each one reaches real data and **fails loudly per source** if it does
not — in a build log, before a demo.

## The root cause the wiring fixes

`ingest.yml` only ever passed `ANTHROPIC_API_KEY` and the default `GITHUB_TOKEN`
into the job env — never `KF_GITHUB_TOKEN`, `JIRA_*` or `CONFLUENCE_*`. So even
with the secrets set, the connectors could not see them and the sources
silently produced nothing. This task passes them in and proves them.

## T124a — preflight (`scripts/preflight_sources.py`)

Reads `data/showcase_sources.json` (the four company sources) and makes ONE
authenticated call per source, recording a line to `$GITHUB_STEP_SUMMARY` and
`data/preflight.json`:

| Source | Call | Pass |
|---|---|---|
| GitHub org `Qualizeal` | `GET /orgs/Qualizeal/repos?per_page=1` (Bearer `KF_GITHUB_TOKEN`) | 200 + a repo (200 + empty ⇒ WARN, org may need to approve the token) |
| Jira | `/rest/api/3/myself`, then `/dashboard/10001` and `/agile/1.0/board/34` (basic auth) | myself 200 **and** dashboard + board 200 |
| Confluence | `$CONFLUENCE_URL/api/v2/pages/1703938?body-format=storage` (basic auth) | 200 + body |
| Website | `GET https://qualizeal.com/sitemap.xml` | 200 (404 ⇒ WARN, homepage still crawlable) |

Failure kinds are distinguished — **401** wrong email/token pairing, **403**
token valid but resource not shared, **404** wrong id/site, **network** URL
wrong — because each needs a different fix. The script **exits non-zero** if any
source with its secrets set fails; a source whose required secret is missing is
skipped, not failed. Ends with `Preflight: N/4 sources reachable`.

Wired into `ingest.yml` before the ingest step. (Not into `ingestion-check.yml`,
which is deliberately offline/no-keys, nor `showcase.yml`, which builds from
already-ingested `fabric-data`.)

## T124b — post-ingest assertion (`verify_showcase.py --fabric`)

After ingest, asserts from `fabric-data` facts:
- `qualizeal.com` → ≥ 20 documents (`documents.by_area.website`).
- `Qualizeal` org → ≥ 1 repository **and** ≥ 1 github document chunked.
- Jira → board `34` fact present, project `V1` census `by_status` non-empty,
  dashboard `10001` produced a title/gadget fact.
- Confluence → page `1703938` produced ≥ 1 document.

A source whose preflight passed but whose ingest produced nothing fails with
`<source>: preflight OK but 0 … — connector bug, not credentials`, so a
credential-vs-connector fault is named correctly. Wired into `ingest.yml` after
ingest.

## T124c — smoke demo (`scripts/smoke_demo.py`)

Asks one question per source and asserts a cited answer (`kind=answer`, ≥ 1
citation), never a gap:
- website: *what does QualiZeal offer for security testing*
- github: *how many repositories does QualiZeal have*
- jira: *how many tasks are in progress on the Platform board*
- confluence: *who is the project lead in the Project Plan page*

The DB is not persisted past ingest, so the smoke does a bounded live re-pull
(model-free extractive, no credits) into an in-memory fabric, then asks. Wired
into `ingest.yml` after the assertion.

## Ingest wiring

`ingest.yml` now exports `KF_GITHUB_TOKEN`, `GITHUB_ORG=Qualizeal`,
`JIRA_URL/EMAIL/TOKEN` + `JIRA_PROJECTS=V1` + `JIRA_DASHBOARDS=10001` +
`JIRA_BOARDS=34`, `CONFLUENCE_URL/EMAIL/TOKEN` + `CONFLUENCE_PAGES=1703938`, and
`KF_WEBSITE_URL=https://qualizeal.com`. The Jira and Confluence `sync()` now
read `JIRA_DASHBOARDS` / `JIRA_BOARDS` / `CONFLUENCE_PAGES` from the environment
(mirroring the existing `JIRA_PROJECTS` / `CONFLUENCE_SPACES`), so the ingest
pulls the dashboard, board and page named in `showcase_sources.json`.

## Verification

- `tests/test_preflight_sources.py` (9): OK/401/403/404/network/warn/skip and
  the non-zero exit on a set-but-failing secret, offline via a mocked fetch.
- `tests/test_verify_and_smoke.py` (9): the fabric assertion (pass, low-count,
  preflight-OK-but-zero, github-repos-no-docs, jira board/by_status) and the
  smoke assertion (cited answers pass, a gap or uncited answer fails and names
  the source).
- `ruff` + `ruff format --check` clean; full suite green.

## Gate

- Preflight runs before ingest, one line per source, non-zero exit on a
  set-but-failing secret — yes.
- Post-ingest assertion fails the build if a source has 0 documents — yes.
- Smoke question per source returns a cited answer — 4/4.
- Failure messages distinguish credential (401/403/404) from connector bug — yes.

## How the keys get verified for real

These run in the GitHub Action (where the secrets live), not in a dev session.
Trigger the `ingest` workflow (`workflow_dispatch`) and read the preflight lines
in the run summary: they say, per source, OK / WARN / FAIL with the exact
reason — so a wrong token or an unshared dashboard is caught there.
