"""Table queries over the ingested sheets (T42).

Every spreadsheet sheet the ingestion track converts lands at
``tables/<doc>/<sheet>.sqlite`` as one table named ``t`` with typed columns.
This module is the ONLY way a question (or the agent) reads them:

* ``describe_table`` — columns, row count and a sample, for the model to plan a
  query and for the aggregate to resolve column names.
* ``run_table_query`` — a single ``SELECT`` over ``t``, validated by a
  tokenising parser (no ``;``, no ``ATTACH``/``PRAGMA``, no mutation, only the
  table ``t`` referenced; sub-selects are fine), executed on a read-only
  connection (``mode=ro`` + ``query_only``) with ``LIMIT`` capped at 200 rows.

The result always carries a citation to the sheet file, so an answer built from
it resolves to the exact source (I2).
"""

from __future__ import annotations

import os
import re
import sqlite3

from .. import fabric_data as fd

MAX_ROWS = 200

_FORBIDDEN = {
    "ATTACH",
    "DETACH",
    "PRAGMA",
    "INSERT",
    "UPDATE",
    "DELETE",
    "DROP",
    "ALTER",
    "CREATE",
    "REPLACE",
    "VACUUM",
    "REINDEX",
    "TRIGGER",
    "TRANSACTION",
    "BEGIN",
    "COMMIT",
    "ROLLBACK",
    "SAVEPOINT",
    "RELEASE",
    "LOAD_EXTENSION",
    "INTO",
}
_KEYWORDS_AFTER_TABLE_REF = {"FROM", "JOIN"}
_TOKEN = re.compile(
    r"""
    (?P<string>'(?:[^']|'')*')            # 'string literal'
  | (?P<qident>"(?:[^"]|"")*"|`[^`]*`|\[[^\]]*\])   # "quoted identifier"
  | (?P<number>\d+(?:\.\d+)?)
  | (?P<word>[A-Za-z_][A-Za-z_0-9.]*)
  | (?P<op><=|>=|<>|!=|\|\||[-+*/%<>=(),.])
  | (?P<semi>;)
  | (?P<ws>\s+)
  | (?P<other>.)
    """,
    re.X,
)


class TableQueryError(ValueError):
    """The SQL was refused by the validator (never sent to SQLite)."""


def _strip_comments(sql: str) -> str:
    sql = re.sub(r"/\*.*?\*/", " ", sql, flags=re.S)
    return re.sub(r"--[^\n]*", " ", sql)


def tokenise(sql: str) -> list[tuple[str, str]]:
    """``[(kind, text)]`` — strings and quoted identifiers stay opaque."""
    out = []
    for m in _TOKEN.finditer(_strip_comments(sql)):
        kind = m.lastgroup
        if kind == "ws":
            continue
        if kind == "other":
            raise TableQueryError(f"unexpected character {m.group()!r} in SQL")
        out.append((kind, m.group()))
    return out


def validate_select(sql: str, max_rows: int = MAX_ROWS) -> str:
    """Return a normalised, LIMIT-capped SELECT or raise ``TableQueryError``.

    Rules: exactly one statement; it starts with ``SELECT`` (or ``WITH`` for a
    CTE); none of the mutation/attach/pragma keywords appear anywhere (not even
    inside a sub-select); every ``FROM``/``JOIN`` table reference is ``t`` or a
    parenthesised sub-select or a CTE name declared in the statement; the
    ``LIMIT`` is capped at ``max_rows`` (appended when absent)."""
    if not isinstance(sql, str) or not sql.strip():
        raise TableQueryError("empty SQL")
    toks = tokenise(sql)
    if any(k == "semi" for k, _ in toks):
        raise TableQueryError("multiple statements are not allowed (';')")
    words = [(i, t.upper()) for i, (k, t) in enumerate(toks) if k == "word"]
    if not words or words[0][1] not in ("SELECT", "WITH"):
        raise TableQueryError("only a single SELECT statement is allowed")
    for _i, w in words:
        if w in _FORBIDDEN:
            raise TableQueryError(f"'{w}' is not allowed in a table query")
        if w.startswith("SQLITE_"):
            raise TableQueryError("system tables are not readable")
    # CTE names: WITH name AS ( ... ), name2 AS ( ... )
    ctes: set[str] = set()
    if words[0][1] == "WITH":
        for j in range(1, len(toks) - 1):
            if (
                toks[j][0] == "word"
                and toks[j + 1][0] == "word"
                and toks[j + 1][1].upper() == "AS"
                and (j == 1 or toks[j - 1][1] in (",", "WITH", "with", "With"))
            ):
                ctes.add(toks[j][1].lower())
    # table references
    for i, (k, t) in enumerate(toks):
        if k == "word" and t.upper() in _KEYWORDS_AFTER_TABLE_REF and i + 1 < len(toks):
            nk, nt = toks[i + 1]
            if nk == "op" and nt == "(":
                continue  # sub-select
            if nk == "word" and (nt.lower() == "t" or nt.lower() in ctes):
                continue
            if nk == "qident" and nt.strip('"`[]').lower() == "t":
                continue
            raise TableQueryError(f"only the table 't' may be queried (saw {nt!r})")
    # LIMIT cap: replace an existing numeric LIMIT, else append one.
    text = " ".join(t for _, t in toks)
    m = re.search(r"\bLIMIT\s+(\d+)", text, re.I)
    if m:
        if int(m.group(1)) > max_rows:
            text = text[: m.start(1)] + str(max_rows) + text[m.end(1) :]
    else:
        text = f"{text} LIMIT {max_rows}"
    return text


def table_path(doc_id: str, sheet: str) -> str:
    return fd.path("tables", doc_id, f"{sheet}.sqlite")


def _facts_entry(doc_id: str, sheet: str) -> dict:
    from ..facts import load_facts

    for t in load_facts().get("tables", []):
        if t.get("doc_id") == doc_id and t.get("sheet") == sheet:
            return t
    return {}


def _resolve_path(doc_id: str, sheet: str) -> str:
    entry = _facts_entry(doc_id, sheet)
    p = entry.get("path") or ""
    if p and not os.path.isabs(p):
        p = os.path.join(fd.fabric_root(), p)
    if p and os.path.exists(p):
        return p
    p = table_path(doc_id, sheet)
    if not os.path.exists(p):
        raise TableQueryError(f"no table for {doc_id!r} sheet {sheet!r}")
    return p


def _connect(path: str) -> sqlite3.Connection:
    conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    conn.execute("PRAGMA query_only = 1")  # belt and braces on top of mode=ro
    return conn


def citation_for(doc_id: str, sheet: str, **locator) -> dict:
    """The tool-shaped citation for a sheet (title, url, document_id, locator)."""
    entry = _facts_entry(doc_id, sheet)
    title = f"{entry.get('doc_title') or doc_id} · {sheet}"
    loc = {"doc_id": doc_id, "sheet": sheet, "path": entry.get("path") or table_path(doc_id, sheet)}
    loc.update(locator)
    return {
        "title": title,
        "url": entry.get("url", ""),
        "document_id": doc_id,
        "locator": loc,
        "kind": "cell",
    }


def describe_table(doc_id: str, sheet: str, sample_rows: int = 5) -> dict:
    """``{columns: [{name, type}], rows: n, sample: [[...]], path, citation}``."""
    path = _resolve_path(doc_id, sheet)
    conn = _connect(path)
    try:
        cols = [{"name": r[1], "type": r[2] or ""} for r in conn.execute("PRAGMA table_info(t)")]
        n = conn.execute("SELECT COUNT(*) FROM t").fetchone()[0]
        sample = [list(r) for r in conn.execute(f"SELECT * FROM t LIMIT {int(sample_rows)}")]
    finally:
        conn.close()
    return {
        "doc_id": doc_id,
        "sheet": sheet,
        "columns": cols,
        "rows": int(n),
        "sample": sample,
        "path": path,
        "citation": citation_for(doc_id, sheet),
    }


def run_table_query(doc_id: str, sheet: str, sql: str, max_rows: int = MAX_ROWS) -> dict:
    """Validate and run one SELECT over ``t``; ``{columns, rows, row_count, sql, citation}``."""
    max_rows = max(1, min(int(max_rows), MAX_ROWS))
    safe = validate_select(sql, max_rows)
    path = _resolve_path(doc_id, sheet)
    conn = _connect(path)
    try:
        try:
            cur = conn.execute(safe)
        except sqlite3.Error as e:  # a bad column name, a typo — surfaced, not hidden
            raise TableQueryError(f"query failed: {e}") from e
        columns = [d[0] for d in cur.description or []]
        rows = [list(r) for r in cur.fetchall()]
    finally:
        conn.close()
    return {
        "columns": columns,
        "rows": rows,
        "row_count": len(rows),
        "sql": safe,
        "citation": citation_for(doc_id, sheet, sql=safe, row=1 if rows else 0),
    }
