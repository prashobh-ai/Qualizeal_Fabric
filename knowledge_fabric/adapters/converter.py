"""DocumentConverter: bytes -> regions with precise coordinates (I2), for every
document kind (T41). ``convert()`` routes by mime / extension:

  Word, PDF, PowerPoint      Docling (``engines`` extra) → page + bbox passages,
                             tables kept whole. Without Docling: .docx keeps the
                             stdlib path; PDF/PPTX raise ConverterUnavailableError.
  Excel, CSV/TSV             ingestion.tables → typed SQLite per sheet + row and
                             summary passages (CELL), facts.json["tables"].
  Images                     ingestion.images → OCR + one model description
                             (BBOX), images/<doc>/<name>.json.
  Markdown, HTML, text       page + paragraph.
  Audio/video transcripts    [mm:ss] lines → timestamps.
  Code, notebooks            symbol + line.
  Jira issues                application/x-kf-jira+json → field + comment passages.
  Scans (.ocr.txt)           page + bbox (OCR stand-in for synthetic corpora).

Every region carries a resolvable coordinate; the pipeline adds the
``citation_url`` and ``source_kind`` from the record's meta.
"""

from __future__ import annotations

import csv
import io
import json
import re
from dataclasses import replace
from html.parser import HTMLParser

from ..contracts.types import ConvertedDocument, Coordinate, CoordinateKind, RawItem, Region

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
    "jsx": "javascript",
    "mjs": "javascript",
    "cjs": "javascript",
    "ipynb": "notebook",
}
_CODE_EXT = (".py", ".js", ".ts", ".tsx", ".go", ".java", ".rb", ".cs", ".sh")
_IMAGE_EXT = (".png", ".jpg", ".jpeg", ".gif", ".webp", ".svg", ".bmp", ".tif", ".tiff")
JIRA_MIME = "application/x-kf-jira+json"


class ConverterUnavailableError(RuntimeError):
    """A document type needs an engine that is not installed. The message names
    the extra to install; nothing is silently converted to an empty document."""


# ---------------------------------------------------------------------------
# routing
# ---------------------------------------------------------------------------
def classify(mime: str, uri: str) -> str:
    """The route ``convert()`` takes for a record:
    ``jira | passages | table | spreadsheet | transcript | code | notebook | scan |
    image | docx | office | html | text``."""
    mime = (mime or "").lower().split(";")[0].strip()
    path = (uri or "").lower().split("?", 1)[0]
    if mime == JIRA_MIME or path.endswith(".jira.json"):
        return "jira"
    if mime.startswith(PASSAGES_MIME):
        return "passages"
    if mime in ("text/csv", "text/tab-separated-values") or path.endswith((".csv", ".tsv")):
        return "table"
    if (
        "spreadsheetml" in mime
        or mime == "application/vnd.ms-excel"
        or path.endswith((".xlsx", ".xlsm"))
    ):
        return "spreadsheet"
    if "transcript" in mime or "audio" in mime or "video" in mime or path.endswith(".transcript"):
        return "transcript"
    if path.endswith(".ipynb") or mime == "application/x-ipynb+json":
        return "notebook"
    if path.endswith(_CODE_EXT) or "code" in mime:
        return "code"
    if path.endswith(".ocr.txt") or "scan" in mime:
        return "scan"
    if mime.startswith("image/") or path.endswith(_IMAGE_EXT):
        return "image"
    if path.endswith(".docx") or "wordprocessingml" in mime:
        return "docx"
    if (
        mime == "application/pdf"
        or path.endswith(".pdf")
        or "presentationml" in mime
        or path.endswith((".pptx", ".ppt"))
        or mime == "application/msword"
        or path.endswith(".doc")
    ):
        return "office"
    if mime in ("text/html", "application/xhtml+xml") or path.endswith((".html", ".htm")):
        return "html"
    return "text"


_KIND_OF_ROUTE = {
    "jira": "jira",
    "table": "table",
    "spreadsheet": "table",
    "image": "image",
    "code": "code",
    "notebook": "code",
}


def source_kind_for(mime: str, uri: str) -> str:
    """``document|table|image|jira|confluence|code|analysis`` for a record whose
    connector did not set ``source_kind`` explicitly."""
    u = (uri or "").lower()
    if u.startswith("confluence://"):
        return "confluence"
    if u.startswith("analysis://"):
        return "analysis"
    return _KIND_OF_ROUTE.get(classify(mime, uri), "document")


# T37/T38: a connector may hand the converter PRE-CHUNKED passages (commits,
# pull-request bodies, reviews, issue comments, release notes) as a JSON list of
# ``{"text", "kind"?, "locator"}`` so every passage keeps its own citation URL.
PASSAGES_MIME = "application/x-kf-passages+json"


def docling_available() -> bool:
    try:
        import docling  # noqa: F401
    except ImportError:
        return False
    return True


# ---------------------------------------------------------------------------
# small helpers
# ---------------------------------------------------------------------------
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


def _doc_key(raw: RawItem) -> str:
    """The id side artefacts are filed under: the pipeline's ``doc_id`` when the
    converter runs inside it, else a slug of the uri (direct use)."""
    key = raw.meta.get("doc_id") if isinstance(raw.meta, dict) else None
    if key:
        return str(key)
    base = re.sub(r"^[a-z]+://", "", raw.uri or "").strip("/")
    return re.sub(r"[^A-Za-z0-9_.-]+", "__", base) or "doc"


class _HTMLText(HTMLParser):
    """HTML → paragraphs: block elements break paragraphs, table cells are
    joined with ``|``, script/style/Confluence macro parameters are dropped."""

    _BLOCK = {
        "p", "div", "br", "li", "h1", "h2", "h3", "h4", "h5", "h6", "tr", "table",
        "section", "article", "header", "footer", "pre", "blockquote", "ul", "ol",
        "ac:structured-macro", "ac:rich-text-body", "ac:plain-text-body",
    }  # fmt: skip
    _SKIP = {"script", "style", "ac:parameter", "head", "title"}

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self._buf: list[str] = []
        self._skip = 0

    def _flush(self):
        text = " ".join("".join(self._buf).split())
        if text:
            self.parts.append(text)
        self._buf = []

    def handle_starttag(self, tag, attrs):
        if tag in self._SKIP:
            self._skip += 1
        elif tag in self._BLOCK:
            self._flush()
        elif tag in ("td", "th"):
            self._buf.append(" | ")

    def handle_endtag(self, tag):
        if tag in self._SKIP:
            self._skip = max(0, self._skip - 1)
        elif tag in self._BLOCK:
            self._flush()

    def handle_data(self, data):
        if not self._skip:
            self._buf.append(data)

    def close(self):
        super().close()
        self._flush()


def html_paragraphs(markup: str) -> list[str]:
    p = _HTMLText()
    p.feed(markup)
    p.close()
    return [x.strip(" |") for x in p.parts if x.strip(" |")]


# ---------------------------------------------------------------------------
# the converter
# ---------------------------------------------------------------------------
class DoclingLite:
    def __init__(self, platform=None, ocr=None):
        # ``platform`` (optional) supplies ``.model`` for the image describe step;
        # ``ocr(data, media_type) -> str`` overrides Tesseract (tests / engines).
        self.platform = platform
        self.ocr = ocr

    def supports(self, mime: str) -> bool:
        return True  # every route either converts or raises a clear error

    def convert(self, raw: RawItem) -> ConvertedDocument:
        route = classify(raw.mime, raw.uri)
        if route == "table":
            return self._table(raw)
        if route == "spreadsheet":
            return self._spreadsheet(raw)
        if route == "image":
            return self._image(raw)
        if route == "jira":
            return self._jira(raw)
        if route == "office":
            return self._office(raw)
        if route == "docx":
            return (
                self._office(raw) if docling_available() else self._docx(raw.bytes_, raw.language)
            )
        if route == "notebook":
            return self._notebook(raw)
        text = raw.bytes_.decode("utf-8", errors="replace")
        if route == "passages":
            return self._passages(text, raw.language)
        if route == "transcript":
            return self._transcript(text, raw.language)
        if route == "code":
            return self._code(text, raw)
        if route == "scan":
            return self._scan(text, raw.language)
        if route == "html":
            return self._html(text, raw.language)
        return self._text(text, raw.language)

    def _passages(self, text: str, lang: str) -> ConvertedDocument:
        """Pre-chunked passages from a connector: one region per item with the
        locator it carries (its citation URL included). Malformed input falls
        back to plain paragraphs."""
        import json as _json

        try:
            items = _json.loads(text)
        except ValueError:
            return self._text(text, lang)
        if not isinstance(items, list):
            return self._text(text, lang)
        regions = []
        for i, it in enumerate(items):
            if not isinstance(it, dict) or not str(it.get("text", "")).strip():
                continue
            try:
                kind = CoordinateKind(it.get("kind") or "page_paragraph")
            except ValueError:
                kind = CoordinateKind.PAGE_PARAGRAPH
            loc = dict(it.get("locator") or {})
            loc.setdefault("page", 1)
            loc.setdefault("paragraph", i + 1)
            regions.append(_region(str(it["text"]).strip(), kind, loc))
        return ConvertedDocument(language=lang, regions=regions)

    # -- Word / PDF / PowerPoint --------------------------------------------
    def _office(self, raw: RawItem) -> ConvertedDocument:
        """Docling when installed (page + bbox passages, tables whole). Without
        it: .docx → the stdlib path; anything else raises loudly."""
        path = raw.uri.lower().split("?", 1)[0]
        ext = "." + path.rsplit(".", 1)[-1] if "." in path.rsplit("/", 1)[-1] else ""
        if "wordprocessingml" in raw.mime.lower() and not ext:
            ext = ".docx"
        elif "presentationml" in raw.mime.lower() and not ext:
            ext = ".pptx"
        elif raw.mime.lower() == "application/pdf" and not ext:
            ext = ".pdf"
        try:
            from docling.document_converter import DocumentConverter
        except ImportError as e:
            if ext == ".docx":
                return self._docx(raw.bytes_, raw.language)
            raise ConverterUnavailableError(
                f"{ext or raw.mime} conversion needs Docling: install the engines extra "
                "(pip install 'qualizeal-knowledge-fabric[engines]')"
            ) from e
        import os
        import tempfile

        with tempfile.NamedTemporaryFile(suffix=ext or ".bin", delete=False) as f:
            f.write(raw.bytes_)
            tmp = f.name
        try:
            result = DocumentConverter().convert(tmp)
        finally:
            os.unlink(tmp)
        return ConvertedDocument(language=raw.language, regions=docling_regions(result.document))

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

    # -- text-ish -----------------------------------------------------------
    def _text(self, text: str, lang: str) -> ConvertedDocument:
        paras = [p.strip() for p in re.split(r"\n\s*\n", text) if p.strip()]
        return ConvertedDocument(language=lang, regions=_paragraph_regions(paras))

    def _html(self, markup: str, lang: str) -> ConvertedDocument:
        return ConvertedDocument(language=lang, regions=_paragraph_regions(html_paragraphs(markup)))

    # -- tables -------------------------------------------------------------
    def _table(self, raw: RawItem) -> ConvertedDocument:
        from ..ingestion import tables

        text = raw.bytes_.decode("utf-8", errors="replace")
        path = raw.uri.lower().split("?", 1)[0]
        delim = "\t" if (path.endswith(".tsv") or "tab-separated" in raw.mime.lower()) else ","
        sheet = tables.safe_name(_stem(raw.uri)) or "sheet1"
        return tables.convert_sheets(
            tables.sheets_from_csv(text, delim, sheet),
            doc_id=_doc_key(raw),
            doc_title=raw.title or _stem(raw.uri),
            language=raw.language,
            citation_url=raw.meta.get("citation_url") or raw.uri,
        )

    def _spreadsheet(self, raw: RawItem) -> ConvertedDocument:
        from ..ingestion import tables

        return tables.convert_sheets(
            tables.sheets_from_xlsx(raw.bytes_),
            doc_id=_doc_key(raw),
            doc_title=raw.title or _stem(raw.uri),
            language=raw.language,
            citation_url=raw.meta.get("citation_url") or raw.uri,
        )

    # -- images -------------------------------------------------------------
    def _image(self, raw: RawItem) -> ConvertedDocument:
        from ..ingestion import images

        model = getattr(self.platform, "model", None) if self.platform is not None else None
        return images.convert_image(raw, doc_id=_doc_key(raw), model=model, ocr=self.ocr)

    # -- Jira issues (from connectors/jira_live) ----------------------------
    def _jira(self, raw: RawItem) -> ConvertedDocument:
        try:
            issue = json.loads(raw.bytes_.decode("utf-8", "replace"))
        except ValueError:
            return self._text(raw.bytes_.decode("utf-8", "replace"), raw.language)
        key = issue.get("key", "")
        head = f"[{key}] {issue.get('summary', '')}".strip()
        facts = [
            f"{label}: {issue.get(field)}"
            for label, field in (
                ("Status", "status"),
                ("Priority", "priority"),
                ("Type", "type"),
                ("Assignee", "assignee"),
            )
            if issue.get(field)
        ]
        if issue.get("labels"):
            facts.append("Labels: " + ", ".join(issue["labels"]))
        if issue.get("sprint"):
            sp = issue["sprint"]
            facts.append(f"Sprint: {sp.get('name', '')} ({sp.get('state', '')})".replace(" ()", ""))
        if issue.get("parent"):
            facts.append(f"Parent: {issue['parent']}")
        regions = [
            _region(
                head + ("\n" + "; ".join(facts) if facts else ""),
                CoordinateKind.PAGE_PARAGRAPH,
                {"page": 1, "paragraph": 1, "field": "summary", "issue": key},
            )
        ]
        n = 1
        for para in re.split(r"\n\s*\n", issue.get("description") or ""):
            para = para.strip()
            if para:
                n += 1
                regions.append(
                    _region(
                        para,
                        CoordinateKind.PAGE_PARAGRAPH,
                        {"page": 1, "paragraph": n, "field": "description", "issue": key},
                    )
                )
        if issue.get("links"):
            n += 1
            links = "; ".join(
                f"{ln.get('direction') or ln.get('type')} {ln.get('key')}"
                + (f" ({ln['summary']})" if ln.get("summary") else "")
                for ln in issue["links"]
            )
            regions.append(
                _region(
                    f"{key} links: {links}",
                    CoordinateKind.PAGE_PARAGRAPH,
                    {"page": 1, "paragraph": n, "field": "links", "issue": key},
                )
            )
        for i, c in enumerate(issue.get("comments") or [], start=1):
            body = (c.get("body") or "").strip()
            if not body:
                continue
            regions.append(
                _region(
                    f"Comment by {c.get('author', '')} on {c.get('created', '')}: {body}",
                    CoordinateKind.PAGE_PARAGRAPH,
                    {
                        "page": 2,
                        "paragraph": i,
                        "field": "comment",
                        "issue": key,
                        "comment_id": c.get("id", ""),
                        "author": c.get("author", ""),
                        "created": c.get("created", ""),
                    },
                )
            )
        return ConvertedDocument(language=raw.language, regions=regions)

    # -- transcripts --------------------------------------------------------
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

    # -- code ---------------------------------------------------------------
    def _notebook(self, raw: RawItem) -> ConvertedDocument:
        """A Jupyter notebook: code cells joined with cell markers, then the
        symbol chunker in the kernel's language (markdown cells stay as prose)."""
        try:
            nb = json.loads(raw.bytes_.decode("utf-8", "replace"))
        except ValueError:
            return self._text(raw.bytes_.decode("utf-8", "replace"), raw.language)
        lang = str(((nb.get("metadata") or {}).get("kernelspec") or {}).get("language", "python"))
        ext = {"python": ".py", "javascript": ".js", "typescript": ".ts"}.get(lang.lower(), ".txt")
        code, prose = [], []
        for i, cell in enumerate(nb.get("cells") or [], start=1):
            src = cell.get("source", "")
            src = "".join(src) if isinstance(src, list) else str(src)
            if cell.get("cell_type") == "code":
                code.append(f"# %% [cell {i}]\n{src.rstrip()}")
            elif src.strip():
                prose.append(src.strip())
        regions = list(_paragraph_regions(prose))
        if code:
            regions.extend(self._code("\n\n".join(code), replace(raw, uri=raw.uri + ext)).regions)
        return ConvertedDocument(language=raw.language, regions=regions)

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
        elif language != "text":
            regions = self._analysis_symbols(text, path, language, loc)
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

    def _analysis_symbols(self, text, path, language, loc):
        """Non-Python languages (T38): one passage per symbol from
        ``analysis.symbols`` — JS/TS, Java, Go, C#, SQL statements, YAML keys,
        notebook cells — with the same locator shape as the Python path.
        Returns [] when the language is not covered, so windows apply."""
        from ..analysis import symbols as _symbols

        lines = text.splitlines()
        regions = []
        for rec in _symbols.extract(path, text, language):
            if rec["kind"] == "module":
                continue
            body = "\n".join(lines[rec["start_line"] - 1 : rec["end_line"]]).rstrip()
            if not body.strip():
                continue
            sig = rec.get("signature") or ""
            args = re.search(r"\(.*", sig)
            base = rec["qualified"] + (args.group(0) if args else "")
            first = _sentences_head(rec.get("docstring") or "")
            summary = f"{base} — {first}" if first else base
            regions.append(
                _region(
                    body,
                    CoordinateKind.SYMBOL_LINE,
                    loc(
                        rec["symbol"], rec["qualified"], rec["start_line"], rec["end_line"], summary
                    ),
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

    # -- scans (OCR stand-in text) ------------------------------------------
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


# ---------------------------------------------------------------------------
# Docling document → regions (duck-typed so it is testable without Docling)
# ---------------------------------------------------------------------------
def docling_regions(document) -> list[Region]:
    """``DoclingDocument`` items → BBOX regions ``{page, bbox, paragraph, label}``.
    Text items become one region each; a table is kept WHOLE as one region
    (its markdown export, else its cells joined row by row)."""
    regions: list[Region] = []
    per_page: dict[int, int] = {}
    for item, _level in document.iterate_items():
        prov = getattr(item, "prov", None) or []
        p0 = prov[0] if prov else None
        page = int(getattr(p0, "page_no", 1) or 1) if p0 is not None else 1
        bb = getattr(p0, "bbox", None) if p0 is not None else None
        bbox = None
        if bb is not None:
            try:
                bbox = [
                    round(float(bb.l), 1),
                    round(float(bb.t), 1),
                    round(float(bb.r), 1),
                    round(float(bb.b), 1),
                ]
            except (AttributeError, TypeError, ValueError):
                bbox = None
        label = str(getattr(item, "label", "") or type(item).__name__).lower()
        if type(item).__name__ == "TableItem" or label == "table":
            text = _docling_table_text(item, document)
            label = "table"
        else:
            text = str(getattr(item, "text", "") or "").strip()
        if len(text) < 2:
            continue
        per_page[page] = per_page.get(page, 0) + 1
        regions.append(
            _region(
                text,
                CoordinateKind.BBOX,
                {
                    "page": page,
                    "bbox": bbox or [0, 0, 0, 0],
                    "paragraph": per_page[page],
                    "label": label,
                },
            )
        )
    return regions


def _docling_table_text(item, document) -> str:
    for call in (lambda: item.export_to_markdown(document), lambda: item.export_to_markdown()):
        try:
            md = call()
        except Exception:
            continue
        if isinstance(md, str) and md.strip():
            return md.strip()
    data = getattr(item, "data", None)
    cells = getattr(data, "table_cells", None) or []
    rows: dict[int, list[str]] = {}
    for c in cells:
        r = int(getattr(c, "start_row_offset_idx", 0) or 0)
        rows.setdefault(r, []).append(str(getattr(c, "text", "") or ""))
    return "\n".join(" | ".join(rows[r]) for r in sorted(rows))


# ---------------------------------------------------------------------------
def _stem(uri: str) -> str:
    base = re.sub(r"^[a-z]+://", "", uri or "").rstrip("/").rsplit("/", 1)[-1]
    return base.rsplit(".", 1)[0] if "." in base else base


def _paragraph_regions(paras: list[str]) -> list[Region]:
    regions = []
    for i, p in enumerate(paras):
        regions.append(
            _region(
                p,
                CoordinateKind.PAGE_PARAGRAPH,
                {"page": i // _PARAS_PER_PAGE + 1, "paragraph": i % _PARAS_PER_PAGE + 1},
            )
        )
    return regions


def _region(text: str, kind: CoordinateKind, locator: dict):
    return Region(text=text, coordinate=Coordinate(kind, locator))


__all__ = [
    "ConverterUnavailableError",
    "DoclingLite",
    "classify",
    "docling_available",
    "docling_regions",
    "html_paragraphs",
    "source_kind_for",
]

# keep the csv/io names importable for older callers of this module
_ = (csv, io)
