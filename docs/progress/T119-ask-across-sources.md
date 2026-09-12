# T119 — Ask across all four sources, and across them; generic pass

## Goal

After T118 connects GitHub, Jira, Confluence and the website from a pasted URL,
the demo must **answer** over each source and across them — every answer
keyless (the model-free extractive path, T106) with a citation that expands to
the paragraph or the code lines — and prove the whole flow is generic by running
it a second time against a personal GitHub repo and a personal website.

## What shipped

This task is the end-to-end proof that T118's newly connectable sources flow
through the existing ingestion → retrieval → cited-answer path. No engine
changes were needed — the T118 records (a Jira dashboard document, a Confluence
page, a website page, a GitHub code file) ingest as ordinary passages and the
answer service grounds on them.

### `scripts/demo_connect_ask.py` (new)

A self-contained, offline, deterministic narrative (`make demo-connect`):

1. **Connect** — four sources parsed from exactly what the browser shows, via
   `url_parse.parse_source_url` (printed so the reviewer sees the classification):
   a GitHub repo URL, a Jira dashboard URL, a Confluence page URL, a website.
2. **Ask** — each source answers with an expandable citation:
   - GitHub → "show the login implementation" → the function, as a code block,
     cited at `auth.py — login:4`.
   - Jira → "what is on the ValidAIte QA Status dashboard" → the dashboard's
     gadgets, cited to the dashboard.
   - Confluence → "who is the project lead / when is the kickoff" → the answer
     from the Project Plan page, cited at the paragraph.
   - Website → "what does QualiZeal offer for security testing" → the crawled
     page, cited.
3. **Cross-source** — "the Jira task V1-42 says done — is there a matching
   commit?" → **AGREE**, citing both Jira and the repo (T99).
4. **Generic** — the identical flow for a personal GitHub repo
   (`github.com/prashobh-ai/sideproject`) and a personal website
   (`prashobh.dev`) answers the same way, proving nothing is wired to QualiZeal.

Wired into `make demo` and its own `make demo-connect` target.

## Verification

- `tests/test_ask_across_sources.py` (8 cases): each of the four sources
  connected from a URL, ingested through the real pipeline via a fake transport,
  answers its question with a citation; the four questions cite four distinct
  documents; the cross-source question cites both Jira and the repo (agree); the
  generic personal-account pass (personal repo code answer + personal website
  answer) works identically.
- `ruff` + `ruff format --check` clean; full suite green; showcase build +
  parity pass.

## Gate

- Each source answers a question with an expandable citation. ✓
- The cross-source question cites both sources. ✓
- The demo script runs the generic pass with a personal account. ✓
