# Cross-source verification

Corroborating a claim across sources — "Jira says V1-42 is done; does the code
show it?" — is a first-class capability, real now for the Jira-vs-repo case and
built to generalise to any source pair **without rework**.

## The interface

The corroboration engine (`knowledge_fabric/answer/cross_source.py`) is:

```
verify(platform, principal, claim, *, live_jql=None) -> CrossSourceResult
```

It resolves the entity named in the claim (today a Jira issue key like `V1-42`),
asks each source for its view, compares, and reports **agreement or the specific
discrepancy** with citations from *both* — never asserting beyond the evidence,
and saying what it could not find. It needs no model, so it works on the
open-source / extractive path the demo runs on.

Every source implements one small contract:

```
class Source:
    def state_of(entity)  -> dict   # the source's current view of the entity
    def evidence_for(entity, state) -> [Citation]   # what backs that view
```

- **`JiraSource.state_of`** — the issue's status, preferring a live JQL callable
  (`key = <KEY>`) and falling back to the ingested issue document's metadata;
  its citation is the issue URL.
- **`RepoSource.state_of`** — whether any commit or pull request references the
  key, read from the **relationships graph** (below); its citations are those
  commits/PRs.

The verdict: `agree` (Jira done **and** code references it, or both agree it is
open), `discrepancy` (Jira done but **no** code references it), `partial` (code
exists ahead of an open ticket), `insufficient` (the issue is unknown).

## Adding a source (next phase, no rework)

Adding a source — test results, deployments, a spreadsheet — is implementing its
`state_of(entity)` and `evidence_for(entity, state)` and naming it in `verify`.
The scan, the store, the router and the gate are **unchanged**. For example a
`TestsSource.state_of(key)` would answer "do the tests for this ticket pass?"
and slot into the same agreement/discrepancy report.

## The relationships graph

`knowledge_fabric/relationships.py` `scan(platform, tenant)` rebuilds a
tenant-scoped edge graph from what the fabric already holds: it reads every
ingested document, classifies it by uri (issue / pull_request / commit /
gh_issue / page / document), and records the Jira keys it **mentions** — but only
keys that are *known* issues, so noise like `UTF-8` never becomes a link. Edges
persist in the `relationships` store (`stores/relationships.py`), so
verification is a graph lookup, not a re-scan:

```
subject (commit acme/app@abc123)  --mentions-->  object (issue V1-42)
    evidence: the commit's url + title
```

The scan runs at the end of ingestion (`scripts/ingest.py`) and is idempotent —
it clears and rebuilds the tenant's edges each time, reflecting the fabric as it
stands. `platform.relationships.mentions_of(tenant, "issue", key,
subject_kinds=["commit", "pull_request"])` is the code-evidence lookup;
`neighbours(kind, id)` walks either direction.

## Surfaces

- **Answer path** — a question that asks to *verify / cross-check / confirm* and
  names an issue key (or the repo as a second source) routes to the cross-source
  answer before prose retrieval (`AnswerService._cross_source_answer`), returning
  the cited two-source result with a `verdict` in its explain.
- **Agent tool** — `corroborate(claim)` (in `answer/tools.py`) wraps the same
  engine, so the tool-using agent can call it when a model is available.

## Verification

`tests/test_cross_source.py`: the scan links a commit to the issue key it
mentions; verifying that issue reports **agreement** citing both sources;
verifying an un-referenced "done" issue reports the **specific discrepancy**; the
governed answer path routes a verify question end to end; an unknown key is
reported as insufficient, never asserted.
