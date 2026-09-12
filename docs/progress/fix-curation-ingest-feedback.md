# Product-quality fixes — Admin ingest, curation queue/switch, feedback context

Fixes for five defects reported after running the deployed app. Each was
reproduced in the code (not guessed) before the fix; all are **additive** — no
existing path, persona, endpoint, dashboard or test is removed.

## 1 · "Not able to ingest data from admin side"

**Cause:** the Admin *Bulk upload* card only accepted **pasted text** (or a JSON
array). There was no file picker, so an admin who wanted to ingest a real
document (PDF / DOCX / XLSX / image / MD / CSV) had no way to do it — even
though the backend intake and `/admin/upload` already accept file **bytes**
(`content_b64`).

**Fix:** a real *Choose files…* control on the Admin upload card. Selected files
are read in the browser and staged as base64, then sent to the same multi-format
intake the connectors use (`intake.upload` → the 7-step pipeline). ACL applies to
the drop. Inline text and the JSON array still work unchanged.

## 2 · "There is no real curation queue and this is a dummy" · 3 · "is this even a switch?"

**Cause (the core bug):** `curation.on_ingest` — the T53 hook that reads a
source's curation mode and, under **manual**, holds a freshly-ingested document
in the review queue — was fully implemented and unit-tested **but never wired
into any ingest door.** So:

- switching a source (or the default) to **manual** persisted to
  `curation_settings` but had **no observable effect** — new content still went
  live → "is this even a switch?";
- the T53 review queue (`/curator/review`) was **always empty** → it read as a
  dummy. (The busy "low-confidence review" list the app also shows is a
  *different*, auto-derived queue — `/curator/gaps` — not the manual-review one.)

**Fix:** a new `curation.settle_ingested(platform, tenant, results, source=None)`
hook, called by the two **user-facing** intake doors:

- `POST /admin/upload` / `/curator/upload` (`http_api._upload`), and
- the connector sync (`SyncManager.sync`).

A source in **manual** mode now holds each ingested document in the review queue
(state `review`, held out of retrieval by the existing `live_filter`, logged
`ingested`) until a curator accepts it. A source in **automated** mode (the
default, and the whole authored/connector corpus) is a genuine **no-op** — the
document stays live-by-default with no log row, so automated ingestion never
floods the queue or the timeline. This is why the bootstrap corpus loader and
the test seed (which ingest via the worker directly, under automated) are
untouched: only content a curator explicitly put in manual review is held.

`/admin/upload` and the sync summary now also report `held_for_review`, surfaced
in the Admin toast ("N awaiting curator review").

**Switch affordance (#3):** the mode control is now a real two-state segmented
switch (Automated | Manual) with the active state highlighted, instead of a
single pill that flipped on click.

## 4 · "Negative feedback is not capturing the full context of that chat"

**Cause:** `POST /feedback` stored only `{subject, question, trace_id, level,
note}`. A curator reviewing a 👎 saw the question but never **what the AI
answered**, which **sources** it cited, or the **turns before it** — so they
could not tell what actually went wrong.

**Fix:** the Workspace now sends the full context of the flagged turn — the
answer text, the cited sources (title · locator · snippet), the persona/level,
the resolved `understood_as`, and the couple of turns before it. `/feedback`
stores them (trimmed to safe sizes) and the Curator's *User feedback* table shows
a "what happened in that chat" disclosure with the AI's answer, its citations
(or an explicit "no sources were cited"), and the earlier turns.

## 5 · "Are these features in the curator tab even working?"

With #2/#3 fixed the curation queue and switch are demonstrably real end-to-end
(switch → upload/sync → review queue → accept → answerable), and #4 makes the
feedback panel actionable. The other Curator panels were verified functional
against the running server.

## Verification

- `tests/test_curation_ingest_feedback.py` — new: `settle_ingested` (automated
  no-op; manual holds + logs; sync obeys a manual source) and served end-to-end
  tests (Admin upload under manual → review queue → held out of answers →
  accept → answerable; negative feedback captures answer + citations + context).
- Existing T53 curation-mode, review-gate and timeline tests unchanged and green
  (automated stays a no-op, so the seed still produces no curation log).
- Full `pytest` green; `ruff` clean; showcase build + parity check pass.
