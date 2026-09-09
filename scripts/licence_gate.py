"""Licence gate (invariant I14 — licence-clean shipped artefacts).

    python3 scripts/licence_gate.py                     # human report, exit 0 = clean
    python3 scripts/licence_gate.py --json              # machine-readable report
    python3 scripts/licence_gate.py --manifest ci/licence_manifest.json --repo .

What it checks
--------------
1. The manifest itself: every component names a licence that belongs to a
   declared licence class and a linkage kind that has a rule; rejected
   alternatives are marked ``approved: false``; approved components are not
   marked ``rejected``.
2. Policy: a component may only use a licence class its linkage allows —
   ``runtime`` / ``optional-runtime`` / ``model-weights`` must be permissive
   (PSF, MIT, Apache-2.0, BSD, ISC, PostgreSQL …); ``tooling`` may be any OSI
   licence because it is run, not shipped; ``external-service`` may be
   copyleft (AGPL MinIO/Grafana) because its code is never linked.
3. The code: every non-stdlib import under the scanned packages must be
   declared in the manifest (``import_names``); an import of a *rejected*
   component fails; an ``optional-runtime`` import must be guarded — inside
   ``try/except ImportError`` or deferred into a function body — so the
   shipped process never hard-depends on it at import time.
4. Requirements files (if any exist): every distribution listed must be a
   declared, approved component.

Exit status: 0 clean · 1 violations · 2 manifest missing/invalid or bad args.
Pure standard library; deterministic (sorted) output.
"""
from __future__ import annotations

import argparse
import ast
import json
import os
import re
import sys

DEFAULT_MANIFEST = os.path.join("ci", "licence_manifest.json")
_SKIP_DIRS = {".git", "__pycache__", "data", "node_modules", ".venv", "venv"}
_IMPORT_ERRORS = {"ImportError", "ModuleNotFoundError", "Exception", "BaseException"}
_REQ_LINE = re.compile(r"^\s*([A-Za-z0-9][A-Za-z0-9._-]*)")


class ManifestError(ValueError):
    """The manifest is missing or structurally invalid."""


# --------------------------------------------------------------------------
# manifest
# --------------------------------------------------------------------------
def load_manifest(path: str) -> dict:
    """Read + validate the manifest; raise ``ManifestError`` on structural problems."""
    try:
        with open(path, encoding="utf-8") as fh:
            m = json.load(fh)
    except FileNotFoundError as e:
        raise ManifestError(f"manifest not found: {path}") from e
    except ValueError as e:
        raise ManifestError(f"manifest is not valid JSON: {e}") from e
    problems = validate_manifest(m)
    if problems:
        raise ManifestError("; ".join(problems))
    return m


def validate_manifest(m: dict) -> list[str]:
    """Structural validation only (policy violations are reported by ``evaluate``)."""
    out: list[str] = []
    if not isinstance(m, dict):
        return ["manifest root must be an object"]
    policy = m.get("policy")
    if not isinstance(policy, dict):
        return ["manifest.policy missing"]
    classes = policy.get("licence_classes")
    rules = policy.get("linkage_rules")
    if not isinstance(classes, dict) or not classes:
        out.append("policy.licence_classes missing")
    if not isinstance(rules, dict) or not rules:
        out.append("policy.linkage_rules missing")
    if out:
        return out
    known_classes = set(classes)
    for linkage, allowed in rules.items():
        for cls in allowed:
            if cls not in known_classes:
                out.append(f"linkage_rules[{linkage}] names unknown class {cls!r}")
    comps = m.get("components")
    if not isinstance(comps, list) or not comps:
        out.append("components missing or empty")
        return out
    seen = set()
    for i, c in enumerate(comps):
        if not isinstance(c, dict):
            out.append(f"components[{i}] is not an object")
            continue
        name = c.get("name")
        for key in ("name", "role", "linkage", "licence", "approved", "status"):
            if key not in c:
                out.append(f"component {name or i!r} lacks {key!r}")
        if name in seen:
            out.append(f"duplicate component name {name!r}")
        seen.add(name)
        if c.get("linkage") not in rules:
            out.append(f"component {name!r} has unknown linkage {c.get('linkage')!r}")
        if licence_class(policy, str(c.get("licence", ""))) is None:
            out.append(f"component {name!r} has unclassified licence {c.get('licence')!r}")
        if not isinstance(c.get("approved"), bool):
            out.append(f"component {name!r}: approved must be boolean")
        for key in ("import_names", "distributions"):
            if key in c and not isinstance(c[key], list):
                out.append(f"component {name!r}: {key} must be a list")
    return out


def licence_class(policy: dict, licence: str) -> str | None:
    """The class a licence identifier belongs to (``None`` if unclassified)."""
    for cls, ids in policy.get("licence_classes", {}).items():
        if licence in ids:
            return cls
    return None


# --------------------------------------------------------------------------
# code scan
# --------------------------------------------------------------------------
def _is_stdlib(name: str) -> bool:
    return name in sys.stdlib_module_names or name in sys.builtin_module_names


def _local_packages(repo_root: str) -> set[str]:
    """Top-level packages/modules that belong to this repository."""
    out = set()
    try:
        for entry in os.listdir(repo_root):
            full = os.path.join(repo_root, entry)
            if os.path.isdir(full) and os.path.exists(os.path.join(full, "__init__.py")):
                out.add(entry)
            elif entry.endswith(".py"):
                out.add(entry[:-3])
    except OSError:
        pass
    return out | {"tests", "scripts"}


def _guarded_imports(tree: ast.AST) -> list[tuple[str, int, bool]]:
    """``(top_level_module, lineno, guarded)`` for every absolute import.

    ``guarded`` is True when the import cannot break module load: it sits
    inside a ``try`` whose handlers catch ImportError/ModuleNotFoundError (or
    a bare/broad except), or it is deferred into a function body (a lazy
    import that only runs when the cloud adapter is actually used).
    """
    found: list[tuple[str, int, bool]] = []

    def handler_guards(t: ast.Try) -> bool:
        for h in t.handlers:
            if h.type is None:
                return True
            names = []
            if isinstance(h.type, ast.Tuple):
                names = [getattr(e, "id", getattr(e, "attr", "")) for e in h.type.elts]
            else:
                names = [getattr(h.type, "id", getattr(h.type, "attr", ""))]
            if any(n in _IMPORT_ERRORS for n in names):
                return True
        return False

    def walk(node: ast.AST, guarded: bool) -> None:
        for child in ast.iter_child_nodes(node):
            g = guarded or isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef))
            if isinstance(child, ast.Try):
                g = guarded or handler_guards(child)
                # only the try-body is protected; handlers/else/finally are not
                for stmt in child.body:
                    walk_stmt(stmt, g)
                for h in child.handlers:
                    walk(h, guarded)
                for stmt in child.orelse + child.finalbody:
                    walk_stmt(stmt, guarded)
                continue
            walk_stmt(child, g)

    def walk_stmt(child: ast.AST, guarded: bool) -> None:
        if isinstance(child, ast.Import):
            for a in child.names:
                found.append((a.name.split(".")[0], child.lineno, guarded))
        elif isinstance(child, ast.ImportFrom):
            if child.level == 0 and child.module:
                found.append((child.module.split(".")[0], child.lineno, guarded))
        walk(child, guarded)

    walk(tree, False)
    return found


def scan_imports(repo_root: str, packages: list[str]) -> dict[str, list[dict]]:
    """Third-party top-level imports under ``packages`` → ``[{file, line, guarded}]``.

    Standard-library modules and this repository's own packages are excluded.
    Files that fail to parse are reported under the pseudo-module ``"<parse-error>"``.
    """
    local = _local_packages(repo_root)
    out: dict[str, list[dict]] = {}
    for pkg in packages:
        base = os.path.join(repo_root, pkg)
        if not os.path.exists(base):
            continue
        files = [base] if os.path.isfile(base) else []
        for root, dirs, names in os.walk(base):
            dirs[:] = sorted(d for d in dirs if d not in _SKIP_DIRS)
            files.extend(os.path.join(root, n) for n in sorted(names) if n.endswith(".py"))
        for path in files:
            rel = os.path.relpath(path, repo_root)
            try:
                with open(path, encoding="utf-8") as fh:
                    tree = ast.parse(fh.read(), filename=rel)
            except (SyntaxError, ValueError, OSError) as e:
                out.setdefault("<parse-error>", []).append({"file": rel, "line": 0, "guarded": False,
                                                            "error": str(e)})
                continue
            for mod, line, guarded in _guarded_imports(tree):
                if _is_stdlib(mod) or mod in local:
                    continue
                out.setdefault(mod, []).append({"file": rel, "line": line, "guarded": guarded})
    return {k: sorted(v, key=lambda d: (d["file"], d["line"])) for k, v in sorted(out.items())}


def scan_requirements(repo_root: str, files: list[str]) -> dict[str, str]:
    """Distribution names pinned in requirements files → source file."""
    out: dict[str, str] = {}
    for name in files:
        path = os.path.join(repo_root, name)
        if not os.path.isfile(path):
            continue
        with open(path, encoding="utf-8") as fh:
            text = fh.read()
        if name.endswith(".toml"):
            # A strict scan: pull ONLY the `dependencies = [...]` array and
            # every `<extra> = [...]` array under `[project.optional-dependencies]`.
            # Everything else in pyproject (project metadata, build-system,
            # tool tables) is ignored — otherwise the greedy regex used to
            # sweep up licence identifiers and heading strings as if they
            # were pip requirements.
            for dist in _pyproject_dists(text):
                if dist.lower() not in ("python",):
                    out.setdefault(_norm(dist), name)
            continue
        for line in text.splitlines():
            line = line.split("#", 1)[0].strip()
            if not line or line.startswith(("-", "git+", "http")):
                continue
            m = _REQ_LINE.match(line)
            if m:
                out.setdefault(_norm(m.group(1)), name)
    return out


def _norm(dist: str) -> str:
    return re.sub(r"[-_.]+", "-", dist).lower()


_TOML_ARR = re.compile(r"^\s*(?:dependencies|[A-Za-z0-9_-]+)\s*=\s*\[", re.M)
_TOML_TABLE = re.compile(r"^\s*\[([^\]]+)\]\s*$", re.M)


def _pyproject_dists(text: str) -> list[str]:
    """Return distribution names declared as project dependencies in a pyproject.toml.

    Reads the ``[project] dependencies`` array and every array under the
    ``[project.optional-dependencies]`` table. Every other table (build-system,
    tool.*) is ignored so the gate stops picking up licence identifiers or
    project metadata as if they were requirement pins.
    """
    dists: list[str] = []
    tables: list[tuple[str, int, int]] = []   # (table_name, start_line, end_line)
    lines = text.splitlines()
    # locate table boundaries
    heads: list[tuple[int, str]] = []
    for i, line in enumerate(lines):
        m = _TOML_TABLE.match(line)
        if m:
            heads.append((i, m.group(1).strip()))
    heads.append((len(lines), ""))     # sentinel
    for (start, name), (end, _) in zip(heads, heads[1:]):
        tables.append((name, start, end))

    def _extract_arrays(block: str) -> list[str]:
        """Every top-level array declaration in this table block."""
        out: list[str] = []
        i = 0
        while True:
            m = _TOML_ARR.search(block, i)
            if not m:
                break
            # Find the matching closing ']', accounting for nested brackets
            depth = 0
            j = m.end() - 1
            while j < len(block):
                ch = block[j]
                if ch == "[":
                    depth += 1
                elif ch == "]":
                    depth -= 1
                    if depth == 0:
                        break
                j += 1
            arr = block[m.end():j]
            for s in re.finditer(r'"([A-Za-z0-9][A-Za-z0-9._-]*)\s*(?:[<>=!~\[;].*?)?"', arr):
                out.append(s.group(1))
            i = j + 1
        return out

    # a bare [project] table can also declare `dependencies = [...]`
    for name, start, end in tables:
        block = "\n".join(lines[start:end])
        if name in ("project",):
            # match only `dependencies = [...]`
            m = re.search(r"^\s*dependencies\s*=\s*\[", block, re.M)
            if m:
                for s in re.finditer(r'"([A-Za-z0-9][A-Za-z0-9._-]*)\s*(?:[<>=!~\[;].*?)?"',
                                     block[m.end():]):
                    dists.append(s.group(1))
        elif name.startswith("project.optional-dependencies") or name == "project.optional-dependencies":
            dists.extend(_extract_arrays(block))
    return dists


# --------------------------------------------------------------------------
# evaluation
# --------------------------------------------------------------------------
def evaluate(manifest: dict, imports: dict[str, list[dict]] | None = None,
             requirements: dict[str, str] | None = None) -> dict:
    """Apply the policy; return ``{"ok", "violations", "warnings", "summary", "components"}``.

    ``imports``/``requirements`` are the outputs of ``scan_imports``/
    ``scan_requirements``; pass ``None`` to evaluate the manifest alone.
    """
    policy = manifest["policy"]
    rules = policy["linkage_rules"]
    violations: list[dict] = []
    warnings: list[dict] = []
    rows: list[dict] = []

    def viol(code: str, component: str | None, detail: str) -> None:
        violations.append({"code": code, "component": component, "detail": detail})

    by_import: dict[str, dict] = {}
    by_dist: dict[str, dict] = {}
    for c in manifest["components"]:
        name, linkage, licence = c["name"], c["linkage"], str(c["licence"])
        cls = licence_class(policy, licence)
        rejected = c.get("status") == "rejected"
        allowed = cls is not None and cls in rules.get(linkage, [])
        rows.append({"name": name, "role": c.get("role", ""), "linkage": linkage, "licence": licence,
                     "class": cls, "approved": bool(c["approved"]), "status": c.get("status", ""),
                     "allowed": allowed})
        for imp in c.get("import_names", []) or []:
            by_import[imp] = c
        for dist in c.get("distributions", []) or []:
            by_dist[_norm(dist)] = c
        if rejected:
            if c["approved"]:
                viol("rejected_but_approved", name, "status=rejected components must have approved=false")
            continue
        if not c["approved"]:
            viol("not_approved", name, f"component is not approved (status={c.get('status')!r})")
        if cls is None:
            viol("unknown_licence", name, f"licence {licence!r} is not in any licence class")
        elif not allowed:
            viol("linkage_denied", name,
                 f"licence {licence} ({cls}) is not allowed for linkage {linkage!r}; "
                 f"allowed classes: {', '.join(rules.get(linkage, []))}")

    for mod, sites in (imports or {}).items():
        where = ", ".join(f"{s['file']}:{s['line']}" for s in sites)
        if mod == "<parse-error>":
            viol("parse_error", None, "unparseable Python: " + where)
            continue
        comp = by_import.get(mod)
        if comp is None:
            viol("undeclared_import", mod, f"third-party import {mod!r} is not declared in the manifest ({where})")
            continue
        if comp.get("status") == "rejected" or not comp["approved"]:
            viol("rejected_import", comp["name"], f"import of rejected component {mod!r} ({where})")
            continue
        if comp["linkage"] == "optional-runtime":
            bare = [s for s in sites if not s["guarded"]]
            if bare:
                viol("unguarded_optional_import", comp["name"],
                     f"optional-runtime import {mod!r} must be inside try/except ImportError: "
                     + ", ".join(f"{s['file']}:{s['line']}" for s in bare))
        elif comp["linkage"] not in ("runtime",):
            viol("linkage_mismatch", comp["name"],
                 f"{mod!r} is imported by shipped code but declared as {comp['linkage']!r} ({where})")

    for dist, src in (requirements or {}).items():
        comp = by_dist.get(dist)
        if comp is None:
            viol("requirements_undeclared", dist, f"{src} pins {dist!r}, which is not in the manifest")
        elif comp.get("status") == "rejected" or not comp["approved"]:
            viol("requirements_rejected", comp["name"], f"{src} pins rejected component {dist!r}")

    linkage_counts: dict[str, int] = {}
    for r in rows:
        if r["status"] != "rejected":
            linkage_counts[r["linkage"]] = linkage_counts.get(r["linkage"], 0) + 1
    if not (imports or {}):
        warnings.append({"code": "no_third_party_imports",
                         "detail": "no third-party imports found in shipped code (stdlib-only)"})
    violations.sort(key=lambda v: (v["code"], str(v["component"]), v["detail"]))
    return {
        "ok": not violations,
        "violations": violations,
        "warnings": warnings,
        "summary": {"components": len(rows), "rejected": sum(1 for r in rows if r["status"] == "rejected"),
                    "by_linkage": dict(sorted(linkage_counts.items())),
                    "third_party_imports": sorted((imports or {}).keys()),
                    "requirements": sorted((requirements or {}).keys())},
        "components": rows,
    }


def run(repo_root: str, manifest_path: str) -> dict:
    """Load the manifest, scan the repo, evaluate — the CLI in one call."""
    manifest = load_manifest(manifest_path)
    scan = manifest["policy"].get("scan", {})
    imports = scan_imports(repo_root, scan.get("packages", ["knowledge_fabric"]))
    reqs = scan_requirements(repo_root, scan.get("requirements_files", ["requirements.txt"]))
    report = evaluate(manifest, imports, reqs)
    report["manifest"] = os.path.relpath(manifest_path, repo_root)
    report["repo"] = os.path.abspath(repo_root)
    return report


def render(report: dict) -> str:
    """Plain-text report (deterministic order)."""
    lines = [f"licence gate — manifest {report.get('manifest', '?')}", ""]
    width = max(len(r["name"]) for r in report["components"]) if report["components"] else 10
    lines.append(f"{'component':<{width}}  {'linkage':<16} {'licence':<22} {'class':<18} approved")
    for r in report["components"]:
        flag = "yes" if r["approved"] else ("rejected" if r["status"] == "rejected" else "NO")
        mark = "" if r["allowed"] or r["status"] == "rejected" else "  <-- DENIED"
        lines.append(f"{r['name']:<{width}}  {r['linkage']:<16} {r['licence']:<22} "
                     f"{str(r['class']):<18} {flag}{mark}")
    s = report["summary"]
    lines += ["", f"third-party imports in shipped code: "
                  f"{', '.join(s['third_party_imports']) or 'none (stdlib-only)'}"]
    if s["requirements"]:
        lines.append("requirements pinned: " + ", ".join(s["requirements"]))
    for w in report["warnings"]:
        lines.append(f"note: {w['detail']}")
    if report["violations"]:
        lines += ["", f"VIOLATIONS ({len(report['violations'])}):"]
        for v in report["violations"]:
            lines.append(f"  [{v['code']}] {v['component'] or '-'}: {v['detail']}")
        lines.append("RESULT: FAIL")
    else:
        lines += ["", "RESULT: PASS — every runtime dependency is permissively licensed (I14)"]
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="licence_gate", description=__doc__.strip().splitlines()[0])
    ap.add_argument("--manifest", default=None, help=f"manifest path (default: <repo>/{DEFAULT_MANIFEST})")
    ap.add_argument("--repo", default=None, help="repository root (default: this checkout)")
    ap.add_argument("--json", action="store_true", help="print the report as JSON")
    args = ap.parse_args(argv)
    repo = os.path.abspath(args.repo or os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    manifest = args.manifest or os.path.join(repo, DEFAULT_MANIFEST)
    try:
        report = run(repo, manifest)
    except ManifestError as e:
        print(f"licence gate: {e}", file=sys.stderr)
        return 2
    print(json.dumps(report, indent=2, sort_keys=True) if args.json else render(report))
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
