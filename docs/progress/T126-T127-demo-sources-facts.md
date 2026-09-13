# T126 / T127 — Every source answers with real facts, and all four show connected

Two demo defects from the same root cause: the baked showcase had **no facts
index**, so count questions had nothing to read and Jira/Confluence had no data.

## The bug (screenshot)

Asking *"how many repos are then in the github?"* returned a **test function**
(`tests.test_connectors.TestConnectors.test_github_allow_list_excludes_other_repos`)
instead of a count — because the chat path (`service.ask`) had no facts/count
tier, so the question fell into the code-symbol tier, which matched "github" +
"repos" against a test symbol. And Jira/Confluence connector cards read
*"not configured · never synced · items 0"* — the build never seeded them.

## T126 — a facts / inventory tier in the chat path

`answer/service.py` now runs `aggregate.analyse()` (the facts ladder that reads
`facts.json` — repositories, Jira issues, Confluence pages, documents,
spreadsheets) as **`_facts_answer`, ahead of the reasoning and code-symbol
tiers**, gated to genuine **count / inventory intent** (`how many`, `number of`,
`count of`, …). So:

- "how many repos are then in the github?" → *"The fabric covers N repositories …"*
  (a count, never a code/test symbol);
- the word "then" no longer decomposes it into a multistep gap (the facts tier
  runs before the planner);
- capability / "where is …" questions have no count intent, so they still route
  to the code / prose tiers unchanged.

Tokens are metered and a self-hosted compute cost imputed (T125), so a facts
answer is counted on the ROI dashboard like every other keyless answer.

`aggregate._p_confluence` gained a **single-space fallback** (mirroring the
existing single-project fallback in `_p_jira`), so "how many pages in Confluence"
resolves the one space without the reader naming it.

### Browser parity (`engine.js`)

The static demo runs on `engine.js`, which had no facts tier either. Added
`factsAnswer(question)` — the same count-intent gate, reading a compact baked
`SNAP.facts` summary — wired **before** BM25 retrieval, so a typed
"how many repos / Jira issues / Confluence pages / documents" answers at the
`facts` level in the browser too (verified via the parity runner).

## T127 — Jira + Confluence connected-with-data in the demo, like GitHub

The GitHub Pages showcase is static and cannot call Atlassian at runtime, so
`build_showcase.py` now **seeds the sources into the baked fabric**:

- `_load_jira` ingests a representative QualiZeal Jira backlog (one cited
  document per issue) and sets the `jira` connector cursor → the card reads
  **healthy · synced · items**, exactly like GitHub.
- `_load_confluence` ingests the demo Confluence space (one page per document)
  and sets the `confluence` cursor.
- `_write_facts` writes `facts.json` **after every loader** — repositories, the
  Jira project (with `by_status` / `by_type` and a board whose columns are named
  status groups), the Confluence space, and the real document total — counted
  from what was ingested so the cards, facts and answers all agree. It returns a
  compact summary baked as `snap["facts"]` for the browser engine.
- The count questions are added to the bake set, so each source's
  "how many …" resolves instantly (Level 0) in the static demo.

The seed is the company's **own demo dataset**, clearly the Internal showcase —
no external record is impersonated, and **no credential is stored or baked**: the
credential badge stays honest (Jira/Confluence show *"add JIRA_URL…"* for live
refresh, exactly as GitHub shows *"public only · add KF_GITHUB_TOKEN"*), while
the card is healthy and synced with data. Add / delete / re-add on the cards is
unchanged.

## Verification

- `tests/test_t126_facts_counts.py` (6) — with a "github repos" code symbol
  ingested, every count (repos, Jira issues, Jira bugs, Confluence pages,
  documents) answers from facts at the `facts` level and never as a code block;
  a genuine code question (no count intent) still returns the cited function.
- Parity runner: the browser engine answers the same counts at the `facts` level.
- Fresh `build_showcase` bakes the counts and the Jira/Confluence cards read
  `items 12` / `items 8`, `last_status ok`; `verify_showcase --dir` passes.
- `ruff` + `ruff format --check` clean.

## What the demo now shows

- Ask "how many repos / Jira issues / Confluence pages / documents" → the real
  number, cited, on every surface.
- Admin → Connectors: GitHub, Jira and Confluence all healthy and synced with
  item counts; add / delete / re-add still work.
