# T95 — Deep token telemetry (thinking tokens and phases)

Builds the token-class depth on top of the existing T55/T56 insights (cost by
phase, active-vs-idle, waste, efficiency, burn rate, percentiles, `?` sheets).
Additive — nothing removed; the existing panels are unchanged.

## What's new

- **A first-class `thinking` token class.** `thinking` is the reasoning spend:
  the whole token count of tool-loop turns (`agent_step`), plus any per-call
  `thinking_tokens` a synthesis call reports (extended thinking, or the
  open-source summariser's intermediate tokens). Output excludes that reported
  thinking so the classes never double-count.
- **`insights.token_classes(days)`** → `{input, thinking, output, cache_read,
  cache_write, total}`. The five stack to `total` by construction and reconcile
  with the ledger's own input/output/cache sums (thinking is a *reclassification*
  of already-billed tokens, never new tokens). Added to `insights.overview` and
  the `?` definitions, alongside an aggregate `waste` line.
- **Ledger `thinking_tokens` field** (`api_ledger.record` + `summary`), read from
  a call's `usage`; additive and defaults to 0.
- **Answer contract**: `Answer.thinking_tokens` (+ `to_dict`), populated from the
  agent payload (all tool-loop turns except the final synthesis output). The
  Workspace card shows `… in · N thinking · … out` when an answer did reasoning.
- **Admin → Models**: an input/thinking/output/cache **stacked bar** with a
  legend, a **waste** KPI (amber past the target), plus the existing efficiency,
  burn-rate, provider-quota and cost-by-phase chips — each with its `?` sheet.

The token-meter concepts (phase cost, active/idle, waste, efficiency, quota,
percentiles) remain a clean-room re-implementation credited in
`THIRD_PARTY_NOTICES.md`.

## Verification

- `tests/test_token_classes.py`: the ledger records `thinking_tokens`; the five
  classes stack to `total` and reconcile with the ledger's summed
  input/output/cache; `agent_step` turns count wholly as thinking and a reported
  synthesis-thinking split moves those tokens out of `output`; `overview` exposes
  `token_classes`, `waste` and their definitions; efficiency stays in `[0,1]`.
- Existing telemetry tests (T55/T56, provider ledger) unchanged and green. Full
  `pytest` green; `ruff` clean; showcase build + parity pass.
