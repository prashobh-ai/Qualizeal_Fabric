# T138 + T140 — real in-browser document parsing, live everywhere

_Implements instructions-fabric-state.md **T138** (real DOCX/PPTX parsing) and
**T140** (counts agree on every screen). Built on the merged active-set plumbing
(T137 was a means, not a goal — no fabric refactor)._

Uploading a document on the static showcase used to add a single title-only
placeholder passage: the tiles moved by one, but nothing inside the file was
searchable until a rebuild. Now the file is **really parsed in the browser** — its
paragraphs, slides, tables and embedded media become real fabric passages that are
retrieved, answered and cited on the very next question, in the same active set and
the same galaxy as the baked corpus.

## How it works (no network, no server)

DOCX / PPTX / XLSX are ZIP archives of XML. Two vendored, same-origin scripts do
the work, loaded in `<head>` on the Admin and Curator pages (`ui_common.UPLOAD_HEAD`):

- **`static/vendor/jszip.min.js`** — JSZip 3.10.1 (MIT), unzips the archive.
- **`assets/upload_parse.js`** — `KFUpload.parse(filename, bytes, opts)` reads the
  OOXML with plain regex (no DOMParser), so the identical code runs in the browser
  **and** under Node for tests. It yields real passages (one per paragraph / slide /
  table / row), extracted tables, image references, and a stable content hash.

`engine.js` decodes the uploaded bytes, calls `KFUpload.parse`, and injects the
resulting passages into `SNAP.index` over the pristine baked base (`applyUploads`),
each with a citation coordinate:

| Type | Passages | Coordinate | Tables / media |
|---|---|---|---|
| DOCX/DOCM | one per paragraph (+ one per table) | `file · ¶N` / `file · Table N` | `<w:tbl>` rows → Tables tile; `word/media/*` → Images tile |
| PPTX/PPTM | one per slide (speaker notes appended) | `file · slide N` | `ppt/media/*` → Images tile |
| XLSX/XLSM | one per sheet row (capped) | `file · Sheet N row R` | each sheet → Tables tile |
| CSV / TSV | one per row | `file · row R` | the file → Tables tile |
| MD / TXT / JSON / LOG | one per paragraph | `file · ¶N` | — |

## What is a real in-browser parse vs stored-but-not-parsed

- **Real in-browser parse** (searchable immediately, no reload): `docx`, `docm`,
  `pptx`, `pptm`, `xlsx`, `xlsm`, `csv`, `tsv`, `md`, `txt`, `json`, `log`.
- **Stored-but-not-parsed**: `pdf` — kept with a metadata passage and marked
  "stored; text extraction runs server-side on commit"; its full text is extracted
  by the build's stdlib `converter._pdf` once the file is committed to the repo
  (the existing T131 commit path). Any unknown/failed archive degrades to the same
  stored placeholder rather than a rejected upload.

## Counts agree on every screen (the T140 proof)

`corpusCounts()` derives the Workspace tiles from the active set, and now adds each
active upload's tables/images to the Tables/Images tiles. Because retrieval, the
ranking index, the tiles, the Admin Files card and the `/admin/uploads` list all
read the one active document set, an upload (or delete) moves them **together, with
no reload**. Dedupe is by content hash: re-uploading the same bytes (or a file whose
name is already baked) is reported as "already in the fabric" and not double-indexed.
Parsed passages persist in `localStorage: kf.uploads` (trimmed to ~4 MB), so they
survive a reload; **Reset demo data** clears them.

## UI

The Admin **Bulk upload** card shows the seven-stage ingestion strip
(`detect→convert→chunk→extract→graph→embed→health`) while the browser parses, then
stamps the real counts it produced onto the stages (convert→documents,
chunk/embed→passages, extract→tables). The uploads list shows each file's real
passage / table / image counts. The Curator "add document" text path routes through
the same parser as plain text.

## Verification

- **`tests/test_counts_agree.py` (T140)** runs the real `engine.js` under a Node
  browser shim with the vendored JSZip and real `upload_parse.js`, uploads a
  synthetic DOCX (paragraphs + a table) and PPTX (5 slides + 3 media) built with the
  stdlib `zipfile`, and asserts all of the above — documents/passages/tables/images
  tiles move by the measured amount and revert on delete; the Admin Files card and
  uploads list agree with the Workspace document delta; the DOCX is cited with
  `· ¶N` and the deck with `· slide N` on the next question; the upload persists
  across a reload; a re-upload is deduped. Skips cleanly where Node is absent.
- `node --check` clean on `engine.js`, `upload_parse.js`, `counts_runner.js`,
  `admin_ui__js.js`; the showcase builds and `verify_showcase` passes (no external
  URLs — both scripts are same-origin).
- JSZip registered in `ci/licence_manifest.json` (MIT, permissive) and
  `THIRD_PARTY_NOTICES.md`; `licence_gate.py` passes.
- Regression suites green: parity (T32), surfaces (T47), showcase, facts (T126),
  uploads (T131), licence (stage2).

## Deferred (follow-up PR, per the plan)

- instructions-fabric-state.md **T139** — delete-anywhere with an impact panel,
  tombstone and 30-day restore from both Curator and Admin.
