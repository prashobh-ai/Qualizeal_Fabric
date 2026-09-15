# T166 — the live, complexity-routed Claude call (PR #1 of the live-LLM series)

_Adds the one thing the demo was missing: a real Claude call on top of the existing
retrieval, routed across three model tiers by the complexity the engine already
computes. Every other module (ingestion, active-set switching, retrieval, counts, the
three UIs) already works — this makes the answer read as a real assistant with a real
model, real input/output tokens and real cost, instead of an extractive snippet._

## What it does

`scripts/showcase/engine.js`:

- **`pickModel(level, why)`** routes by the level the engine already computes:
  - L0/L1 facts & verbatim lookups → **extractive, $0** (no model — a fact needs no generation);
  - L2 summarise → **`claude-haiku-4-5`** ($1 / $5 per MTok);
  - a comparison / why-how / ≥3 relationships / L3 → **`claude-sonnet-4-6`** ($3 / $15);
  - ≥4 docs, a contradiction, or a cross-source verify → **`claude-sonnet-5`** ($2 / $10, used sparingly).
  All three are current, verified model ids (no fictional `4-8`).
- **`llmCompose(question, passages, model)`** makes the direct browser → Anthropic call
  (`x-api-key` + `anthropic-dangerous-direct-browser-access`), grounded ONLY on the
  answer's passages, and returns the composed text plus real `input_tokens`,
  `output_tokens`, cost (from the inline price table) and latency. A guard refuses any
  model outside the three allowed ids before the call.
- **`maybeCompose(a, question)`** re-composes an eligible Level-2/3 retrieval answer
  with the routed model, keeping the `[cN]` citations; on any error (no key, 401/429,
  network) it keeps the extractive answer and labels the fallback reason, so an answer
  always appears. It is fed only from the answer's grounded passages — which come from
  `activePassages()` — so a switched-off source never reaches the model.
- The card now shows the **real model id, tokens and cost**; no answer path emits
  `"demo model"` any more.

## The key: build-injected, never committed

The committed source carries a placeholder `__ANTHROPIC_BROWSER_KEY__`; `kfLlmKey()`
treats the placeholder as "no key" → the extractive core answers and the demo works
with no key at all. When the repo/owner sets the **opt-in** secret
`ANTHROPIC_BROWSER_KEY`, `showcase.yml` replaces the placeholder in the **built**
`engine.js` after the build (only in the ephemeral deploy artifact), and the deployed
Pages app calls Claude directly.

> **Security note (owner's decision):** on a public Pages site the key is visible in the
> served JS. Set a **low spend cap** on this key in the Anthropic Console and rotate it.
> The safer alternative is a tiny free proxy (Cloudflare Worker) that holds the key
> server-side; this PR implements the direct-call path the owner chose, with the
> extractive fallback as the safety net.

## Gate

- `tests/unit/test_live_llm.py` — static checks that the shipped engine carries the
  endpoint, the browser-access header, the three model ids, `pickModel`/`llmCompose`
  and the placeholder (never a real `sk-ant-` key), no `"demo model"`; that
  `prices.json` prices all three models; and that `verify_showcase` fails a built
  engine missing the wiring.
- `verify_showcase.py` asserts (on the built engine) the Anthropic endpoint, the three
  model ids, `pickModel`, and no `"demo model"` — so a regression that drops the live
  call fails the deploy.
- The keyless path is unchanged: `test_ask_routing` and the parity check still pass
  (with the placeholder key, `maybeCompose` is a no-op and the extractive core answers).

## What it looks like once the key is set

- `what is QMentisAI` → extractive/Haiku (Level-1 fact), cheapest.
- `compare QMentisAI and ValidAIte for test automation` → **Sonnet 4.6**, a written,
  cited comparison, real tokens + cost on the card.
- a ≥4-doc / cross-source verify question → **Sonnet 5**.
  Admin → Models then shows three models with real, different token/cost numbers — the
  visible, cost-effective model-switching story.

## Not in this PR (the rest of the series)

Dashboards wiring (tokens/latency/complexity series — T167/T155/T156), golden Q&A
(T157), repeat-cache + cost-saved (T158), the six Curator routes (T159), and the
user-management form (T160) build on the ledger rows this PR emits and follow as
PR #2/#3.

## Verification

`node --check` clean; ruff + format clean; `test_live_llm` (8) + `test_ask_routing` +
`test_t32_parity` pass; full `build_showcase` + `verify_showcase` clean with the T166
gate satisfied on the keyless build.
