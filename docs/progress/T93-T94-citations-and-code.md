# T93–T94 — Citations expand to the paragraph; code answers show snippets

Restores the demo behaviour where a citation opens the exact paragraph, and
confirms code answers render as a fenced, line-anchored snippet. Additive.

## T93 — Citation expands to the paragraph in context

The data was already on every `Citation` (snippet, coordinate, passage_id,
document_title). This adds the server + UI that expand it in place:

- **`passages.context(tenant, passage_id, radius=1)`** (`stores/repositories.py`):
  the cited passage plus its in-document neighbours in reading order (page →
  paragraph for prose, start line for code), live (non-superseded) passages only.
- **`GET /api/passage/{id}`** (`http_api.py`): returns `{document_id,
  document_title, before[], passage, after[]}`, each entry with its resolvable
  `coordinate_render` and a source `url` when present. Any signed-in principal
  may ask; the passage ACL still gates it (403 for a restricted passage a public
  asker can't see, 404 for unknown).
- **Workspace (`ask_ui__js.js`)**: a citation chip now expands **inline** — the
  cited paragraph with the cited sentence highlighted, its neighbours above and
  below (fetched from `/api/passage/{id}`), and an **Open document** button
  (opens the page drawer). A second click on the same chip collapses it. On the
  static build the endpoint 404s and the cited snippet stands as the highlighted
  paragraph, so the affordance works everywhere; the browser engine also serves
  `/api/passage/{id}` from the snapshot when the build bakes it (`SNAP.passages`).

## T94 — Code answers show snippets (confirmed + language tag)

The T25 code path (`service._compose_code`) already renders a code answer as a
short prose line, the cited function verbatim in a fenced block, and a
line-anchored GitHub citation — never paraphrasing code, capped at two blocks by
persona depth. This PR confirms and locks it, and the citation expand (T93) now
renders a **code** citation as a fenced code block with an **Open on GitHub**
link (using the passage's `#L` URL) rather than a prose paragraph.

## Verification

- `tests/test_citation_expand.py`: `passages.context` returns ordered neighbours
  (never the target, subset of the doc) and `None` for an unknown id; the served
  `/api/passage/{id}` returns the cited passage + neighbours for a real answer's
  citation, 404 for unknown, 401 without auth.
- `tests/test_code_snippet.py`: a code answer carries a ```` ```python ```` fenced
  block with the verbatim function and a `#L` line-anchored `symbol_line`
  citation; at most two code blocks.
- Existing `test_t25_code` unchanged and green. Full `pytest` green; `ruff` clean;
  showcase build + parity pass.
