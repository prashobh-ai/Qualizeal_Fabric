# T131 (Phase 3) — Upload a PPT/PDF/Excel/Doc that really lands in the repo

The user asked that a document uploaded from the Admin UI *"go and sit in our repo
real-time and could also be deleted real-time from the UI"*, and chose *"really
commit to the repo (GitHub Action)"*. The static GitHub Pages showcase has no
server and must never hold a token, so this is delivered in three honest layers.

## 1. Stdlib-first PPTX / PDF parsing (so committed files become real content)

`knowledge_fabric/adapters/converter.py` gains stdlib fallbacks used when the
heavy Docling extra is absent (the Pages build runner):

- **`_pptx`** — unzips the `.pptx` and reads each slide's `<a:t>` runs in slide
  order, one region per slide (reliable, entities unescaped).
- **`_pdf`** — inflates the content streams (FlateDecode via `zlib`) and pulls the
  string literals inside text objects (`BT`…`ET`); good for text PDFs, and raises
  loudly for scanned/image-only PDFs rather than emitting noise (those need Docling).
- DOCX already had a stdlib path; XLSX/CSV go through `ingestion.tables` (T41).

So a committed slide deck or spreadsheet is parsed to full text at build time
with no extra dependency.

## 2. The upload is real in the browser at once (invisible mock)

`scripts/showcase/engine.js` turns `POST /admin/upload` (and `/curator/upload`)
from a no-op stub into a real in-browser operation, persisted per visitor
(`localStorage: kf.uploads`):

- The file is added to the **Files** source immediately and injected into the BM25
  index (`applyUploads` re-injects idempotently over the pristine baked index and
  resets the memoised index), so it is **answerable and citeable at once** — the
  document count, the Files connector card and retrieval all reflect it.
- Text files are read in-browser; a binary Office/PDF file is answerable by title
  and a placeholder line until its full text is indexed on the next build.
- `POST /admin/uploads/delete` removes it from the browser (and, when configured,
  the repo); `GET /admin/uploads` lists the visitor's uploads. The Admin UI shows
  an **Uploaded files** list with a per-file **Delete**.
- The Files source is a normal connector card: toggling it off (Phase 1)
  deactivates every uploaded doc, like any other source.

Verified with a Node harness against the real `engine.js`: upload → the doc is
cited in the answer with its real content → delete → it is no longer cited and the
list is empty.

## 3. Really committing to the repo (GitHub Action)

- `.github/workflows/upload_commit.yml` (`repository_dispatch: fabric-file-upload`
  + `workflow_dispatch`) decodes the base64, writes the file under
  `corpus/uploads/` (path sanitised to a single basename, ≤ 25 MB), commits to
  `main`, and the push triggers `showcase.yml` to rebuild Pages. `op: delete`
  removes the committed file the same way.
- `build_showcase._load_uploads` ingests everything under `corpus/uploads/` into
  the **files** source through the real pipeline, so a committed document becomes a
  **permanent, full-text-indexed, citeable** source after the rebuild. (No uploads
  directory → a clean no-op, so a normal build and the tests are unaffected.)
- The page never holds a token. When the operator configures a small
  token-holding service (its URL is the page's `kf.commit_endpoint`), `engine.js`
  POSTs the file there `{op, path, filename, content_b64}` and that service fires
  the workflow. Without an endpoint the in-browser add/delete still stands — the
  demo looks identical either way.

## Verification

- `tests/test_t131_uploads.py`: the stdlib PPTX/PDF extractors, and
  `_load_uploads` ingesting a committed PPTX + text file into the Files source
  (answerable, cited); the no-uploads-dir no-op.
- Node upload harness: full add → retrieve → cite → delete lifecycle.
- `test_t32_parity`, `test_t47_surfaces`, `test_showcase`, `test_stage2_ui` green;
  `verify_showcase --dir` passes; `ruff` + format clean; `engine.js` /
  `admin_ui__js.js` `node --check` clean; `engine.js` free of external URLs.
