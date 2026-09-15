# T150 — the free GitHub Pages demo actually answers

_Fixes the three things that made the keyless, open-source Pages demo look broken:
a build-time crash, a class of questions that were turned into product-chip
clarify-stubs before retrieval was ever tried, and live-source ingest that could
never be pointed at a real org. This is the interim, zero-cost stand-in for the
role-aware orchestration platform (Cloudflare Workers + connectors + governed KB +
vector DB) until the vector DB and cloud approvals land — the browser engine keeps
doing grounded retrieval (RAG) over the baked corpus with citations and the
evidence graph; nothing here needs a key or a server._

## 1 — the build-time corpus crash (`scripts/ingest.py`)

`ingest.load_corpus` still called `build_showcase._load_code` and `_load_org`,
which **T141 deleted** (self-ingestion and org-filler were purged). Every showcase
build logged:

```
corpus  failed  AttributeError: module 'scripts.build_showcase' has no attribute '_load_code'
```

`load_corpus` now mirrors `build_showcase._seed`: `_load_corpus` (the authentic
QualiZeal briefs) + `_load_uploads` (documents committed via the Admin upload
path). No more crash; the corpus step ingests cleanly.

## 2 — content-rich questions were clarified instead of answered

The browser engine (and the mirrored server resolver `answer/context.py`) put two
patterns in the wrong bucket, short-circuiting retrieval with a product-chip
"Which one do you mean?" before the question was ever tried:

- **Existential/expletive "there"** was in the pronoun set, so *"Is **there** any
  code on login or SSO?"* and *"How many repositories are **there**?"* looked like
  a dangling reference and asked back — even though the repo count is a plain fact
  answer. `there` is removed from the pronoun regex in **both** `engine.js` and
  `context.py` (kept in lockstep for the parity check).
- **`integrate` / `migrate`** were in the "compare two entities" intent set, so
  *"Is it possible to integrate with Jira?"* asked *"Compare which two?"*. Those
  words are removed — an integrate/migrate question names its own target and is
  answered by retrieval.

Plus a defensive guard: a subject-less pronoun only asks back when the question is
a **bare reference** (< 2 content tokens). A content-rich phrasing falls through to
live retrieval, which answers it or reports an honest gap — never a manufactured
clarify. A genuine bare pronoun ("when was it made") and a bare "compare them"
still clarify, exactly as before.

Before → after (real engine, keyless build):

| Question | Before | After |
|---|---|---|
| Is there any code on login or SSO? | clarify (product chips) | **answer** — 6 grounded assets incl. Security & SSO Standard |
| How many repositories are there? | clarify (product chips) | **answer** — 6 repositories listed (facts) |
| Is there a test automation service? | clarify (product chips) | **answer** — Test Automation service |
| Is it possible to integrate with Jira? | clarify ("Compare which two?") | **answer** — Jira/Confluence connectors |
| when was it made | clarify | clarify (unchanged) |
| compare them | clarify | clarify (unchanged) |

## 3 — live-source ingest could never be pointed at a real org

A source secret (`KF_GITHUB_TOKEN`, `JIRA_*`, `CONFLUENCE_*`) **authenticates**;
it does not say **what** to fetch. With only the token set, the GitHub ingest
correctly skipped (`no GitHub org/user or repo connected`), and Jira/Confluence
skipped for the missing `JIRA_PROJECTS` / `CONFLUENCE_SPACES`. So "I added the
repo but nothing ingested" was expected — the scope was never provided.

`showcase.yml` now passes the ingest **scope** from repo **Variables** (empty by
default, so the stdlib-only build is unchanged):

| Variable | Points the build at |
|---|---|
| `KF_GITHUB_ORG` | a GitHub org/user to pull repositories from |
| `KF_GITHUB_REPOS` | explicit `owner/repo` list |
| `JIRA_PROJECTS` | Jira project/board keys |
| `CONFLUENCE_SPACES` | Confluence space keys |
| `KF_WEBSITE_URL` | a public site to crawl |

`github_live.sync` reads `KF_GITHUB_ORG` / `KF_GITHUB_REPOS` (KF-prefixed because
GitHub Actions restricts setting `GITHUB_`-prefixed env from repo variables). Set
`KF_GITHUB_ORG` (once the org is approved — a 403 there is an org-admin action, not
a code one) and re-run the build to fill the Repositories tiles from the live org;
the 6 repositories otherwise come from the seeded demo `SAMPLE_REPOS` and answer
today.

## Where the LLM is (and where it's going)

On GitHub Pages there is no server and no key, so answers are grounded **retrieval
over the baked corpus** — real RAG (BM25 retrieval → grounded compose → citations →
evidence graph), honestly labelled as the extractive/open-source path. A funded
build-time Anthropic key bakes real Claude answers (T147); the live per-question
Claude + vector DB is the Cloudflare/Workers destination that arrives with the
cloud approvals. A **follow-up** will add an opt-in in-browser open-source LLM
(WebLLM/WebGPU) for live generative RAG over the retrieved passages, gated with the
fixed retrieval as the always-on fallback.

## Gate

`tests/unit/test_ask_routing.py`:
- a fast, build-free check on `answer/context.resolve` (existential "there" and
  integrate/migrate resolve to retrieval; a bare pronoun and a bare "compare" still
  clarify);
- a real-engine check (`scripts/showcase/ask_runner.js` over a 20-doc build): the
  questions a visitor types come back as answers, not clarifies.

## Verification

`node --check` clean on `engine.js` + `ask_runner.js`; ruff + format clean;
`test_ask_routing`, `test_t26_context`, `test_t32_parity`, `test_services_question`,
`test_t126_facts_counts`, `test_corpus_authentic` pass; full `build_showcase` +
`verify_showcase` clean.
