"""Call graph (T38) — ``ast.Call`` names (and the brace-language call scan)
resolved IN-REPOSITORY to qualified symbols.

Output (``analysis/<repo>/callgraph.json``)::

    {"nodes": [qualified, ...], "edges": [{"from", "to", "kind": "calls"}, ...]}

Resolution order for a call ``c`` made from symbol ``S`` in module ``M``:

1. ``self.x`` / ``this.x`` / ``cls.x`` → a method ``x`` on S's class
2. ``M.c`` (a module-local function, or ``Class.method`` / ``Class``)
3. an import of S's module ending in ``.c`` that names a repository symbol
4. a fully qualified name that exists as-is
5. a UNIQUE symbol anywhere in the repository whose qualified name ends in ``.c``

Ambiguous or external calls produce no edge — the graph never guesses.
"""

from __future__ import annotations

import os

from .. import fabric_data as fd
from . import symbols as _symbols

CALLABLE_KINDS = ("class", "function", "method", "cell", "statement")


def build(symbols: list[dict]) -> dict:
    exact = {s["qualified"]: s for s in symbols}
    by_suffix: dict[str, list[str]] = {}
    for q in exact:
        parts = q.split(".")
        for i in range(1, len(parts) + 1):
            by_suffix.setdefault(".".join(parts[-i:]), []).append(q)
    module_imports: dict[str, list[str]] = {}
    for s in symbols:
        if s["kind"] == "module":
            module_imports[_symbols.module_name(s["path"])] = s.get("imports", [])

    def resolve(call: str, module: str, cls: str | None, mod_imports: list[str]) -> str | None:
        parts = call.split(".")
        if parts[0] in ("self", "this", "cls") and cls and len(parts) >= 2:
            cand = f"{module}.{cls}.{parts[1]}"
            return cand if cand in exact else None
        cand = f"{module}.{call}"
        if cand in exact:
            return cand
        for imp in mod_imports:
            name = imp.lstrip(".")
            if name.endswith("." + parts[0]) or name == parts[0]:
                tail = ".".join(parts[1:])
                for q in by_suffix.get(name if not tail else f"{name}.{tail}", []):
                    return q
                for q in by_suffix.get(parts[-1], []):
                    if q.endswith(name.split(".")[-1] + ("." + tail if tail else "")):
                        return q
        if call in exact:
            return call
        cands = by_suffix.get(call, [])
        if len(cands) == 1:
            return cands[0]
        return None

    nodes: set[str] = set()
    edges: list[dict] = []
    seen: set[tuple[str, str]] = set()
    for s in symbols:
        if s["kind"] not in CALLABLE_KINDS:
            continue
        nodes.add(s["qualified"])
        module = _symbols.module_name(s["path"])
        cls = None
        if s["kind"] == "method":
            head = s["qualified"].rsplit(".", 2)
            cls = head[-2] if len(head) >= 2 else None
        imports = module_imports.get(module, []) + s.get("imports", [])
        for call in s.get("calls", []):
            target = resolve(call, module, cls, imports)
            if not target or target == s["qualified"]:
                continue
            key = (s["qualified"], target)
            if key in seen:
                continue
            seen.add(key)
            edges.append({"from": s["qualified"], "to": target, "kind": "calls"})
            nodes.add(target)
    return {"nodes": sorted(nodes), "edges": sorted(edges, key=lambda e: (e["from"], e["to"]))}


def degrees(graph: dict) -> dict[str, int]:
    deg: dict[str, int] = dict.fromkeys(graph.get("nodes", []), 0)
    for e in graph.get("edges", []):
        deg[e["from"]] = deg.get(e["from"], 0) + 1
        deg[e["to"]] = deg.get(e["to"], 0) + 1
    return deg


def most_connected(graph: dict, symbols: list[dict], n: int = 30) -> list[dict]:
    """The ``n`` symbols with the highest degree (ties by name), with their
    docstrings — the summary's input."""
    deg = degrees(graph)
    by_q = {s["qualified"]: s for s in symbols}
    ranked = sorted(deg.items(), key=lambda kv: (-kv[1], kv[0]))
    out = []
    for q, d in ranked[:n]:
        s = by_q.get(q)
        if not s:
            continue
        out.append(
            {
                "qualified": q,
                "path": s["path"],
                "kind": s["kind"],
                "degree": d,
                "signature": s.get("signature", ""),
                "docstring": (s.get("docstring") or "")[:300],
                "start_line": s["start_line"],
                "end_line": s["end_line"],
            }
        )
    return out


def write(repo: str, graph: dict) -> str:
    p = fd.path("analysis", fd.repo_slug(repo), "callgraph.json")
    os.makedirs(os.path.dirname(p), exist_ok=True)
    return fd.write_json(p, graph)
