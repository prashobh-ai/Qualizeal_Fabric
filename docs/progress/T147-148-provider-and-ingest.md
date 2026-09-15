# T147 + T148 — finish the per-provider model bake and the build-time ingest

_Completes `instructions-llm-and-lists.md` T147 and T148 on the code side. Both are
wired end-to-end and **safe-dormant**: every path that needs an external credential
(a funded Anthropic key, an `OPENAI_API_KEY`, the GitHub-org approval + Jira/Confluence
secrets) degrades honestly and never takes the always-ships static demo offline, and
lights up automatically the moment that credential is provided — with no further code._

## Why the "real" bits can't be verified here (and how the gates stay safe)

The live `showcase` build's provider check fails with `HTTP 400 — credit balance too
low`, so the bake runs extractively; the GitHub org is not approved and the source
secrets are not set. So the real Claude/OpenAI tokens and the live-ingested repo/Jira
tiles cannot be produced or verified in this environment. Rather than hard-fail (which
would take the Pages demo offline), the gates bite **only when a provider/source
actually verified**:

- `verify_showcase._provider_bake_errors` fails only for a provider the manifest marks
  `available` whose `answers/<provider>/` set is empty — never for one that was not
  baked this build.
- `verify_showcase._source_ingest_errors` fails only for a source whose build-time
  preflight `ok` and not `skipped` — a missing secret (skipped) or a 403 org-approval
  (failed, recorded on the card) never fails the deploy.

Both are covered by fast, build-free unit tests (`tests/unit/test_provider_gates.py`).

## T147 — per-provider bake, provider radio, model routing

- **build_showcase** writes the current build's answers to `answers/<provider>/<hash>.json`
  (`_bake_provider_answers`) and a `providers` manifest (`_providers_manifest`) — the
  provider this build ran (open-source in the keyless demo) is `available`; Claude and
  OpenAI carry the honest reason they were not baked. `provider_status` is baked too.
- **engine.js** serves `answers/<selected>/<hash>.json`, falling back to the root
  `answers/<hash>.json`; `/api/providers` returns the manifest + selection, `GET
  /api/provider` reflects the visitor's choice, `POST /api/provider` switches it
  (persisted). The Level-0/1 label already names the active provider (PR-C); a real
  generation shows the model id, tokens and cost on the card (already wired) — those
  become non-zero once a funded build bakes the Claude set.
- **Admin → Models** gains a provider radio (Open-source / Claude / OpenAI): the baked
  provider is selectable; the others are disabled with their reason. Switching re-serves
  answers from that provider's set.
- Gates: `tests/unit/test_provider_gates.py` (manifest + verify rules) and
  `tests/unit/test_provider_radio.py` (the real engine: manifest, switch persists,
  baked question still answered).

## T148 — build-time ingest + preflight, honest source cards

- **showcase.yml** gains two **secret-gated, non-fatal** steps before the bake:
  `preflight_sources.py` then `ingest.py --all`, running only when a source secret is
  present (`if: env.KF_GITHUB_TOKEN != '' || …`) and installing the connector extras
  in-step. With no secrets the fast stdlib demo build is unchanged; with secrets they
  fill the Repositories / Jira / Confluence facts and write `data/preflight.json`.
- **build_showcase** bakes `snap["preflight"]` (`_preflight_for_snapshot`, honest
  `absent:true` when no preflight ran). **engine.js** serves it at `/api/preflight`.
- **Admin → Sources** cards show each source's build-time state from the preflight:
  `verified` / `not configured` (no secret) / `failed · <reason>` (e.g. a GitHub 403
  org-approval — an org-admin action, not a code one).
- Gate: `tests/unit/test_provider_gates.py::TestSourceIngestGate` (the verify rule
  bites only on a verified-but-empty source).

## What lights up when the owner provides the prerequisites

- Fund `ANTHROPIC_API_KEY` (or set `OPENAI_API_KEY`) → the provider check passes, the
  build bakes `answers/anthropic/` (or `answers/openai/`) with real tokens/cost, the
  radio switches to it, and Level 2/3 cards show the model, real tokens and real cost.
- Set `KF_GITHUB_TOKEN` / `JIRA_*` / `CONFLUENCE_*` (and get the GitHub org approved) →
  preflight verifies, ingest fills the tiles, and the Sources cards read `verified · N`.

## Verification

`node --check` on `engine.js` + the new runner; `ruff` + `ruff format` + `notices`
clean; `make showcase` + `verify_showcase` clean with `providers` + `preflight` present
and `answers/open-source/` baked; the provider/source gate unit tests pass; the
regression suites are run before the PR.
