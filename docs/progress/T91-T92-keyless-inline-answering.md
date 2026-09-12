# T91–T92 — Keyless open-source answering, inline (no GitHub-issue detour)

Two of the T91–T102 gaps: make the platform answer with no provider key, and
remove the "Get full answer" → GitHub-issue queue in favour of inline answering.
Additive — no existing path, persona or endpoint is removed.

## T91 — Open-source answering that runs keyless

The open-source path (`adapters/oss_model.py`) was already built (NLU → extractive
selection → optional DistilBART/flan-t5 rewrite gated behind local weights →
entity-containment guard → `tiktoken` token count → `$0` ledger row). This change
finishes T91:

- **`auto` is the default `KF_MODEL_MODE`** (`build_model_client`, `model_for_tier`).
  A keyless deployment now resolves `anthropic (if a key verifies) → oss →
  extractive` and **answers** instead of raising `ProviderUnavailable`. A verified
  provider key still wins; `anthropic` mode still fails loudly when explicitly set
  without a key (the T35 doctor path is untouched).
- **Honest label everywhere: `Open-source LLM · <summariser>`** (`Open-source LLM ·
  DistilBART` when the fluent summariser's weights are present, `Open-source LLM ·
  Extractive-NLG` otherwise) — never `mock`/`extractive`/`disabled`. Aligned in
  `oss_model.provider_label`, the server badge (`http_api._provider_badge`) and the
  MCP provider view.
- **Bug fixed:** `provider_label` was only a module function, so the server badge
  and MCP probed `hasattr(client, "provider_label")`, missed it, and mislabelled a
  live open-source answer as `Extractive core`. `OSSModelClient.provider_label()`
  is now a method, so the badge names the open-source path correctly.

The fluent DistilBART/flan-t5 summariser stays gated behind local weights
(`KF_OSS_SUMMARIZER` a model dir, or `KF_OSS_ALLOW_DOWNLOAD`); the deterministic
extractive-NLG composer is the always-on, network-free, testable behaviour, and
the bake workflow produces the fluent baked answers.

## T92 — Inline answering; the issue queue is gone

- **Deleted** `.github/workflows/ask.yml` and `.github/ISSUE_TEMPLATE/ask.yml`.
- **Workspace (`ask_ui__js.js`)**: removed the queue bar, the `issueUrl`/`startPoll`/
  `applyFull` polling, the per-browser `QUEUE`, and the "Queued questions" usage
  block. No control leaves the app. Baked answers from the bake workflow are still
  served (the `baked` badge stays).
- **Static browser engine (`scripts/showcase/engine.js`)**: no longer stamps
  `queue.eligible`; every Level 1/2/3 question is composed and answered in place by
  the open-source path, labelled `Open-source LLM · Extractive-NLG`, with a real
  input/output token estimate on the card. A baked fluent answer is served when
  present. The live server already answered inline (it never stamped `queue`).

## Verification

- `tests/test_oss_answer.py` (new): unset mode + no key resolves to the open-source
  client; the label starts with `Open-source LLM`; a keyless `ask` is grounded
  (cited) and not labelled mock/extractive/disabled; the server badge reports
  `Open-source LLM`.
- `tests/unit/test_oss_fallback.py` unchanged and green (label still
  `startswith("Open-source")`).
- Showcase build + parity and the served-page tests updated only in comments (the
  no-external-URL assertions still hold, with the issue detour gone).
