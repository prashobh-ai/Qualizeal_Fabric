# T125 — Real-time, consumption-driven Value & ROI

The Admin **Overview → Value & ROI** page was reading `MODEL SPEND $0.0000` and
`ROI RATIO —`, with `VALUE DELIVERED $4590` computed by a fed calculator
(`questions × minutes_saved/60 × loaded_rate`). Two problems, both fixed here:

1. **Token consumption was not measured on every answer.** The extractive /
   open-source-NLP fallback (the keyless path — no Anthropic/OpenAI key) recorded
   `cost = tokens_in = tokens_out = 0`. So spend was $0 by construction and the
   ratio was undefined.
2. **Value was a calculator we fed**, not a real-time read of activity.

Now value and the ROI ratio are derived from the **live question count** and the
**real token consumption of every answer** — paid provider, open-source LLM, and
extractive NLP alike.

## 1 — Real token consumption on every path

`answer/service.py` now measures tokens and imputes a compute cost on every
answer shape that runs without a paid provider:

- `_compose` (prose): the extractive floor counts real tokens (the prompt +
  retrieved evidence it read in, the answer it produced) via the same
  `count_tokens` used by the open-source client. The open-source LLM path already
  counted tokens; its `$0` provider cost is now replaced by the imputed compute
  cost.
- `_compose_code` / `_code_answer` (identifier tier), `_discovery_answer`,
  `_cross_source_answer`: each keyless answer shape now records real tokens + the
  imputed compute cost through one shared helper (`_oss_meter`).

A **paid** provider answer (`provider_cost > 0`) keeps its real, priced cost
untouched — imputation applies only to tokens a paid provider did not bill.

### Imputed self-hosted compute cost

`adapters/model.py` adds `oss_compute_cost(tin, tout)` at a representative
self-hosted rate — `OSS_COMPUTE_USD_PER_MTOK = 0.05` (USD per **million** tokens),
env-overridable with `KF_OSS_COMPUTE_USD_PER_MTOK`. Deliberately tiny next to a
frontier API (that gap is the ROI story), but **non-zero**, so open-source and
extractive answers still show real spend.

## 2 — Value & ROI from consumption, not a fed rate

`telemetry/roi.py::overview` reframed:

| Metric | Before (fed calculator) | Now (real-time, consumption-driven) |
|---|---|---|
| **Value delivered** | `hours_saved × loaded_rate` | Frontier-equivalent worth of the real tokens: `tokens_in × $in/M + tokens_out × $out/M` at the premium model's published price (`frontier_price()` from `prices.json`). |
| **Model spend** | analytics `total_cost` (=$0 keyless) | analytics `total_cost` — now **real** (paid API + imputed compute). |
| **ROI ratio** | `value / spend` (— when spend 0) | `value / spend` — a real number, because spend is now real. |

The minutes-saved / loaded-rate settings are **kept** but demoted to a *secondary
labour view* (`hours_saved`, `labour_value_usd`) — useful for leadership, but no
longer the definition of value and no longer needed for the ratio. New value-block
fields: `tokens_in`, `tokens_out`, `total_tokens`, `tokens_per_answer`.

Everything still reconciles with the analytics/SLA/insights panels (the
observability reconciliation check — dashboard totals vs raw span counters within
1% — still passes, since both read the same span costs).

## UI

`admin_ui.py` + `admin_ui__js.js`: the ROI tile row now leads with **Tokens
consumed** (total · per-answer · "every path") beside the questions count; **Value
delivered** shows the frontier-equivalent worth; the labour figure moves to the
Adoption line (`Nh saved ≈ $X labour value`). The settings row is labelled
"Labour view (secondary)". Because the showcase bakes `/admin/overview` from the
real `roi.overview()` output and `engine.js` serves that JSON verbatim, the
deployed Pages dashboard picks up the new shape with no engine change.

## Verification

- `tests/test_roi.py` — value + ratio are consumption-driven (tokens measured
  keyless, spend > 0, ratio a real number, frontier baseline named); the labour
  view stays settings-driven.
- `tests/test_answer_first.py` — the extractive floor now records real tokens and
  a tiny imputed cost (no paid model), instead of `$0 / 0 tokens`.
- Full suite green; `ruff` + `ruff format --check` clean.
- `verify_showcase --dir` passes on a fresh build; the baked `/admin/overview`
  carries the new fields.

## Answering the two questions from the screenshots

- *"Why is it failing even after a new ANTHROPIC_API_KEY?"* — that
  `showcase / build` failure was **not** the key (the provider check passes with
  the new key). It was a separate T117 login-gating regression, fixed in the
  `fix-showcase-login-bake` branch (PR #54). This task is the second screenshot.
- *"Value & ROI should not be a calculator … real-time value with real-time token
  consumption, even for the NLP / open-source fallback"* — done: value and the
  ROI ratio are now computed live from the question count and the measured token
  consumption of every answer, keyless paths included.
