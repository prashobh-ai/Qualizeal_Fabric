"""Comment extraction (T38) — every docstring, block comment (≥ 2 lines), line
comment (≥ 8 words) and every ``TODO | FIXME | NOTE | HACK`` line.

Record (``analysis/<repo>/comments.jsonl``)::

    {repo, path, line, kind: "comment",
     tag ∈ docstring|block|line|TODO|FIXME|NOTE|HACK, text, symbol, url}

``symbol`` is the innermost symbol whose line range holds the comment (from
``symbols.py``); ``url`` is ``https://github.com/{o}/{r}/blob/<branch>/<path>#L<n>``.
Python comments come from ``tokenize`` (exact); the brace languages, SQL and
YAML use the same string/comment-aware scanner as the symbol extractor.
"""

from __future__ import annotations

import ast
import io
import os
import re
import tokenize

from .. import fabric_data as fd
from . import symbols as _symbols

TAG_RE = re.compile(r"\b(TODO|FIXME|NOTE|HACK)\b")
MIN_LINE_WORDS = 8

_STYLE = {
    "python": {"line": ("#",), "block": None},
    "yaml": {"line": ("#",), "block": None},
    "javascript": {"line": ("//",), "block": ("/*", "*/")},
    "typescript": {"line": ("//",), "block": ("/*", "*/")},
    "java": {"line": ("//",), "block": ("/*", "*/")},
    "go": {"line": ("//",), "block": ("/*", "*/")},
    "csharp": {"line": ("//",), "block": ("/*", "*/")},
    "sql": {"line": ("--",), "block": ("/*", "*/")},
}


def _url(repo: str, branch: str, path: str, line: int) -> str:
    return f"https://github.com/{repo}/blob/{branch}/{path}#L{line}" if repo else ""


def _python_comments(text: str) -> list[tuple[int, str, str]]:
    """``(line, text, group)`` for every ``#`` comment via ``tokenize``; the
    group id joins consecutive full-line comments into one block."""
    out, group, last = [], 0, -2
    try:
        toks = list(tokenize.generate_tokens(io.StringIO(text).readline))
    except (tokenize.TokenError, SyntaxError, IndentationError):
        toks = []
    if not toks:  # unparseable: a plain scan is still better than nothing
        for i, ln in enumerate(text.splitlines(), start=1):
            s = ln.strip()
            if s.startswith("#"):
                if i != last + 1:
                    group += 1
                out.append((i, s.lstrip("#").strip(), f"g{group}"))
                last = i
        return out
    for tok in toks:
        if tok.type != tokenize.COMMENT:
            continue
        line = tok.start[0]
        full_line = tok.line.strip().startswith("#")
        if not full_line or line != last + 1:
            group += 1
        out.append((line, tok.string.lstrip("#").strip(), f"g{group}"))
        last = line if full_line else -2
    return out


def _scan_comments(text: str, style: dict) -> list[tuple[int, str, str]]:
    """Line/block comments for brace languages, SQL and YAML (string-aware)."""
    lines = text.splitlines()
    line_markers = style["line"]
    block = style["block"]
    out, group, last, in_block, s = [], 0, -2, False, None
    for i, line in enumerate(lines, start=1):
        j, started_block = 0, False
        if in_block:
            started_block = True
        while j < len(line):
            ch = line[j]
            if in_block:
                k = line.find(block[1], j)
                seg = line[j:] if k < 0 else line[j:k]
                out.append((i, _symbols.clean_comment(seg), f"b{group}"))
                if k < 0:
                    j = len(line)
                    break
                in_block = False
                j = k + 2
                continue
            if s:
                if ch == "\\":
                    j += 2
                    continue
                if ch == s:
                    s = None
                j += 1
                continue
            marker = next((m for m in line_markers if line.startswith(m, j)), None)
            if marker:
                full = line[:j].strip() == ""
                if not full or i != last + 1:
                    group += 1
                out.append((i, _symbols.clean_comment(line[j:]), f"g{group}"))
                last = i if full else -2
                break
            if block and line.startswith(block[0], j):
                group += 1
                in_block = True
                k = line.find(block[1], j + 2)
                seg = line[j + 2 :] if k < 0 else line[j + 2 : k]
                out.append((i, _symbols.clean_comment(seg), f"b{group}"))
                if k < 0:
                    break
                in_block = False
                j = k + 2
                continue
            if ch in "\"'`":
                s = ch
            j += 1
        if s in ("'", '"'):
            s = None
        if started_block:
            last = -2
    return [(ln, tx, g) for ln, tx, g in out]


def _py_docstrings(text: str) -> list[tuple[int, str]]:
    try:
        tree = ast.parse(text)
    except SyntaxError:
        return []
    out = []
    for node in [tree, *ast.walk(tree)]:
        if not isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        doc = ast.get_docstring(node)
        if doc and node.body and isinstance(node.body[0], ast.Expr):
            out.append((node.body[0].lineno, doc.strip()))
    return out


def _enclosing(symbols: list[dict], line: int) -> str:
    best, span = "", None
    for s in symbols:
        if s["kind"] == "module":
            continue
        if s["start_line"] <= line <= s["end_line"]:
            w = s["end_line"] - s["start_line"]
            if span is None or w < span:
                best, span = s["qualified"], w
    if not best:
        for s in symbols:
            if s["kind"] == "module":
                return s["qualified"]
    return best


def extract(
    path: str,
    text: str,
    language: str | None = None,
    symbols: list[dict] | None = None,
    repo: str = "",
    branch: str = "main",
) -> list[dict]:
    language = language or _symbols.language_of(path)
    if language is None:
        return []
    syms = symbols if symbols is not None else _symbols.extract(path, text, language)
    syms = [s for s in syms if s.get("path") == path] if symbols is not None else syms
    out: list[dict] = []

    def rec(line, tag, body):
        return {
            "repo": repo,
            "path": path,
            "line": int(line),
            "kind": "comment",
            "tag": tag,
            "text": body,
            "symbol": _enclosing(syms, int(line)),
            "url": _url(repo, branch, path, int(line)),
        }

    # docstrings
    doc_lines = set()
    if language == "python":
        for line, doc in _py_docstrings(text):
            out.append(rec(line, "docstring", doc))
            doc_lines.add(line)
    elif language == "notebook":
        for s in syms:
            if s.get("docstring") and s["signature"].startswith("markdown"):
                out.append(rec(s["start_line"], "docstring", s["docstring"]))
    # comment groups
    style = _STYLE.get(language)
    if language == "python":
        raw = _python_comments(text)
    elif style:
        raw = _scan_comments(text, style)
    else:
        raw = []
    groups: dict[str, list[tuple[int, str]]] = {}
    order: list[str] = []
    for line, body, g in raw:
        if g not in groups:
            groups[g] = []
            order.append(g)
        groups[g].append((line, body))
    starts = {s["start_line"]: s for s in syms if s["kind"] != "module"}
    for g in order:
        items = groups[g]
        first, last = items[0][0], items[-1][0]
        joined = "\n".join(b for _, b in items).strip()
        if not joined:
            continue
        tagged = [(ln, b) for ln, b in items if TAG_RE.search(b)]
        # a comment directly above a symbol (annotations allowed) is its docstring
        nxt = last + 1
        while nxt not in starts and nxt <= last + 4 and _is_annotation(text, nxt):
            nxt += 1
        if language != "python" and nxt in starts and starts[nxt].get("docstring") == joined:
            out.append(rec(first, "docstring", joined))
            for ln, b in tagged:
                out.append(rec(ln, TAG_RE.search(b).group(1), b.strip()))
            continue
        if len(items) >= 2:
            out.append(rec(first, "block", joined))
            for ln, b in tagged:
                out.append(rec(ln, TAG_RE.search(b).group(1), b.strip()))
        elif tagged:
            ln, b = tagged[0]
            out.append(rec(ln, TAG_RE.search(b).group(1), b.strip()))
        elif len(joined.split()) >= MIN_LINE_WORDS:
            out.append(rec(first, "line", joined))
    # TODO-style tags inside docstrings
    for line, doc in _py_docstrings(text) if language == "python" else []:
        for off, ln in enumerate(doc.splitlines()):
            m = TAG_RE.search(ln)
            if m:
                out.append(rec(line + off, m.group(1), ln.strip()))
    out.sort(key=lambda r: (r["line"], r["tag"]))
    return out


def _is_annotation(text: str, line_no: int) -> bool:
    lines = text.splitlines()
    if 1 <= line_no <= len(lines):
        s = lines[line_no - 1].strip()
        return s.startswith(("@", "[")) and not s.startswith("[[")
    return False


def extract_repo(
    repo: str,
    root: str,
    branch: str = "main",
    symbols: list[dict] | None = None,
    write: bool = True,
) -> list[dict]:
    """Every comment under a clone; writes ``analysis/<repo>/comments.jsonl``."""
    by_path: dict[str, list[dict]] = {}
    for s in symbols or []:
        by_path.setdefault(s["path"], []).append(s)
    out: list[dict] = []
    for rel in _symbols.iter_source_files(root):
        text = _symbols.read_text(os.path.join(root, rel))
        if text is None:
            continue
        syms = by_path.get(rel) if symbols is not None else None
        out.extend(extract(rel, text, symbols=syms, repo=repo, branch=branch))
    if write:
        p = fd.path("analysis", fd.repo_slug(repo), "comments.jsonl")
        os.makedirs(os.path.dirname(p), exist_ok=True)
        if os.path.exists(p):
            os.remove(p)
        fd.append_jsonl(p, out)
    return out
