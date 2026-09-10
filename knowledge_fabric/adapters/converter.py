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
_DEF = re.compile(
    r"^\s*(?:export\s+)?(?:public\s+|private\s+|static\s+|async\s+|func\s+|"
    r"def\s+|function\s+|class\s+)+([A-Za-z_][\w]*)"
)
_CODE_LANG = {
    "py": "python",
    "js": "javascript",
    "ts": "typescript",
    "tsx": "typescript",
    "go": "go",
    "java": "java",
    "rb": "ruby",
    "cs": "csharp",
    "sh": "bash",
    "sql": "sql",
    "yaml": "yaml",
    "yml": "yaml",
    "tf": "hcl",
}


def _parse_code_uri(uri: str):
    """``github://owner/repo/a/b/c.py`` -> (owner, repo, 'a/b/c.py'). Any other
    scheme yields ('', '', basename) so a code answer still cites a path."""
    body = re.sub(r"^[a-z]+://", "", uri or "")
    parts = [p for p in body.split("/") if p]
    if len(parts) >= 3 and "://" in (uri or "github://"):
        return parts[0], parts[1], "/".join(parts[2:])
    return "", "", (parts[-1] if parts else "")


def _code_language(path: str) -> str:
    return _CODE_LANG.get(path.rsplit(".", 1)[-1].lower() if "." in path else "", "text")


def _module_name(path: str) -> str:
    """'knowledge_fabric/answer/service.py' -> 'knowledge_fabric.answer.service'."""
    stem = re.sub(r"\.[A-Za-z0-9]+$", "", path or "")
    return stem.strip("/").replace("/", ".")


def _sentences_head(text: str) -> str:
    """First sentence of a docstring, collapsed to one line (deterministic
    summary input — no model)."""
    one = re.sub(r"\s+", " ", (text or "").strip())
    if not one:
        return ""
    m = re.match(r"(.+?[.!?])(\s|$)", one)
    return (m.group(1) if m else one)[:160]


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
        if (
            uri.endswith((".py", ".js", ".ts", ".tsx", ".go", ".java", ".rb", ".cs", ".sh"))
            or "code" in mime
        ):
            return self._code(text, raw)
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

    def _code(self, text: str, raw: RawItem) -> ConvertedDocument:
        """Chunk source by SYMBOL (function / method / class), not by line
        window, so a code passage is a whole, citeable unit. Python is parsed
        with ``ast`` for exact symbol boundaries; other languages use a
        brace/indent scanner; both fall back to fixed windows when parsing
        fails. Every passage carries a rich SYMBOL_LINE locator — ``symbol`` and
        ``line`` (kept for back-compat) plus ``qualified``, ``start_line``,
        ``end_line``, ``path``, ``repo``, ``language``, a GitHub line-anchored
        ``url`` and a deterministic ``summary_line`` — so a code answer can cite
        ``path#L<start>-L<end>`` and open the exact function on GitHub."""
        owner, repo, path = _parse_code_uri(raw.uri)
        language = _code_language(path)
        module = _module_name(path)
        blob = f"https://github.com/{owner}/{repo}/blob/main/{path}" if owner and repo else ""

        def loc(symbol, qualified, start, end, summary):
            d = {
                "symbol": symbol,
                "line": start,  # back-compat: existing SYMBOL_LINE render shows symbol:line
                "qualified": qualified,
                "start_line": start,
                "end_line": end,
                "path": path,
                "repo": f"{owner}/{repo}" if owner and repo else repo,
                "language": language,
                "summary_line": summary,
            }
            if blob:
                d["url"] = f"{blob}#L{start}-L{end}"
            return d

        regions: list = []
        if language == "python":
            regions = self._python_symbols(text, module, loc)
        if not regions:
            regions = self._code_windows(text, module, language, loc)
        return ConvertedDocument(language=raw.language, regions=regions)

    def _python_symbols(self, text, module, loc):
        """One passage per top-level function and per class (its docstring +
        each method), via ``ast``. Returns [] on a syntax error so the caller
        falls back to windows."""
        import ast

        try:
            tree = ast.parse(text)
        except SyntaxError:
            return []
        lines = text.splitlines()

        def body(start, end):
            return "\n".join(lines[start - 1 : end]).rstrip()

        def signature(node):
            first = lines[node.lineno - 1].strip()
            return re.sub(r"^\s*(?:async\s+)?(?:def|class)\s+", "", first).rstrip(":")

        def summary(qualified, node):
            args = re.search(r"\(.*", signature(node))
            base = qualified + (args.group(0) if args else "")
            first = _sentences_head(ast.get_docstring(node) or "")
            return f"{base} — {first}" if first else base

        regions = []
        for node in tree.body:
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                q = f"{module}.{node.name}" if module else node.name
                regions.append(
                    _region(
                        body(node.lineno, node.end_lineno),
                        CoordinateKind.SYMBOL_LINE,
                        loc(node.name, q, node.lineno, node.end_lineno, summary(q, node)),
                    )
                )
            elif isinstance(node, ast.ClassDef):
                methods = [
                    m for m in node.body if isinstance(m, (ast.FunctionDef, ast.AsyncFunctionDef))
                ]
                head_end = methods[0].lineno - 1 if methods else node.end_lineno
                cq = f"{module}.{node.name}" if module else node.name
                regions.append(
                    _region(
                        body(node.lineno, head_end),
                        CoordinateKind.SYMBOL_LINE,
                        loc(node.name, cq, node.lineno, head_end, summary(cq, node)),
                    )
                )
                for m in methods:
                    mq = f"{cq}.{m.name}"
                    regions.append(
                        _region(
                            body(m.lineno, m.end_lineno),
                            CoordinateKind.SYMBOL_LINE,
                            loc(m.name, mq, m.lineno, m.end_lineno, summary(mq, m)),
                        )
                    )
        return regions

    def _code_windows(self, text, module, language, loc):
        """Non-Python / unparseable source: split on brace-or-keyword symbol
        starts, else fixed 40-line windows. Keeps SYMBOL_LINE coordinates."""
        lines = text.splitlines()
        regions, buf, symbol, start = [], [], "module", 1
        for i, line in enumerate(lines, start=1):
            m = _DEF.match(line)
            if m and buf:
                q = f"{module}.{symbol}" if module else symbol
                regions.append(
                    _region(
                        "\n".join(buf).rstrip(),
                        CoordinateKind.SYMBOL_LINE,
                        loc(symbol, q, start, i - 1, q),
                    )
                )
                buf, start, symbol = [], i, m.group(1)
            elif m:
                symbol, start = m.group(1), i
            buf.append(line)
            if len(buf) >= 40 and not _DEF.match(line):  # bound very long spans
                q = f"{module}.{symbol}" if module else symbol
                regions.append(
                    _region(
                        "\n".join(buf).rstrip(),
                        CoordinateKind.SYMBOL_LINE,
                        loc(symbol, q, start, i, q),
                    )
                )
                buf, start, symbol = [], i + 1, "block"
        if buf:
            q = f"{module}.{symbol}" if module else symbol
            regions.append(
                _region(
                    "\n".join(buf).rstrip(),
                    CoordinateKind.SYMBOL_LINE,
                    loc(symbol, q, start, len(lines), q),
                )
            )
        return regions

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
