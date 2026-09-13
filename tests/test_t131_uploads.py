"""T131 — uploaded PPTX/PDF/text documents become real, retrievable fabric docs.

Two layers are covered:

* the stdlib-first converter fallbacks (``_pptx`` / ``_pdf``) that let an Office /
  PDF upload ingest on a runner WITHOUT the heavy Docling extra — the static
  showcase build path;
* ``build_showcase._load_uploads``, which ingests documents committed under
  ``corpus/uploads/`` into the ``files`` source so a committed slide deck becomes a
  permanent, full-text-indexed, citeable document.
"""

from __future__ import annotations

import io
import zipfile

from knowledge_fabric.adapters.converter import DoclingLite


def _pptx_bytes(slides: list[list[str]]) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        for i, runs in enumerate(slides, 1):
            body = "".join(f"<a:t>{r}</a:t>" for r in runs)
            z.writestr(f"ppt/slides/slide{i}.xml", f"<p:sld>{body}</p:sld>")
    return buf.getvalue()


def _pdf_bytes(lines: list[str]) -> bytes:
    body = " ".join(f"({ln}) Tj" for ln in lines)
    return b"%PDF-1.4\nstream\nBT " + body.encode("latin-1") + b" ET\nendstream\n%%EOF"


class TestConverterFallbacks:
    def test_pptx_extracts_slide_text_in_order_without_docling(self):
        conv = DoclingLite()
        doc = conv._pptx(
            _pptx_bytes([["Nimbus Plan"], ["Move to AWS by Q4", "blue-green cutover"]]),
            "en",
        )
        texts = [r.text for r in doc.regions]
        assert texts == ["Nimbus Plan", "Move to AWS by Q4 blue-green cutover"]

    def test_pptx_unescapes_entities(self):
        conv = DoclingLite()
        doc = conv._pptx(_pptx_bytes([["Q3 launch &amp; scale"]]), "en")
        assert doc.regions[0].text == "Q3 launch & scale"

    def test_pdf_extracts_text_object_literals(self):
        conv = DoclingLite()
        doc = conv._pdf(_pdf_bytes(["Hello from ValidAIte", "Second line"]), "en")
        joined = " ".join(r.text for r in doc.regions)
        assert "Hello from ValidAIte" in joined
        assert "Second line" in joined


class TestLoadUploads:
    def test_committed_upload_ingests_into_files_source_and_answers(self, tmp_path, monkeypatch):
        import scripts.build_showcase as bs
        from knowledge_fabric.answer.service import AnswerService
        from knowledge_fabric.surfaces import http_api
        from knowledge_fabric.tenants import demo

        up = tmp_path / "uploads"
        up.mkdir()
        (up / "octo-runbook.txt").write_text(
            "OCTOPUS incident runbook. Page the on-call and post status every fifteen minutes."
        )
        (up / "nimbus.pptx").write_bytes(_pptx_bytes([["Nimbus migration to AWS by Q4"]]))
        monkeypatch.setattr(bs, "UPLOADS_DIR", str(up))

        p = http_api.Platform(db_path=":memory:", blob_root=str(tmp_path / "blobs"))
        demo.seed(p, [bs.TENANT])
        n = bs._load_uploads(p)
        assert n == 2

        svc = AnswerService(p)
        prin = demo.principal_for(p, bs.TENANT, "admin")
        a = svc.ask(prin, "what is the octopus incident runbook")
        assert a.kind == "answer"
        titles = " ".join(
            (getattr(c, "document_title", "") or "") for c in (a.citations or [])
        ).lower()
        assert "octo" in titles

    def test_no_uploads_dir_is_a_noop(self, tmp_path, monkeypatch):
        import scripts.build_showcase as bs
        from knowledge_fabric.surfaces import http_api
        from knowledge_fabric.tenants import demo

        monkeypatch.setattr(bs, "UPLOADS_DIR", str(tmp_path / "does-not-exist"))
        p = http_api.Platform(db_path=":memory:", blob_root=str(tmp_path / "blobs"))
        demo.seed(p, [bs.TENANT])
        assert bs._load_uploads(p) == 0
