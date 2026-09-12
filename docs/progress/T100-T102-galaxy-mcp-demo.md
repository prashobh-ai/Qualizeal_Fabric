# T100–T102 — physics galaxy, v2 MCP tools, and the keyless capstone demo

The final group of the T91–T102 series: confirm the physics galaxy, add the two
remaining MCP tools, wire the Level-0 facts path into the governed keyless
service, and land the end-to-end demo.

## T100 — physics galaxy

Already built and shipping: vendored **vis-network 9.1.9**
(`surfaces/static/vendor/vis-network.min.js`, ~690 KB, no CDN) + `galaxy.js` —
activated concepts in coral, one-hop halo in blue, others dimmed to 0.04, a
500 ms flash on the answer's concepts and a camera fit to the activated set,
click-to-inspect, and an empty state showing the top concepts; both themes.
Mounted via `GALAXY_HEAD` and flashed on each answer in `ask_ui`
(`renderGalaxy` → `KFGalaxy.mount` + `flash`), baked into the showcase, and
covered by `tests/integration/test_galaxy.py`. This PR verifies it end to end
(the demo lights the first answer's concepts) — no rebuild needed.

## T101 — MCP tools

Two tools added to the MCP server (`mcp/server.py`), keeping the plain-function +
`build_server` registration pattern:

- **`jira_status(project, board?)`** — the Jira status snapshot from facts (T97):
  totals, the by-status breakdown, the board columns and the **exact
  in-progress count** (the sum over the "In Progress" column's statuses), the
  sprint, and freshness. Reads `data/facts.json` — no live call.
- **`corroborate(claim, sources?)`** — cross-source verification (T99): agreement
  or the specific discrepancy, citing both sources, via `answer.tools.corroborate`.

`ask_fabric`'s result now names the **provider** that answered
(`provider` / `provider_model`), so a keyless run is honestly labelled
`Open-source LLM` or `Extractive`. Both tools are in `TOOL_NAMES`, so the
server's registered set stays in lock-step with the declared list.

## Level-0 facts wired into the keyless service

`aggregate.try_answer` was never called by `AnswerService` — the Level-0 facts
path (counts, inventories, Jira boards, Confluence pages, sheet aggregates) was
only reachable through the agent's `query_facts` tool, so a **keyless** run fell
to retrieval for a "how many in progress" question. `AnswerService._facts_answer`
now answers those from the pinned facts **before retrieval** — exact,
as-of-dated, cited, no model — matching `aggregate`'s documented design and the
T97/T102 gates. Returns `None` for non-fact questions, so retrieval is unchanged.

## T102 — demo

`scripts/demo_v2.py` (+ `make demo` / `make demo-v2`) runs the KF-Instructions
narrative on the extractive floor, deterministic and creditless:

keyless cited answer (Open-source LLM) → citation expands to its paragraph →
code answer with a fenced snippet → **exact** in-progress count on the V1 board
with freshness → cross-source verification citing Jira and the repo → ROI
(hours saved, cost avoided, ratio) + reconciled observability + the
input/thinking/output token split → the answer's galaxy concepts lit — no step
leaves the app.

## Verification

- `tests/test_mcp_v2.py` — `jira_status` in-progress from facts (and no
  fabrication for an unknown project); `corroborate` agreement citing both
  sources; `ask_fabric` names the provider.
- `tests/test_demo_v2.py` — the capstone narrative runs end to end and every
  step's assertion holds (cited answer, paragraph expand, fenced code, exact
  board count with freshness, cross-source agreement, ROI + reconciliation, lit
  galaxy).
- Full `pytest` green; `ruff` clean; showcase build + parity pass.
