# T143 + T144 — re-add a removed source, and an upload drop zone on both surfaces

_Implements instructions_2.md (Foolproof Fix) **T143** (delete then re-add a source)
and **T144** (an obvious upload drop zone on Admin **and** Curator). Builds on PR-A
(T141 + T142). The seven real QualiZeal documents are committed to `corpus/uploads/`
so they are in the baked baseline; the drop zone is for demonstrating live additions
on top of them._

## The seven documents (corpus, not UX)

`corpus/uploads/` now holds the seven real QualiZeal documents (the SOW at its final
name `Nova_x_QZ_Product_Knowledge_Fabric_SOW_v1_1.docx`, plus the GenAI playbook,
the two architecture plans, and three POC / overview decks). The build ingests them
through the real intake (`_load_uploads`) under the `files` source, so they are
permanent, full-text-indexed, citeable documents. Their titles do not lead with
`Product` / `Service` / `Company`, so they carry an empty `area` and never pollute
the T142 service list; their URIs are `file://…`, so they pass the T141 authentic
gate.

## T143 — delete then re-add (fixes B)

The defect: deleting a source removed its card, and there was no restore route, so it
could never be added back.

- **engine.js** — `connectorsPayload()` no longer filters deleted cards; a removed
  source stays listed carrying `_deleted:true`. New `addConn(source)` clears
  `_deleted`, re-enables the source and persists it; route `POST
  /admin/connectors/add {source}` calls it and emits `source_toggle`, so the same
  `kf:changed` recompute delete already fires runs on re-add. A deleted source stays
  out of retrieval (`disabledSources`) until restored.
- **admin_ui__js.js** — `connCard()` renders a `_deleted` source as a compact
  `removed`-state card with one **Re-add** button (guarding every full-card control).
  `renderConnectors()` wires it to `readdConnector()`; an "Add a source" click on a
  removed source restores it instead of only focusing a card. The delete confirm now
  says *"You can re-add it any time from this panel."*
- **http_api.py** — the FastAPI server mirrors the restore route: `POST
  /admin/connectors/add` → `conn_admin.upsert(enabled=True)`, shaped like the
  existing connector routes (never a bare 500).
- **Gate**: `tests/integration/test_readd.py` drives the real `engine.js` under Node
  and, twice, asserts: on delete the github card stays listed but flagged removed,
  the active connector count drops by one, the `repositories` tile is 0 and the
  repositories question is no longer a Level-0 facts count; on re-add the card is
  active again and counts / the facts answer return. (26 checks.)

## T144 — an obvious upload, on Admin and Curator (fixes C)

The defect: upload existed on Admin but was buried under "Bulk upload", and Curator
had no file input at all.

- **Admin → Sources** — a prominent dashed **drop zone** at the top of the Sources
  panel: *"Drop DOCX, PPTX, XLSX, CSV, MD here — or choose files"*, a primary
  **Upload documents** button, an ACL select, `accept=".docx,.pptx,.xlsx,.csv,.md,
  .txt,.pdf"`, multi-file. Drag-and-drop and the picker both parse in the browser and
  ingest at once, with a live per-file stage strip (detect → convert → chunk →
  extract → graph → embed → health) showing the real passage / table / slide counts.
  The upload core is factored into `doUpload(files, {stagesId, statusId})`, shared
  with the existing Bulk-upload batch.
- **Curator → Add a document** — the identical drop zone, wired to the same
  `/curator/upload` intake, with the paste-text form kept below as a secondary
  "…or paste text" disclosure.
- **PDF** — accepted and stored (`stored — text extracted at the next build`); never
  ingested to zero silently.
- **Gate**: `tests/integration/test_upload_dropzone.py` builds a snapshot with an
  empty uploads dir (so the drop genuinely adds), then uploads the REAL SOW through
  `/admin/upload` and `/curator/upload` (fresh engine each), asserting the endpoint
  parsed the DOCX (201 passages), the documents tile rose by one, and "summarize the
  Knowledge Fabric SOW" is answered with a `Nova…SOW… · ¶N` citation. No synthetic
  DOCX — the fixture is the real SOW.
  - Note: the SOW is retrievable and cited for natural questions naming it
    ("summarize the Knowledge Fabric SOW", "what is in the SOW", "statement of work
    scope"). The one phrasing "what is in the Knowledge Fabric SOW" gaps because
    "Knowledge Fabric" resolves as the product subject — a subject-resolution edge
    case, outside T144's upload scope, left for a future retrieval pass.

## Verification

`node --check` clean on the changed `engine.js` and the two new runners; the two
gate tests pass; the server connector tests pass; ruff clean. T145 (full rebuild +
`verify_showcase`, services answer, source coverage) is run before the PR.
