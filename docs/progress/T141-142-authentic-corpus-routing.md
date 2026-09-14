# T141 + T142 — authentic corpus, and documents win the answer

_Implements instructions.md (Foolproof Fix) **T141** (purge every non-authentic
document) and **T142** (fix the routing so documents win)._

Two defects made "what all services are given by QualiZeal?" answer with **Hr Leave
Policy** (or an empty citation to a `.py` test file):

1. The fabric held documents that are not QualiZeal's knowledge — four invented
   `corpus/org/*.md` files, and this repository's own Python, tests and README
   (self-ingested with `github://…/Qualizeal_Fabric/…` URIs).
2. A code-tier rule fired whenever any query token ≥ 3 chars appeared as a
   **substring** of a passage symbol or path — so `all` in "what **all** services"
   matched `test_list_**all**_merges_registry` and returned that test function
   before any document was considered.

## T141 — the fabric holds the organisation's knowledge only

- **Deleted** `corpus/org/hr_leave_policy.md`, `onboarding_guide.md`,
  `security_sso_standard.md`, `test_automation_playbook.md` and removed their
  loader (`_load_org`) and the org seed questions.
- **Stopped self-ingestion**: removed `CODE_REPO` / `CODE_FILES` / `TEST_FILES`,
  `_load_code` and the repository-overview card (`_repo_card`) from
  `build_showcase.py`. The fabric no longer contains its own source, tests or
  docs. The `repositories` facts still come from the demo `SAMPLE_REPOS`, so the
  GitHub source and "how many repositories" are unaffected.
- **Renamed** the two Confluence demo pages that read as meta filler
  ("Onboarding a New Source" → "Connecting Data Sources"; "Model Cost Playbook" →
  "Cost-Aware Model Routing").
- **Connector exclusion** (`knowledge_fabric/connectors/github.py`): the live
  GitHub connector now skips `tests` / `test` / `__tests__` / `spec` / `.github`
  / `docs/progress` directories and `test_*.py` / `*_test.py` / `*.spec.*` /
  `conftest.py` files, so a real client repo's tests never enter the fabric.
- **Gate**: `tests/unit/test_corpus_authentic.py` asserts, on the built snapshot,
  that no document URI contains `Qualizeal_Fabric`, no passage symbol starts with
  `tests.`/`test_`, and no forbidden title appears. `verify_showcase.py` runs the
  same check, so a bad corpus fails the Pages build.

The corpus after this is exactly the vendored QualiZeal `corpus/*.docx` briefs,
`corpus/uploads/`, and the baked website / GitHub / Jira / Confluence demo sources.

## T142 — a list question lists the service documents

In `scripts/showcase/engine.js`:

- **Code tier only on code intent.** The code branch now fires only when the
  question actually asks about code (a `function|class|where is|how does … work|
  code|module|repo…` intent) **or** a query token equals a *whole* symbol/path
  identifier (never a substring) and is not a generic word (`all`, `list`, `get`,
  `test`, …). It also never returns a near-empty code answer — it falls through
  to the documents instead of citing a bare symbol.
- **List intent.** A "what all services / which services / list services" (or the
  products equivalent) question is answered as a Level-0 **list of the distinct
  service (or product) document titles** — up to 20, each its own citation —
  resolved before coreference/clarify and before the baked cache, so it can never
  be turned into a clarify-back or a single-document baked answer. It runs over
  the active index, so a toggled-off source drops from the list.
- **Area on every baked document.** `build_showcase.py` writes each document's
  `area` (`Product` / `Service` / `Company`) from the corpus filename prefix, so
  the engine can group and list by it. Uploaded / connector documents carry an
  empty area and are correctly excluded from the service list.
- **Gate**: `tests/unit/test_services_question.py` runs the real `engine.js` under
  a Node browser shim over a 20-document build and asserts each phrasing returns
  `kind=answer` with ≥ 10 service citations, every title a service name, none a
  `.py` path. (Verified answer on that slice: *"QualiZeal offers these services:
  Functional Testing, Test Automation, Performance Engineering, Security Testing,
  … AIML Model Testing"* — 13 citations, 0 code.)

## Verification

`node --check` clean on `engine.js` + `services_runner.js`; the two gate tests
pass; `verify_showcase` passes (authentic check + no external URLs); ruff clean.
The regression suites are run before the PR.

## Deferred (PR-B, per the plan)

- **T143** — delete then re-add a source (restore route + removed-state card).
- **T144** — an obvious upload drop zone on Admin **and** Curator.
