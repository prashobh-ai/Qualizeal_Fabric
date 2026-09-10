"""DocumentConverter (Docling-class): bytes -> regions with precise coordinates.

Every modality from the checklist is handled and every region carries a
resolvable coordinate (I2):
  * markdown / plain / pdf-text  -> page + paragraph
  * csv / tsv (tables)           -> row/col cell
  * code (.py/.js/.go/...)       -> symbol + line
  * transcript ([mm:ss] lines)   -> timestamp
  * scans/images (.ocr.txt)      -> page + bbox (OCR stand-in)

Real PDF/image binaries would plug a Docling/OCR engine in behind the same
``convert`` method; here synthetic corpora are UTF-8 text so the coordinate
math is exact and testable offline.
"""

from __future__ import annotations

import csv
import io
import re

from ..contracts.types import ConvertedDocument, Coordinate, CoordinateKind, RawItem

_PARAS_PER_PAGE = 4
_TS = re.compile(r"^\[(\d{1,2}):(\d{2})\]\s*(.*)$")
_DEF = re.compile(r"^\s*(?:def|function|func|class)\s+([A-Za-z_][\w]*)")


class DoclingLite:
    def supports(self, mime: str) -> bool:
        return True  # reference converter handles everything in the demo corpus

    def convert(self, raw: RawItem) -> ConvertedDocument:
        text = raw.bytes_.decode("utf-8", errors="replace")
        mime = raw.mime.lower()
        uri = raw.uri.lower()

        if mime in ("text/csv", "text/tab-separated-values") or uri.endswith((".csv", ".tsv")):
            return self._table(text, "\t" if uri.endswith(".tsv") else ",", raw.language)
        if (
            "transcript" in mime
            or "audio" in mime
            or "video" in mime
            or uri.endswith(".transcript")
        ):
            return self._transcript(text, raw.language)
        if uri.endswith((".py", ".js", ".ts", ".go", ".java", ".rb")) or "code" in mime:
            return self._code(text, raw.language)
        if uri.endswith(".ocr.txt") or "scan" in mime or "image" in mime:
            return self._scan(text, raw.language)
        if uri.endswith(".docx") or "wordprocessingml" in mime:
            return self._docx(raw.bytes_, raw.language)
        return self._text(text, raw.language)

    def _docx(self, data: bytes, lang: str) -> ConvertedDocument:
        """Extract paragraphs from a .docx (Office Open XML) with stdlib only."""
        import html as _html
        import io as _io
        import zipfile

        paras: list[str] = []
        try:
            z = zipfile.ZipFile(_io.BytesIO(data))
            xml = z.read("word/document.xml").decode("utf-8", "replace")
        except Exception:
            return self._text(data.decode("utf-8", "replace"), lang)
        for pm in re.findall(r"<w:p[ >].*?</w:p>", xml, re.S):
            runs = re.findall(r"<w:t[^>]*>(.*?)</w:t>", pm, re.S)
            # XML entities (&amp; &apos; &quot;) become their characters, so the
            # passage text is clean prose, not markup.
            para = _html.unescape(re.sub(r"<[^>]+>", "", "".join(runs))).strip()
            if para:
                paras.append(para)
        # Drop label/heading noise (a brief's "Category", "Official URL",
        # TOC entries…): they are short high-density fragments that outscore the
        # real body on a keyword match and make an extractive answer read as a
        # list of headings. Keep substantive paragraphs; fall back to all if that
        # would leave too little.
        body = [p for p in paras if len(p) >= 25]
        if len(body) >= 2:
            paras = body
        regions = []
        for i, p in enumerate(paras):
            regions.append(
                _region(
                    p,
                    CoordinateKind.PAGE_PARAGRAPH,
                    {"page": i // _PARAS_PER_PAGE + 1, "paragraph": i % _PARAS_PER_PAGE + 1},
                )
            )
        return ConvertedDocument(language=lang, regions=regions)

    def _text(self, text: str, lang: str) -> ConvertedDocument:
        paras = [p.strip() for p in re.split(r"\n\s*\n", text) if p.strip()]
        regions = []
        for i, p in enumerate(paras):
            page = i // _PARAS_PER_PAGE + 1
            para = i % _PARAS_PER_PAGE + 1
            regions.append(
                _region(p, CoordinateKind.PAGE_PARAGRAPH, {"page": page, "paragraph": para})
            )
        return ConvertedDocument(language=lang, regions=regions)

    def _table(self, text: str, delim: str, lang: str) -> ConvertedDocument:
        rows = list(csv.reader(io.StringIO(text), delimiter=delim))
        regions = []
        if not rows:
            return ConvertedDocument(language=lang, regions=regions)
        header = rows[0]
        for r, row in enumerate(rows[1:], start=1):
            for c, val in enumerate(row):
                col = header[c] if c < len(header) else f"col{c}"
                cell_text = f"{col}: {val} (row {r})"
                regions.append(
                    _region(cell_text, CoordinateKind.CELL, {"row": r, "col": c, "col_name": col})
                )
        return ConvertedDocument(language=lang, regions=regions)

    def _transcript(self, text: str, lang: str) -> ConvertedDocument:
        regions = []
        for line in text.splitlines():
            m = _TS.match(line.strip())
            if not m:
                continue
            start = int(m.group(1)) * 60 + int(m.group(2))
            regions.append(
                _region(
                    m.group(3), CoordinateKind.TIMESTAMP, {"start_s": start, "end_s": start + 10}
                )
            )
        return ConvertedDocument(language=lang, regions=regions)

    def _code(self, text: str, lang: str) -> ConvertedDocument:
        lines = text.splitlines()
        regions = []
        current_symbol, buf, start_line = "module", [], 1
        for i, line in enumerate(lines, start=1):
            m = _DEF.match(line)
            if m and buf:
                regions.append(
                    _region(
                        "\n".join(buf),
                        CoordinateKind.SYMBOL_LINE,
                        {"symbol": current_symbol, "line": start_line},
                    )
                )
                buf, start_line = [], i
                current_symbol = m.group(1)
            elif m:
                current_symbol, start_line = m.group(1), i
            buf.append(line)
        if buf:
            regions.append(
                _region(
                    "\n".join(buf),
                    CoordinateKind.SYMBOL_LINE,
                    {"symbol": current_symbol, "line": start_line},
                )
            )
        return ConvertedDocument(language=lang, regions=regions)

    def _scan(self, text: str, lang: str) -> ConvertedDocument:
        paras = [p.strip() for p in re.split(r"\n\s*\n", text) if p.strip()]
        regions = []
        for i, p in enumerate(paras):
            regions.append(
                _region(
                    p,
                    CoordinateKind.BBOX,
                    {
                        "page": i // _PARAS_PER_PAGE + 1,
                        "bbox": [72, 100 + (i % _PARAS_PER_PAGE) * 120, 520, 90],
                    },
                )
            )
        return ConvertedDocument(language=lang, regions=regions)


def _region(text: str, kind: CoordinateKind, locator: dict):
    from ..contracts.types import Region

    return Region(text=text, coordinate=Coordinate(kind, locator))
