# T146 + T149 + T147 — noun-scoped lists, live usage/galaxy, the model made visible

_Implements instructions-llm-and-lists.md. **T146** (list intent scoped by the noun)
and **T149** (live usage + galaxy) land in full; **T147** lands its verifiable slice
(the provider is named on every answer and its status is in the snapshot). The
remaining T147 work — the per-provider real-token bake, the provider radio and
Level 2/3 model routing — is gated on the Anthropic key having credit and is called
out below._

## What the live build actually showed (finding B is real)

Before writing code we read the latest `showcase` Actions run. The provider check
(`doctor.py --require anthropic`, already in `showcase.yml`) **failed** with
`HTTP 400 — "Your credit balance is too low to access the Anthropic API"`, so the
`continue-on-error` step selected `KF_MODEL_MODE: extractive`, the bake made
`API calls: 0`, all 64 answers are extractive and no `provider_status` reached the
snapshot. So the root cause is **billing, not code** — the workflow already wires the
doctor, the mode selection and the exit-7 guard correctly. No code change makes real Claude
calls until the account has credit (or a funded key is set in Actions secrets); that
is the T148-style "needs the account owner, not code" case.

## T146 — a list question is scoped by its noun (fixes A)

`scripts/showcase/engine.js`: `LIST_INTENT` (which fired on any "what all …") is
replaced by a **verb + noun** rule. A list verb (`what all` / `which` / `list` /
`what are the`) plus a list noun selects exactly one source:

| Noun | Answer |
|---|---|
| services / offerings / solutions / capabilities | Service-area document titles |
| products | Product-area document titles |
| pages / wiki / confluence | Confluence page titles, cited as pages |
| repos / repositories (or projects + github) | repository names from facts |
| boards / sprints / issues / tasks (or projects + jira) | the Jira project + status census |
| documents / files / uploads | uploaded (Files-source) documents |
| sources / connectors | the active source registry |

When the chosen noun's source is off or empty the answer says `No <noun> … yet` —
never another source's list. "how many" stays with the Level-0 facts counter for the
nouns it already covers (repos / issues / pages / documents), so those counts and
their tests are unchanged. A list verb with no list noun (e.g. "what all is happening
today") does not enter the list path. Verified: "what all pages … confluence" now
lists the 8 Confluence pages (it used to answer with the services). Gate:
`tests/unit/test_list_intent.py`.

## T149 — live usage and a lit galaxy on the workspace (fixes D)

- `engine.js`: `/api/usage` is computed LIVE from the event ledger (`usageFromEvents`),
  not the baked `usage[subject]` (which early-returned when a subject had no baked
  row, leaving the panel at 0). Today / 7 d / 30 d, tokens, cost and the level split
  move on the next question. Verified: three questions → Today = 3.
- `ask_ui__js.js`: when the baked galaxy has nothing for a live answer's trace, the
  galaxy is synthesised from the answer's own citations (the question at the centre,
  each cited document linked and activated) so a real answer lights up instead of
  "Nothing linked"; `gap`/`clarify` (no citations) still show empty.

Gate: `tests/unit/test_counter_sync.py` (ask 3 → Today = 3, a real answer lights the
galaxy).

## T147 — the model is named and its status is in the snapshot (partial)

Landed here (verifiable now, honest under the unfunded key):

- **The Model row never reads "No model needed".** `ask_ui__js.js` `modelLabel` now
  names the active provider even for a Level 0/1 answer that needed no generation —
  `Open-source LLM · Extractive-NLG`, or `Claude … · lookup, no generation` /
  `OpenAI … · lookup, no generation` when that provider is active. A real generation
  still shows the model id. `loadProvider` records the active provider for it.
- **`provider_status` is in the snapshot.** `build_showcase` bakes
  `snap["provider_status"]` from the doctor's `provider_status.json` when the key
  verified, else an honest fallback that records the real build mode and reason
  (e.g. "provider key not verified at build — served by the extractive core"). The
  engine serves it at `/api/provider/status`, and `verify_showcase.py` fails the
  build if it is missing.

Deferred — gated on the Anthropic key having credit (cannot be verified without a
real call), to be built as a dedicated follow-up so its gate can be proven green:

- the per-provider answer bake (`answers/<provider>/<hash>.json`) with real
  usage/cost/latency, the provider radio (Open-source / Claude / OpenAI) that swaps
  the baked answer set, and the Level 2/3 routing that shows the model, real tokens
  and real cost. These light up automatically once the key is funded and the
  showcase workflow re-runs.

## Verification

`node --check` clean on the changed `engine.js` / `ask_ui__js.js` and the new runner;
`ruff` + `ruff format` + `notices` clean; `make showcase` + `verify_showcase` clean
with `provider_status` present; the three new gates pass; the regression suites are
run before the PR.
