"""T140 — the counts agree on every screen after a real in-browser upload/delete.

This is the proof that T138 landed *everywhere*, not just in the parser. It runs
the ACTUAL shipped ``engine.js`` under a Node browser shim (with the vendored
JSZip and the real ``upload_parse.js`` loaded exactly as the Admin/Curator pages
load them), uploads a synthetic DOCX (real paragraphs + a table) and PPTX (real
per-slide passages + embedded media) built with the standard-library ``zipfile``,
and asserts — with no reload between steps — that:

* the Workspace corpus tiles move by the measured amount (documents +1, passages
  +N, tables +T, images +I) and revert on delete;
* the Admin Files connector card and the ``/admin/uploads`` list agree with the
  Workspace document delta (one active set, three screens);
* the uploaded document is retrieved and cited on the very next question, with its
  real coordinate (``… · ¶N`` for the DOCX, ``… · slide N`` for the deck);
* the upload persists across a reload and a re-upload of the same bytes is deduped
  by content hash.

Node runs in the CI ``mcp`` job; the test skips cleanly where Node is absent. A
small corpus slice keeps the one build fast (the full Pages build is verified
elsewhere).
"""

from __future__ import annotations

import base64
import io
import json
import os
import shutil
import subprocess
import tempfile
import unittest
import zipfile

os.environ.setdefault("KF_MODEL_MODE", "off")

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RUNNER = os.path.join(ROOT, "scripts", "showcase", "counts_runner.js")
JSZIP = os.path.join(ROOT, "knowledge_fabric", "surfaces", "static", "vendor", "jszip.min.js")
PARSER = os.path.join(ROOT, "knowledge_fabric", "surfaces", "assets", "upload_parse.js")


def _docx_bytes(paragraphs: list[str], table_rows: list[list[str]]) -> bytes:
    """A minimal DOCX: enough OOXML for the regex parser to read paragraphs + a table."""
    body = "".join(f'<w:p><w:r><w:t xml:space="preserve">{t}</w:t></w:r></w:p>' for t in paragraphs)
    if table_rows:
        rows = "".join(
            "<w:tr>"
            + "".join(f"<w:tc><w:p><w:r><w:t>{c}</w:t></w:r></w:p></w:tc>" for c in row)
            + "</w:tr>"
            for row in table_rows
        )
        body += f"<w:tbl>{rows}</w:tbl>"
    doc = (
        '<?xml version="1.0"?>'
        '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
        f"<w:body>{body}</w:body></w:document>"
    )
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("[Content_Types].xml", '<?xml version="1.0"?><Types/>')
        z.writestr("word/document.xml", doc)
    return buf.getvalue()


def _pptx_bytes(slides: list[list[str]], media: int) -> bytes:
    """A minimal PPTX: per-slide ``<a:t>`` runs + N embedded media stubs."""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("[Content_Types].xml", '<?xml version="1.0"?><Types/>')
        for i, texts in enumerate(slides, 1):
            runs = "".join(f"<a:p><a:r><a:t>{t}</a:t></a:r></a:p>" for t in texts)
            z.writestr(
                f"ppt/slides/slide{i}.xml",
                '<?xml version="1.0"?>'
                '<p:sld xmlns:p="http://schemas.openxmlformats.org/presentationml/2006/main" '
                'xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main">'
                f"<p:cSld><p:spTree>{runs}</p:spTree></p:cSld></p:sld>",
            )
        for j in range(media):
            z.writestr(f"ppt/media/image{j + 1}.png", b"\x89PNG\r\n\x1a\n")
    return buf.getvalue()


def _office_json() -> str:
    docx = _docx_bytes(
        [
            "Project Zylophonic Quibberflux Overview.",
            "The Quibberflux engine reconciles ledger drift across nine synthetic tenants.",
            "Its throughput target is 4200 reconciliations per minute.",
        ],
        [["Metric", "Value"], ["Throughput", "4200/min"], ["Tenants", "9"]],
    )
    pptx = _pptx_bytes(
        [
            ["Governed SDLC Overview", "Vorplex introduction stage"],
            ["Planning Phase", "Grindlewax story point estimation"],
            ["Build Phase", "Snorkblat continuous integration pipeline"],
            ["Test Phase", "Wobblenock regression suite"],
            ["Release Phase", "Flimberdoodle canary rollout"],
        ],
        media=3,
    )
    return json.dumps(
        {
            "docx_b64": base64.b64encode(docx).decode(),
            "pptx_b64": base64.b64encode(pptx).decode(),
        }
    )


@unittest.skipIf(shutil.which("node") is None, "node not available")
class TestCountsAgree(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from scripts import build_showcase

        cls.tmp = tempfile.mkdtemp(prefix="kf-counts-")
        cls.out = os.path.join(cls.tmp, "showcase")
        os.environ.setdefault("KF_SHOWCASE_CORPUS_LIMIT", "8")
        build_showcase.build(cls.out)
        cls.office = os.path.join(cls.tmp, "office.json")
        with open(cls.office, "w", encoding="utf-8") as fh:
            fh.write(_office_json())

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def test_counts_agree_across_screens_after_upload_and_delete(self):
        res = subprocess.run(
            [
                "node",
                RUNNER,
                os.path.join(self.out, "engine.js"),
                os.path.join(self.out, "snapshot.json"),
                self.office,
                JSZIP,
                PARSER,
            ],
            capture_output=True,
            text=True,
            timeout=120,
        )
        self.assertTrue(res.stdout.strip(), f"no runner output; stderr={res.stderr[-2000:]}")
        out = json.loads(res.stdout)
        failed = [c for c in out.get("checks", []) if not c["ok"]]
        detail = "\n".join(f"  FAIL {c['name']} — {c['extra']}" for c in failed)
        self.assertTrue(
            out.get("ok"),
            f"counts-agree checks failed (error={out.get('error')}):\n{detail}",
        )
        # a real run exercises every assertion, not an empty list
        self.assertGreaterEqual(len(out.get("checks", [])), 15)


if __name__ == "__main__":
    unittest.main()
