"""Symbol extraction (T38) — one record per symbol, every language stdlib-only.

* Python — ``ast``: module docstring + imports, classes, functions, methods
  (qualified name, signature, decorators, docstring, lines, calls, imports).
* JavaScript / TypeScript — a brace scanner over ``function``, arrow
  assignments, ``class`` (with methods) and ``export`` modifiers.
* Java, Go, C# — declaration regexes + brace matching.
* SQL — one record per statement; YAML — one per top-level key;
  notebooks (``.ipynb``) — one per cell.

Record shape (``analysis/<repo>/symbols.jsonl``)::

    {repo, path, language, symbol, qualified,
     kind ∈ module|class|function|method|statement|key|cell,
     signature, decorators[], docstring, start_line, end_line, calls[], imports[], url}

The converter (T25 hook) chunks non-Python source by these records, so a
code passage is a whole, citeable symbol with a ``#L<start>-L<end>`` anchor.
"""

from __future__ import annotations

import ast
import json
import os
import re

from .. import fabric_data as fd

KINDS = ("module", "class", "function", "method", "statement", "key", "cell")

LANGUAGE_BY_EXT = {
    ".py": "python",
    ".js": "javascript",
    ".jsx": "javascript",
    ".mjs": "javascript",
    ".cjs": "javascript",
    ".ts": "typescript",
    ".tsx": "typescript",
    ".java": "java",
    ".go": "go",
    ".cs": "csharp",
    ".sql": "sql",
    ".yaml": "yaml",
    ".yml": "yaml",
    ".ipynb": "notebook",
}

SKIP_DIRS = {
    ".git",
    "node_modules",
    "vendor",
    "dist",
    "build",
    "__pycache__",
    ".venv",
    "venv",
    "site-packages",
    "fabric-data",
    ".mypy_cache",
    ".ruff_cache",
    ".pytest_cache",
}

MAX_FILE_BYTES = 1_000_000


def language_of(path: str) -> str | None:
    return LANGUAGE_BY_EXT.get(os.path.splitext(path)[1].lower())


def module_name(path: str) -> str:
    """``knowledge_fabric/answer/service.py`` → ``knowledge_fabric.answer.service``."""
    stem = re.sub(r"\.[A-Za-z0-9]+$", "", path or "")
    return stem.strip("/").replace("\\", "/").replace("/", ".")


def blob_url(repo: str, branch: str, path: str, start: int, end: int) -> str:
    if not repo:
        return ""
    anchor = f"#L{start}-L{end}" if end > start else f"#L{start}"
    return f"https://github.com/{repo}/blob/{branch}/{path}{anchor}"


def record(
    path: str,
    language: str,
    *,
    symbol: str,
    qualified: str,
    kind: str,
    signature: str = "",
    decorators: list[str] | None = None,
    docstring: str = "",
    start_line: int = 1,
    end_line: int = 1,
    calls: list[str] | None = None,
    imports: list[str] | None = None,
) -> dict:
    if kind not in KINDS:
        raise ValueError(f"unknown symbol kind {kind!r}")
    return {
        "repo": "",
        "path": path,
        "language": language,
        "symbol": symbol,
        "qualified": qualified,
        "kind": kind,
        "signature": signature,
        "decorators": list(decorators or []),
        "docstring": (docstring or "").strip(),
        "start_line": int(start_line),
        "end_line": int(max(end_line, start_line)),
        "calls": _dedupe(calls or []),
        "imports": _dedupe(imports or []),
        "url": "",
    }


def _dedupe(items) -> list[str]:
    seen, out = set(), []
    for it in items:
        if it and it not in seen:
            seen.add(it)
            out.append(it)
    return out


# ---------------------------------------------------------------------------
# public API
# ---------------------------------------------------------------------------
def extract(path: str, text: str, language: str | None = None) -> list[dict]:
    """Symbol records for one file (``repo``/``url`` left empty). Unknown
    languages and unparseable sources yield ``[]`` — the converter then falls
    back to its window chunker, never an error."""
    language = language or language_of(path)
    fn = _EXTRACTORS.get(language or "")
    if fn is None:
        return []
    try:
        return fn(path, text)
    except (SyntaxError, ValueError, RecursionError, IndexError):
        return []


def extract_repo(
    repo: str, root: str, branch: str = "main", write: bool = True, paths=None
) -> list[dict]:
    """Every symbol under a clone; writes ``analysis/<repo>/symbols.jsonl``."""
    out: list[dict] = []
    for rel in paths if paths is not None else iter_source_files(root):
        text = read_text(os.path.join(root, rel))
        if text is None:
            continue
        for rec in extract(rel, text):
            rec["repo"] = repo
            rec["url"] = blob_url(repo, branch, rel, rec["start_line"], rec["end_line"])
            out.append(rec)
    if write:
        p = fd.path("analysis", fd.repo_slug(repo), "symbols.jsonl")
        os.makedirs(os.path.dirname(p), exist_ok=True)
        if os.path.exists(p):
            os.remove(p)
        fd.append_jsonl(p, out)
    return out


def iter_source_files(root: str) -> list[str]:
    """Relative paths of every recognised source file under ``root`` (sorted)."""
    found = []
    for base, dirs, names in os.walk(root):
        dirs[:] = sorted(d for d in dirs if d not in SKIP_DIRS)
        for n in sorted(names):
            if language_of(n) is None or n.endswith((".min.js", ".lock")):
                continue
            full = os.path.join(base, n)
            try:
                if os.path.getsize(full) > MAX_FILE_BYTES:
                    continue
            except OSError:
                continue
            found.append(os.path.relpath(full, root).replace(os.sep, "/"))
    return found


def read_text(full: str) -> str | None:
    try:
        with open(full, "rb") as f:
            data = f.read()
    except OSError:
        return None
    if b"\x00" in data[:4096]:
        return None
    return data.decode("utf-8", errors="replace")


# ---------------------------------------------------------------------------
# Python (ast)
# ---------------------------------------------------------------------------
def _py_imports(stmts) -> list[str]:
    out = []
    for n in stmts:
        if isinstance(n, ast.Import):
            out.extend(a.name for a in n.names)
        elif isinstance(n, ast.ImportFrom):
            base = "." * n.level + (n.module or "")
            out.extend(
                f"{base}.{a.name}" if base and not base.endswith(".") else base + a.name
                for a in n.names
            )
    return out


def _call_name(func) -> str:
    if isinstance(func, ast.Name):
        return func.id
    if isinstance(func, ast.Attribute):
        head = _call_name(func.value)
        return f"{head}.{func.attr}" if head else func.attr
    return ""


def _py_calls(node) -> list[str]:
    out = []
    for n in ast.walk(node):
        if isinstance(n, ast.Call):
            name = _call_name(n.func)
            if name:
                out.append(name)
    return out


def _py_signature(node) -> str:
    args = ast.unparse(node.args)
    ret = f" -> {ast.unparse(node.returns)}" if getattr(node, "returns", None) else ""
    prefix = "async " if isinstance(node, ast.AsyncFunctionDef) else ""
    return f"{prefix}{node.name}({args}){ret}"


def _python(path: str, text: str) -> list[dict]:
    tree = ast.parse(text)
    module = module_name(path)
    lines = text.splitlines()
    out = [
        record(
            path,
            "python",
            symbol=os.path.basename(path),
            qualified=module,
            kind="module",
            docstring=ast.get_docstring(tree) or "",
            start_line=1,
            end_line=max(1, len(lines)),
            calls=[],
            imports=_py_imports(tree.body),
        )
    ]

    def func(node, qualified, kind):
        inner_imports = _py_imports([n for n in ast.walk(node) if n is not node])
        out.append(
            record(
                path,
                "python",
                symbol=node.name,
                qualified=qualified,
                kind=kind,
                signature=_py_signature(node),
                decorators=[ast.unparse(d) for d in node.decorator_list],
                docstring=ast.get_docstring(node) or "",
                start_line=node.lineno,
                end_line=node.end_lineno or node.lineno,
                calls=_py_calls(node),
                imports=inner_imports,
            )
        )

    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            func(node, f"{module}.{node.name}" if module else node.name, "function")
        elif isinstance(node, ast.ClassDef):
            cq = f"{module}.{node.name}" if module else node.name
            bases = ", ".join(ast.unparse(b) for b in node.bases)
            methods = [
                m for m in node.body if isinstance(m, (ast.FunctionDef, ast.AsyncFunctionDef))
            ]
            head_end = (methods[0].lineno - 1) if methods else (node.end_lineno or node.lineno)
            out.append(
                record(
                    path,
                    "python",
                    symbol=node.name,
                    qualified=cq,
                    kind="class",
                    signature=f"{node.name}({bases})" if bases else node.name,
                    decorators=[ast.unparse(d) for d in node.decorator_list],
                    docstring=ast.get_docstring(node) or "",
                    start_line=node.lineno,
                    end_line=max(node.lineno, head_end),
                    calls=[],
                    imports=[],
                )
            )
            for m in methods:
                func(m, f"{cq}.{m.name}", "method")
    return out


# ---------------------------------------------------------------------------
# brace-language helpers (JS/TS, Java, C#, Go)
# ---------------------------------------------------------------------------
_C_KEYWORDS = {
    "if",
    "for",
    "while",
    "switch",
    "catch",
    "return",
    "function",
    "new",
    "typeof",
    "throw",
    "else",
    "do",
    "try",
    "await",
    "yield",
    "delete",
    "void",
    "instanceof",
    "super",
    "case",
    "with",
    "synchronized",
    "sizeof",
    "lock",
    "using",
    "fixed",
    "foreach",
    "select",
    "go",
    "defer",
    "range",
    "chan",
    "func",
    "import",
    "package",
    "class",
    "interface",
    "struct",
    "enum",
    "namespace",
    "export",
    "default",
    "const",
    "let",
    "var",
}
_MODIFIERS = {
    "public",
    "private",
    "protected",
    "internal",
    "static",
    "final",
    "abstract",
    "virtual",
    "override",
    "async",
    "sealed",
    "extern",
    "unsafe",
    "new",
    "partial",
    "readonly",
    "volatile",
    "synchronized",
    "native",
    "default",
    "transient",
    "strictfp",
}
_CALL_RE = re.compile(r"(?<![\w$.])([A-Za-z_$][\w$]*(?:\.[A-Za-z_$][\w$]*)*)\s*\(")


def _calls_in(text: str, own: str = "") -> list[str]:
    out = []
    for m in _CALL_RE.finditer(text):
        name = m.group(1)
        head = name.split(".")[0]
        if head in _C_KEYWORDS or name == own:
            continue
        out.append(name)
    return out


def _strip_comments_line(line: str) -> str:
    """Drop ``// …`` and ``/* … */`` outside string literals (single line)."""
    out, j, s = [], 0, None
    while j < len(line):
        ch = line[j]
        if s:
            out.append(ch)
            if ch == "\\":
                out.append(line[j + 1 : j + 2])
                j += 2
                continue
            if ch == s:
                s = None
            j += 1
            continue
        if line.startswith("//", j):
            break
        if line.startswith("/*", j):
            k = line.find("*/", j + 2)
            if k < 0:
                break
            j = k + 2
            continue
        if ch in "\"'`":
            s = ch
        out.append(ch)
        j += 1
    return "".join(out)


def depth_map(lines: list[str]) -> list[int]:
    """Brace depth at the START of each line, ignoring strings and comments.
    Template literals may span lines; ``'``/``"`` reset at end of line."""
    depths, depth, in_block, s = [], 0, False, None
    for line in lines:
        depths.append(depth)
        j = 0
        while j < len(line):
            ch = line[j]
            if in_block:
                if line.startswith("*/", j):
                    in_block = False
                    j += 2
                    continue
                j += 1
                continue
            if s:
                if ch == "\\":
                    j += 2
                    continue
                if ch == s:
                    s = None
                j += 1
                continue
            if line.startswith("//", j):
                break
            if line.startswith("/*", j):
                in_block = True
                j += 2
                continue
            if ch in "\"'`":
                s = ch
            elif ch == "{":
                depth += 1
            elif ch == "}":
                depth = max(0, depth - 1)
            j += 1
        if s in ("'", '"'):
            s = None
    return depths


def block_end(lines: list[str], start: int, depths: list[int]) -> int:
    """Index of the line closing the block opened at/after ``start`` (the first
    ``{`` within three lines); the start line itself when no block opens."""
    base = depths[start]
    opened_at = None
    for i in range(start, min(start + 3, len(lines))):
        if "{" in _strip_comments_line(lines[i]):
            opened_at = i
            break
        if _strip_comments_line(lines[i]).rstrip().endswith(";"):
            return start
    if opened_at is None:
        return start
    for i in range(opened_at + 1, len(lines)):
        if depths[i] <= base:
            # the closing brace is on line i-1 unless it sits alone on line i
            return i - 1 if depths[i] < base or lines[i].strip() != "}" else i
    return len(lines) - 1


def preceding_comment(lines: list[str], idx: int, *, annotation=None) -> tuple[str, list[str]]:
    """(doc comment text, annotation lines) directly above line ``idx``.
    Annotations (``@Foo`` / ``[Attr]``) between the comment and the declaration
    are returned separately; a blank line ends the search."""
    ann: list[str] = []
    i = idx - 1
    while i >= 0 and annotation and annotation.match(lines[i].strip()):
        ann.insert(0, lines[i].strip())
        i -= 1
    doc: list[str] = []
    if i >= 0:
        s = lines[i].strip()
        if s.endswith("*/"):
            j = i
            while j >= 0:
                doc.insert(0, lines[j])
                if "/*" in lines[j]:
                    break
                j -= 1
        elif s.startswith(("//", "#", "--")) and not s.startswith("#!"):
            marker = "//" if s.startswith("//") else ("--" if s.startswith("--") else "#")
            j = i
            while j >= 0 and lines[j].strip().startswith(marker):
                doc.insert(0, lines[j])
                j -= 1
    return clean_comment("\n".join(doc)), ann


def clean_comment(text: str) -> str:
    out = []
    for raw in text.splitlines():
        s = raw.strip()
        s = re.sub(r"^/\*+\s?", "", s)
        s = re.sub(r"\s*\*+/\s*$", "", s)
        s = re.sub(r"^\*+\s?", "", s)
        s = re.sub(r"^(///|//|--|#)\s?", "", s)
        s = re.sub(r"^<summary>|</summary>$", "", s).strip()
        out.append(s)
    return "\n".join(out).strip()


# ---------------------------------------------------------------------------
# JavaScript / TypeScript
# ---------------------------------------------------------------------------
_JS_FUNC = re.compile(
    r"^(?P<mods>(?:export\s+|default\s+|async\s+)*)function\s*\*?\s*"
    r"(?P<name>[A-Za-z_$][\w$]*)\s*(?:<[^>]*>)?\s*\((?P<params>[^)]*)\)"
)
_JS_ARROW = re.compile(
    r"^(?P<mods>(?:export\s+)*)(?:const|let|var)\s+(?P<name>[A-Za-z_$][\w$]*)\s*(?::[^=]+?)?"
    r"=\s*(?P<fn>(?:async\s+)?(?:function\b\s*\*?\s*\((?P<fparams>[^)]*)\)|"
    r"(?:\((?P<params>[^)]*)\)|[A-Za-z_$][\w$]*)\s*(?::\s*[^=]+?)?\s*=>))"
)
_JS_CLASS = re.compile(
    r"^(?P<mods>(?:export\s+|default\s+|abstract\s+|declare\s+)*)"
    r"(?P<kw>class|interface|enum)\s+(?P<name>[A-Za-z_$][\w$]*)(?P<rest>[^{]*)"
)
_JS_METHOD = re.compile(
    r"^(?P<mods>(?:static\s+|async\s+|public\s+|private\s+|protected\s+|readonly\s+|"
    r"get\s+|set\s+|override\s+|abstract\s+)*)\*?\s*(?P<name>[A-Za-z_$#][\w$]*)\s*"
    r"(?:<[^>]*>)?\s*\((?P<params>[^)]*)\)\s*(?::\s*[^{;]+)?\s*[{;]"
)
_JS_IMPORT = re.compile(r"""^\s*import\s+(?:[^'"]*\s+from\s+)?['"]([^'"]+)['"]""")
_JS_REQUIRE = re.compile(r"""require\(\s*['"]([^'"]+)['"]\s*\)""")


def _js_like(path: str, text: str, language: str) -> list[dict]:
    lines = text.splitlines()
    depths = depth_map(lines)
    module = module_name(path)
    imports = []
    for line in lines:
        m = _JS_IMPORT.match(line)
        if m:
            imports.append(m.group(1))
        imports.extend(_JS_REQUIRE.findall(line))
    out = [
        record(
            path,
            language,
            symbol=os.path.basename(path),
            qualified=module,
            kind="module",
            docstring=preceding_comment(lines, _first_code_line(lines))[0]
            if lines and lines[0].lstrip().startswith("/*")
            else "",
            start_line=1,
            end_line=max(1, len(lines)),
            imports=imports,
        )
    ]

    def add(name, qualified, kind, sig, mods, i, end):
        body = "\n".join(lines[i : end + 1])
        doc, _ = preceding_comment(lines, i, annotation=re.compile(r"^@\w"))
        decos = [m for m in re.split(r"\s+", mods.strip()) if m]
        out.append(
            record(
                path,
                language,
                symbol=name,
                qualified=qualified,
                kind=kind,
                signature=sig,
                decorators=decos,
                docstring=doc,
                start_line=i + 1,
                end_line=end + 1,
                calls=_calls_in(body, own=name),
            )
        )

    i = 0
    while i < len(lines):
        s = lines[i].strip()
        if depths[i] != 0 or not s:
            i += 1
            continue
        m = _JS_CLASS.match(s)
        if m:
            end = block_end(lines, i, depths)
            name = m.group("name")
            cq = f"{module}.{name}"
            rest = m.group("rest").strip()
            is_class = m.group("kw") == "class"
            add(
                name,
                cq,
                "class",
                f"{m.group('kw')} {name}{(' ' + rest) if rest else ''}",
                m.group("mods"),
                i,
                i if is_class else end,
            )
            if is_class:
                # the class record covers the header only; methods follow
                j = i + 1
                while j <= end:
                    t = lines[j].strip()
                    if depths[j] == 1 and t and not t.startswith(("//", "*", "/*", "}")):
                        mm = _JS_METHOD.match(t)
                        if mm and mm.group("name") not in _C_KEYWORDS:
                            mend = block_end(lines, j, depths)
                            add(
                                mm.group("name"),
                                f"{cq}.{mm.group('name')}",
                                "method",
                                f"{mm.group('name')}({mm.group('params').strip()})",
                                mm.group("mods"),
                                j,
                                mend,
                            )
                            j = mend + 1
                            continue
                    j += 1
            i = end + 1
            continue
        m = _JS_FUNC.match(s)
        if m:
            end = block_end(lines, i, depths)
            name = m.group("name")
            add(
                name,
                f"{module}.{name}",
                "function",
                f"{name}({m.group('params').strip()})",
                m.group("mods"),
                i,
                end,
            )
            i = end + 1
            continue
        m = _JS_ARROW.match(s)
        if m:
            end = block_end(lines, i, depths)
            name = m.group("name")
            params = (m.group("params") or m.group("fparams") or "").strip()
            if params == "" and "=>" in m.group("fn") and "(" not in m.group("fn"):
                params = m.group("fn").split("=>")[0].strip()
            add(name, f"{module}.{name}", "function", f"{name}({params})", m.group("mods"), i, end)
            i = end + 1
            continue
        i += 1
    return out


def _first_code_line(lines: list[str]) -> int:
    for i, line in enumerate(lines):
        s = line.strip()
        if s and not s.startswith(("/*", "*", "//")):
            return i
    return len(lines)


def _javascript(path, text):
    return _js_like(path, text, "javascript")


def _typescript(path, text):
    return _js_like(path, text, "typescript")


# ---------------------------------------------------------------------------
# Java
# ---------------------------------------------------------------------------
_JAVA_MODS = (
    r"(?:(?:public|private|protected|static|final|abstract|sealed|non-sealed|strictfp"
    r"|synchronized|native|default|transient|volatile)\s+)*"
)
_JAVA_TYPE = re.compile(
    rf"^(?P<mods>{_JAVA_MODS})(?P<kw>class|interface|enum|record|@interface)\s+(?P<name>\w+)"
)
_JAVA_METHOD = re.compile(
    rf"^(?P<mods>{_JAVA_MODS})(?:<[^>]+>\s+)?(?P<ret>[\w$][\w$<>\[\],.?\s]*?)\s+(?P<name>\w+)\s*"
    r"\((?P<params>[^)]*)\)\s*(?:throws\s+[\w., ]+)?\s*(?:\{.*|;)?\s*$"
)
_JAVA_CTOR = re.compile(
    rf"^(?P<mods>{_JAVA_MODS})(?P<name>[A-Z]\w*)\s*\((?P<params>[^)]*)\)\s*(?:\{{.*)?\s*$"
)
_ANNOTATION = re.compile(r"^@\w")


def _java(path: str, text: str) -> list[dict]:
    lines = text.splitlines()
    depths = depth_map(lines)
    pkg = ""
    imports = []
    for line in lines:
        m = re.match(r"^\s*package\s+([\w.]+)\s*;", line)
        if m:
            pkg = m.group(1)
        m = re.match(r"^\s*import\s+(?:static\s+)?([\w.*]+)\s*;", line)
        if m:
            imports.append(m.group(1))
    module = pkg or module_name(path)
    out = [
        record(
            path,
            "java",
            symbol=os.path.basename(path),
            qualified=module_name(path),
            kind="module",
            start_line=1,
            end_line=max(1, len(lines)),
            imports=imports,
        )
    ]
    return out + _c_like_types(
        path, "java", lines, depths, module, _JAVA_TYPE, _JAVA_METHOD, _JAVA_CTOR, _ANNOTATION
    )


def _c_like_types(path, language, lines, depths, module, type_re, method_re, ctor_re, ann_re):
    out = []

    def add(name, qualified, kind, sig, mods, ann, i, end):
        body = "\n".join(lines[i : end + 1])
        doc, ann2 = preceding_comment(lines, i, annotation=ann_re)
        decos = ann2 + [m for m in re.split(r"\s+", mods.strip()) if m]
        out.append(
            record(
                path,
                language,
                symbol=name,
                qualified=qualified,
                kind=kind,
                signature=sig,
                decorators=decos,
                docstring=doc,
                start_line=i + 1,
                end_line=end + 1,
                calls=_calls_in(body, own=name),
            )
        )

    def scan(start, stop, depth, prefix):
        j = start
        while j < stop:
            t = lines[j].strip()
            if depths[j] != depth or not t or t.startswith(("//", "*", "/*", "}", "@", "[")):
                j += 1
                continue
            m = type_re.match(t)
            if m:
                end = block_end(lines, j, depths)
                name = m.group("name")
                q = f"{prefix}.{name}"
                add(name, q, "class", f"{m.group('kw')} {name}", m.group("mods"), [], j, j)
                scan(j + 1, end + 1, depth + 1, q)
                j = end + 1
                continue
            cls = prefix.rsplit(".", 1)[-1]
            m = ctor_re.match(t) if ctor_re else None
            if m and m.group("name") == cls:
                end = block_end(lines, j, depths)
                add(
                    cls,
                    f"{prefix}.{cls}",
                    "method",
                    f"{cls}({m.group('params').strip()})",
                    m.group("mods"),
                    [],
                    j,
                    end,
                )
                j = end + 1
                continue
            m = method_re.match(t)
            if (
                m
                and m.group("name") not in _C_KEYWORDS
                and not set(m.group("ret").split()) <= _MODIFIERS
                and m.group("ret").split()[-1] not in ("new", "return", "else", "throw")
            ):
                end = block_end(lines, j, depths)
                name = m.group("name")
                add(
                    name,
                    f"{prefix}.{name}",
                    "method",
                    f"{m.group('ret').strip()} {name}({m.group('params').strip()})",
                    m.group("mods"),
                    [],
                    j,
                    end,
                )
                j = end + 1
                continue
            j += 1

    scan(0, len(lines), 0, module)
    return out


# ---------------------------------------------------------------------------
# C#
# ---------------------------------------------------------------------------
_CS_MODS = (
    r"(?:(?:public|private|protected|internal|static|virtual|override|abstract|async|sealed"
    r"|extern|unsafe|new|partial|readonly|unsafe|volatile)\s+)*"
)
_CS_TYPE = re.compile(
    rf"^(?P<mods>{_CS_MODS})(?P<kw>class|struct|interface|enum|record(?:\s+struct|\s+class)?)\s+(?P<name>\w+)"
)
_CS_METHOD = re.compile(
    rf"^(?P<mods>{_CS_MODS})(?P<ret>[\w$][\w$<>\[\],.?()\s]*?)\s+(?P<name>\w+)\s*(?:<[^>]+>)?\s*"
    r"\((?P<params>[^)]*)\)\s*(?:where\s+[^{;=]+)?\s*(?:\{.*|=>.*|;)?\s*$"
)
_CS_CTOR = re.compile(
    rf"^(?P<mods>{_CS_MODS})(?P<name>[A-Z]\w*)\s*\((?P<params>[^)]*)\)\s*"
    r"(?::\s*(?:base|this)\([^)]*\)\s*)?(?:\{.*)?\s*$"
)
_CS_ATTR = re.compile(r"^\[\w")


def _csharp(path: str, text: str) -> list[dict]:
    lines = text.splitlines()
    ns, imports = "", []
    for line in lines:
        m = re.match(r"^\s*namespace\s+([\w.]+)", line)
        if m:
            ns = m.group(1)
        m = re.match(r"^\s*using\s+(?:static\s+)?([\w.]+)\s*;", line)
        if m:
            imports.append(m.group(1))
    # a block-scoped namespace wraps everything in one brace level: unwrap it
    scoped = any(re.match(r"^\s*namespace\s+[\w.]+\s*\{?\s*$", ln) and "{" in ln for ln in lines)
    if not scoped:
        scoped = any(
            re.match(r"^\s*namespace\s+[\w.]+\s*$", ln)
            and i + 1 < len(lines)
            and lines[i + 1].strip() == "{"
            for i, ln in enumerate(lines)
        )
    depths = depth_map(lines)
    if scoped:
        depths = [max(0, d - 1) if d > 0 else 0 for d in depths]
    module = ns or module_name(path)
    out = [
        record(
            path,
            "csharp",
            symbol=os.path.basename(path),
            qualified=module_name(path),
            kind="module",
            start_line=1,
            end_line=max(1, len(lines)),
            imports=imports,
        )
    ]
    return out + _c_like_types(
        path, "csharp", lines, depths, module, _CS_TYPE, _CS_METHOD, _CS_CTOR, _CS_ATTR
    )


# ---------------------------------------------------------------------------
# Go
# ---------------------------------------------------------------------------
_GO_FUNC = re.compile(
    r"^func\s+(?:\((?P<recv>[^)]*)\)\s+)?(?P<name>\w+)\s*(?:\[[^\]]*\])?\((?P<params>[^)]*)\)(?P<ret>[^{]*)"
)
_GO_TYPE = re.compile(r"^type\s+(?P<name>\w+)\s+(?P<kw>struct|interface)\b")


def _go(path: str, text: str) -> list[dict]:
    lines = text.splitlines()
    depths = depth_map(lines)
    pkg, imports, in_imp = "", [], False
    for line in lines:
        s = line.strip()
        m = re.match(r"^package\s+(\w+)", s)
        if m:
            pkg = m.group(1)
        if s.startswith("import ("):
            in_imp = True
            continue
        if in_imp:
            if s.startswith(")"):
                in_imp = False
            else:
                m = re.search(r'"([^"]+)"', s)
                if m:
                    imports.append(m.group(1))
        else:
            m = re.match(r'^import\s+(?:\w+\s+)?"([^"]+)"', s)
            if m:
                imports.append(m.group(1))
    module = module_name(path)
    prefix = pkg or module
    out = [
        record(
            path,
            "go",
            symbol=os.path.basename(path),
            qualified=module,
            kind="module",
            start_line=1,
            end_line=max(1, len(lines)),
            imports=imports,
        )
    ]
    for i, line in enumerate(lines):
        if depths[i] != 0:
            continue
        s = line.strip()
        m = _GO_TYPE.match(s)
        if m:
            end = block_end(lines, i, depths)
            doc, _ = preceding_comment(lines, i)
            out.append(
                record(
                    path,
                    "go",
                    symbol=m.group("name"),
                    qualified=f"{prefix}.{m.group('name')}",
                    kind="class",
                    signature=f"type {m.group('name')} {m.group('kw')}",
                    docstring=doc,
                    start_line=i + 1,
                    end_line=end + 1,
                )
            )
            continue
        m = _GO_FUNC.match(s)
        if m:
            end = block_end(lines, i, depths)
            doc, _ = preceding_comment(lines, i)
            name = m.group("name")
            recv = (m.group("recv") or "").strip()
            body = "\n".join(lines[i : end + 1])
            if recv:
                rtype = recv.split()[-1].lstrip("*")
                q, kind = f"{prefix}.{rtype}.{name}", "method"
            else:
                q, kind = f"{prefix}.{name}", "function"
            sig = f"func {('(' + recv + ') ') if recv else ''}{name}({m.group('params').strip()})"
            ret = m.group("ret").strip()
            if ret:
                sig += f" {ret}"
            out.append(
                record(
                    path,
                    "go",
                    symbol=name,
                    qualified=q,
                    kind=kind,
                    signature=sig,
                    docstring=doc,
                    start_line=i + 1,
                    end_line=end + 1,
                    calls=_calls_in(body, own=name),
                )
            )
    return out


# ---------------------------------------------------------------------------
# SQL — one record per statement
# ---------------------------------------------------------------------------
_SQL_HEAD = re.compile(
    r"^\s*(?P<verb>CREATE(?:\s+OR\s+REPLACE)?(?:\s+(?:TEMP|TEMPORARY|UNIQUE|MATERIALIZED))?\s+"
    r"(?P<obj>TABLE|VIEW|INDEX|FUNCTION|PROCEDURE|TRIGGER|SCHEMA|TYPE|SEQUENCE|EXTENSION|DATABASE)"
    r"(?:\s+IF\s+NOT\s+EXISTS)?|ALTER\s+TABLE|DROP\s+TABLE|INSERT\s+INTO|UPDATE|DELETE\s+FROM|"
    r"SELECT|WITH|TRUNCATE|GRANT|MERGE\s+INTO)\b\s*(?P<name>[\w.\"`]+)?",
    re.I,
)


def _sql(path: str, text: str) -> list[dict]:
    lines = text.splitlines()
    module = module_name(path)
    out = []
    stmts: list[tuple[int, int, str]] = []  # (start_idx, end_idx, text)
    buf, start, s, in_block, counter = [], None, None, False, {}
    for i, line in enumerate(lines):
        j = 0
        content = line
        while j < len(content):
            ch = content[j]
            if in_block:
                if content.startswith("*/", j):
                    in_block = False
                    j += 2
                    continue
                j += 1
                continue
            if s:
                if ch == s:
                    s = None
                j += 1
                continue
            if content.startswith("--", j):
                break
            if content.startswith("/*", j):
                in_block = True
                j += 2
                continue
            if ch in "'\"`":
                s = ch
            elif ch == ";":
                if start is not None:
                    buf.append(content[:j])
                    stmts.append((start, i, "\n".join(buf)))
                buf, start = [], None
                content = content[j + 1 :]
                j = 0
                continue
            elif not ch.isspace() and start is None:
                start = i
            j += 1
        if start is not None:
            buf.append(content)
    if start is not None and "".join(buf).strip():
        stmts.append((start, len(lines) - 1, "\n".join(buf)))
    for start, end, body in stmts:
        first = body.strip().splitlines()[0] if body.strip() else ""
        m = _SQL_HEAD.match(body.strip())
        if m:
            verb = re.sub(r"\s+", " ", m.group("verb").upper())
            name = (m.group("name") or "").strip('"`')
            if verb.startswith("CREATE") and name:
                symbol = name
            else:
                head = verb.split()[0].lower()
                symbol = f"{head}:{name}" if name and head not in ("select", "with") else head
        else:
            symbol = first.split()[0].lower() if first else "statement"
        counter[symbol] = counter.get(symbol, 0) + 1
        if counter[symbol] > 1:
            symbol = f"{symbol}#{counter[symbol]}"
        doc, _ = preceding_comment(lines, start)
        out.append(
            record(
                path,
                "sql",
                symbol=symbol,
                qualified=f"{module}.{symbol}",
                kind="statement",
                signature=re.sub(r"\s+", " ", first)[:120],
                docstring=doc,
                start_line=start + 1,
                end_line=end + 1,
            )
        )
    return out


# ---------------------------------------------------------------------------
# YAML — one record per top-level key
# ---------------------------------------------------------------------------
_YAML_KEY = re.compile(r"^(?P<key>[A-Za-z0-9_.$/@-]+|\"[^\"]+\"|'[^']+')\s*:(?P<rest>.*)$")


def _yaml(path: str, text: str) -> list[dict]:
    lines = text.splitlines()
    module = module_name(path)
    keys: list[tuple[int, str, str]] = []
    for i, line in enumerate(lines):
        if line.startswith((" ", "\t", "-", "#")) or not line.strip():
            continue
        m = _YAML_KEY.match(line.rstrip())
        if m:
            keys.append((i, m.group("key").strip("\"'"), m.group("rest").strip()))
    out = []
    seen: dict[str, int] = {}
    for n, (i, key, rest) in enumerate(keys):
        nxt = keys[n + 1][0] if n + 1 < len(keys) else len(lines)
        end = nxt - 1
        while end > i and (
            not lines[end].strip()
            or lines[end].lstrip().startswith("#")
            or lines[end].strip() == "---"
        ):
            end -= 1
        doc, _ = preceding_comment(lines, i)
        seen[key] = seen.get(key, 0) + 1
        symbol = key if seen[key] == 1 else f"{key}#{seen[key]}"
        out.append(
            record(
                path,
                "yaml",
                symbol=symbol,
                qualified=f"{module}.{symbol}",
                kind="key",
                signature=f"{key}: {rest[:80]}" if rest else f"{key}:",
                docstring=doc,
                start_line=i + 1,
                end_line=end + 1,
            )
        )
    return out


# ---------------------------------------------------------------------------
# Notebooks — one record per cell
# ---------------------------------------------------------------------------
def _notebook(path: str, text: str) -> list[dict]:
    try:
        nb = json.loads(text)
    except ValueError:
        return []
    cells = nb.get("cells") if isinstance(nb, dict) else None
    if not isinstance(cells, list):
        return []
    module = module_name(path)
    out, line, prev_md = [], 1, ""
    for i, cell in enumerate(cells, start=1):
        src = cell.get("source", "")
        src = "".join(src) if isinstance(src, list) else str(src)
        n = max(1, len(src.splitlines()))
        ctype = cell.get("cell_type", "code")
        calls, imports = [], []
        if ctype == "code":
            code = "\n".join(
                ln for ln in src.splitlines() if not ln.lstrip().startswith(("%", "!", "?"))
            )
            try:
                tree = ast.parse(code)
                calls = _py_calls(tree)
                imports = _py_imports(list(ast.walk(tree)))
            except SyntaxError:
                pass
            doc = prev_md[:300]
        else:
            doc = src.strip()[:300]
        out.append(
            record(
                path,
                "notebook",
                symbol=f"cell-{i}",
                qualified=f"{module}.cell-{i}",
                kind="cell",
                signature=f"{ctype} cell {i}",
                docstring=doc,
                start_line=line,
                end_line=line + n - 1,
                calls=calls,
                imports=imports,
            )
        )
        prev_md = src.strip() if ctype == "markdown" else ""
        line += n + 1
    return out


_EXTRACTORS = {
    "python": _python,
    "javascript": _javascript,
    "typescript": _typescript,
    "java": _java,
    "go": _go,
    "csharp": _csharp,
    "sql": _sql,
    "yaml": _yaml,
    "notebook": _notebook,
}

LANGUAGES = tuple(_EXTRACTORS)
