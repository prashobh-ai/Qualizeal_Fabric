# T99 — cross-source verification (Jira vs repo, extensible to any pair)

Corroboration across sources is now a first-class capability — real for the
Jira-vs-repo case and built to generalise without rework. Additive; keyless.

## What ships

- **The engine** `answer/cross_source.py` — `verify(platform, principal, claim,
  *, live_jql=None)`. Resolves the Jira issue key in the claim, pulls the issue's
  status (live JQL if available, else the ingested issue document) and the code
  references from the relationships graph, and reports **agreement or the
  specific discrepancy** with citations from **both** sources — never asserting
  beyond the evidence. Verdicts: `agree`, `discrepancy`, `partial`,
  `insufficient`. Two source adapters (`JiraSource`, `RepoSource`) implement the
  `state_of` / `evidence_for` contract that any future source will implement.

- **The relationships graph** — a new `relationships` table + store
  (`stores/relationships.py`) and a `relationships.scan(platform, tenant)` that,
  at the end of ingestion, reads every document, classifies it by uri (issue /
  pull_request / commit / …) and records the **known** Jira keys it mentions as
  idempotent edges (`commit --mentions--> issue`, with the commit's url as
  evidence). Wired into `scripts/ingest.py` and `platform.relationships`.

- **Routing** — `AnswerService._cross_source_answer`: a question that asks to
  *verify / cross-check / confirm* and names an issue key routes to the
  cross-source answer before prose retrieval, returning the cited two-source
  result (verdict in its explain). No model required.

- **Agent tool** — `corroborate(claim)` in `answer/tools.py` wraps the same
  engine for the tool-using agent, added to `SEARCH_ORDER`.

- **`docs/CROSS_SOURCE.md`** — the extension interface: `verify(claim,
  sources[])`, `state_of` / `evidence_for`, and how the relationships graph makes
  verification a lookup. Adding a source is implementing its two methods — the
  scan, store, router and gate are unchanged.

## Done when (met)

- The Jira-vs-repo verification question returns a cited, two-source answer
  stating agreement or the specific gap. ✓
- `docs/CROSS_SOURCE.md` defines the extension interface. ✓
- The `relationships` table links Jira keys to the PRs/commits found during
  ingestion. ✓

## Verification

`tests/test_cross_source.py` (offline): the scan links a commit to the issue key
it mentions and leaves an un-referenced issue unlinked; verifying the linked
issue reports agreement citing both sources; the un-referenced "done" issue
reports the specific discrepancy; the governed answer path routes a verify
question end to end; an unknown key is reported insufficient, not asserted. Full
`pytest` green; `ruff` clean; showcase build + parity unaffected.
