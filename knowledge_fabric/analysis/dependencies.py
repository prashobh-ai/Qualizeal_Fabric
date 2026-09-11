"""Dependencies and licences (T39).

Parses every manifest a clone carries — ``requirements*.txt``,
``pyproject.toml``, ``Pipfile``, ``package.json``, ``go.mod``, ``pom.xml``,
``build.gradle(.kts)``, ``Gemfile``, ``Cargo.toml``, ``*.csproj`` — resolves
licences from the PyPI JSON API and the npm registry (cached in
``data/licence_cache.json``), and files each library under a category from
``licence_map.json`` ∈ ``permissive | copyleft | non_commercial | unknown``.

Row (``data/dependencies.json["owner/repo"]``)::

    {name, version, ecosystem, licence, category, manifest_path, licence_source}

Nothing here fabricates a licence: an unresolvable one is ``unknown`` with
``licence_source`` saying why (``unsupported ecosystem``, ``HTTP 404``,
``unreachable``). Transport is injectable (``transport(url, headers, timeout)
-> (status, bytes)``) so the resolver is testable offline.
"""

from __future__ import annotations

import json
import os
import re
import tomllib
import urllib.error
import urllib.request
import xml.etree.ElementTree as ET

from .. import fabric_data as fd

_MAP_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "licence_map.json")
MANIFEST_NAMES = (
    "pyproject.toml",
    "Pipfile",
    "package.json",
    "go.mod",
    "pom.xml",
    "build.gradle",
    "build.gradle.kts",
    "Gemfile",
    "Cargo.toml",
)
SKIP_DIRS = {".git", "node_modules", "vendor", "dist", "build", "__pycache__", ".venv", "venv"}
_PEP508 = re.compile(r"^\s*([A-Za-z0-9][A-Za-z0-9._-]*)\s*(\[[^\]]*\])?\s*([^;#]*)")
_VERSION_CHARS = re.compile(r"[=<>!~^]+\s*([^\s,;]+)")


# ---------------------------------------------------------------------------
# manifests
# ---------------------------------------------------------------------------
def find_manifests(clone_dir: str) -> list[str]:
    """Relative paths of every manifest under ``clone_dir`` (sorted)."""
    out: list[str] = []
    for root, dirs, files in os.walk(clone_dir):
        dirs[:] = sorted(d for d in dirs if d not in SKIP_DIRS and not d.startswith("."))
        for name in files:
            if (
                name in MANIFEST_NAMES
                or (name.startswith("requirements") and name.endswith(".txt"))
                or name.endswith(".csproj")
            ):
                out.append(
                    os.path.relpath(os.path.join(root, name), clone_dir).replace(os.sep, "/")
                )
    return sorted(out)


def _row(name: str, version: str, ecosystem: str, manifest: str) -> dict:
    return {
        "name": name.strip(),
        "version": (version or "").strip(),
        "ecosystem": ecosystem,
        "manifest_path": manifest,
    }


def _spec(text: str) -> tuple[str, str] | None:
    """``"fastapi[all]>=0.115; python_version>'3'"`` → ``("fastapi", ">=0.115")``."""
    m = _PEP508.match(text or "")
    if not m or not m.group(1):
        return None
    ver = (m.group(3) or "").strip()
    if ver.startswith("@"):
        ver = ver[1:].strip()
    return m.group(1), ver


def _requirements(text: str, manifest: str) -> list[dict]:
    rows = []
    for line in text.splitlines():
        s = line.strip()
        if not s or s.startswith(("#", "-", "--")) or s.startswith(("git+", "http")):
            continue
        spec = _spec(s.split("#", 1)[0])
        if spec:
            rows.append(_row(spec[0], spec[1], "pypi", manifest))
    return rows


def _pyproject(text: str, manifest: str) -> list[dict]:
    rows = []
    try:
        data = tomllib.loads(text)
    except (tomllib.TOMLDecodeError, ValueError):
        return rows
    proj = data.get("project") or {}
    for dep in proj.get("dependencies") or []:
        spec = _spec(str(dep))
        if spec:
            rows.append(_row(spec[0], spec[1], "pypi", manifest))
    for _extra, deps in (proj.get("optional-dependencies") or {}).items():
        for dep in deps or []:
            spec = _spec(str(dep))
            if spec:
                rows.append(_row(spec[0], spec[1], "pypi", manifest))
    poetry = ((data.get("tool") or {}).get("poetry") or {}).get("dependencies") or {}
    for name, ver in poetry.items():
        if name.lower() == "python":
            continue
        v = ver.get("version", "") if isinstance(ver, dict) else str(ver)
        rows.append(_row(name, v, "pypi", manifest))
    return rows


def _pipfile(text: str, manifest: str) -> list[dict]:
    rows = []
    try:
        data = tomllib.loads(text)
    except (tomllib.TOMLDecodeError, ValueError):
        return rows
    for section in ("packages", "dev-packages"):
        for name, ver in (data.get(section) or {}).items():
            v = ver.get("version", "") if isinstance(ver, dict) else str(ver)
            rows.append(_row(name, "" if v == "*" else v, "pypi", manifest))
    return rows


def _package_json(text: str, manifest: str) -> list[dict]:
    rows = []
    try:
        data = json.loads(text)
    except ValueError:
        return rows
    for section in ("dependencies", "devDependencies", "peerDependencies"):
        for name, ver in (data.get(section) or {}).items():
            rows.append(_row(name, str(ver), "npm", manifest))
    return rows


def _go_mod(text: str, manifest: str) -> list[dict]:
    rows = []
    in_block = False
    for line in text.splitlines():
        s = line.split("//", 1)[0].strip()
        if s.startswith("require ("):
            in_block = True
            continue
        if in_block and s == ")":
            in_block = False
            continue
        if s.startswith("require "):
            s = s[len("require ") :]
        elif not in_block:
            continue
        parts = s.split()
        if len(parts) >= 2 and "/" in parts[0]:
            rows.append(_row(parts[0], parts[1], "go", manifest))
    return rows


def _pom(text: str, manifest: str) -> list[dict]:
    rows = []
    try:
        root = ET.fromstring(text)
    except ET.ParseError:
        return rows
    for dep in root.iter():
        if dep.tag.split("}")[-1] != "dependency":
            continue
        vals = {c.tag.split("}")[-1]: (c.text or "").strip() for c in dep}
        if vals.get("artifactId"):
            name = f"{vals.get('groupId', '')}:{vals['artifactId']}".strip(":")
            rows.append(_row(name, vals.get("version", ""), "maven", manifest))
    return rows


_GRADLE = re.compile(
    r"(?:implementation|api|compile|testImplementation|runtimeOnly|compileOnly)"
    r"\s*\(?\s*['\"]([^'\"]+)['\"]"
)


def _gradle(text: str, manifest: str) -> list[dict]:
    rows = []
    for m in _GRADLE.finditer(text):
        coords = m.group(1).split(":")
        if len(coords) >= 2:
            rows.append(
                _row(":".join(coords[:2]), coords[2] if len(coords) > 2 else "", "maven", manifest)
            )
    return rows


_GEM = re.compile(r"""^\s*gem\s+['"]([^'"]+)['"]\s*(?:,\s*['"]([^'"]+)['"])?""", re.M)


def _gemfile(text: str, manifest: str) -> list[dict]:
    return [_row(m.group(1), m.group(2) or "", "rubygems", manifest) for m in _GEM.finditer(text)]


def _cargo(text: str, manifest: str) -> list[dict]:
    rows = []
    try:
        data = tomllib.loads(text)
    except (tomllib.TOMLDecodeError, ValueError):
        return rows
    for section in ("dependencies", "dev-dependencies", "build-dependencies"):
        for name, ver in (data.get(section) or {}).items():
            v = ver.get("version", "") if isinstance(ver, dict) else str(ver)
            rows.append(_row(name, v, "cargo", manifest))
    return rows


_CSPROJ = re.compile(r"<PackageReference\s+([^>]*?)/?>", re.I)
_ATTR = re.compile(r"(\w+)\s*=\s*\"([^\"]*)\"")


def _csproj(text: str, manifest: str) -> list[dict]:
    rows = []
    for m in _CSPROJ.finditer(text):
        attrs = dict(_ATTR.findall(m.group(1)))
        if attrs.get("Include"):
            rows.append(_row(attrs["Include"], attrs.get("Version", ""), "nuget", manifest))
    if not rows:
        try:
            root = ET.fromstring(text)
            for el in root.iter():
                if el.tag.split("}")[-1] == "PackageReference" and el.get("Include"):
                    rows.append(_row(el.get("Include"), el.get("Version", ""), "nuget", manifest))
        except ET.ParseError:
            pass
    return rows


def parse_manifest(rel_path: str, text: str) -> list[dict]:
    """Dependency rows for one manifest (empty for an unknown or broken file)."""
    name = os.path.basename(rel_path)
    if name.startswith("requirements") and name.endswith(".txt"):
        return _requirements(text, rel_path)
    if name == "pyproject.toml":
        return _pyproject(text, rel_path)
    if name == "Pipfile":
        return _pipfile(text, rel_path)
    if name == "package.json":
        return _package_json(text, rel_path)
    if name == "go.mod":
        return _go_mod(text, rel_path)
    if name == "pom.xml":
        return _pom(text, rel_path)
    if name in ("build.gradle", "build.gradle.kts"):
        return _gradle(text, rel_path)
    if name == "Gemfile":
        return _gemfile(text, rel_path)
    if name == "Cargo.toml":
        return _cargo(text, rel_path)
    if name.endswith(".csproj"):
        return _csproj(text, rel_path)
    return []


# ---------------------------------------------------------------------------
# licences
# ---------------------------------------------------------------------------
def http_transport(url: str, headers: dict, timeout: int = 20) -> tuple[int, bytes]:
    req = urllib.request.Request(url, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.status, resp.read()
    except urllib.error.HTTPError as e:
        return e.code, e.read()
    except urllib.error.URLError as e:
        raise ConnectionError(str(e.reason)) from e


def _pypi_licence(data: dict) -> str:
    info = data.get("info") or {}
    expr = (info.get("license_expression") or "").strip()
    if expr:
        return expr
    for c in info.get("classifiers") or []:
        if c.startswith("License :: OSI Approved ::"):
            return c.split("::")[-1].strip()
        if c.startswith("License ::") and "OSI" not in c:
            tail = c.split("::")[-1].strip()
            if tail and tail != "Other/Proprietary License":
                return tail
    lic = (info.get("license") or "").strip()
    return lic.splitlines()[0][:80] if lic else ""


def _npm_licence(data: dict) -> str:
    lic = data.get("license")
    if isinstance(lic, dict):
        lic = lic.get("type")
    if not lic:
        latest = (data.get("dist-tags") or {}).get("latest")
        v = (data.get("versions") or {}).get(latest or "", {})
        lic = v.get("license")
        if isinstance(lic, dict):
            lic = lic.get("type")
    if not lic and isinstance(data.get("licenses"), list) and data["licenses"]:
        first = data["licenses"][0]
        lic = first.get("type") if isinstance(first, dict) else str(first)
    return str(lic or "").strip()


def resolve_licence(
    name: str, ecosystem: str, transport=None, cache: dict | None = None
) -> tuple[str, str]:
    """``(licence, source)`` from the registry (or the cache). Unsupported
    ecosystems and failures give ``("unknown", <why>)`` — never a guess."""
    key = f"{ecosystem}:{name.lower()}"
    if cache is not None and key in cache and cache[key].get("licence"):
        return cache[key]["licence"], "cache"
    transport = transport or http_transport
    if ecosystem == "pypi":
        url = f"https://pypi.org/pypi/{name}/json"
    elif ecosystem == "npm":
        url = "https://registry.npmjs.org/" + name.replace("/", "%2F")
    else:
        return "unknown", "unsupported ecosystem"
    try:
        status, body = transport(url, {"Accept": "application/json"}, 20)
    except (ConnectionError, OSError) as e:
        return "unknown", f"unreachable: {str(e)[:80]}"
    if status != 200:
        return "unknown", f"HTTP {status}"
    try:
        data = json.loads(body.decode("utf-8", "replace"))
    except ValueError:
        return "unknown", "invalid registry response"
    lic = _pypi_licence(data) if ecosystem == "pypi" else _npm_licence(data)
    lic = lic or "unknown"
    if cache is not None:
        cache[key] = {"licence": lic, "source": "pypi" if ecosystem == "pypi" else "npm"}
    return lic, ("pypi" if ecosystem == "pypi" else "npm")


def licence_map() -> dict:
    return fd.read_json(_MAP_PATH, {}) or {}


def categorise(licence: str, mapping: dict | None = None) -> str:
    """``permissive | copyleft | non_commercial | unknown`` for a licence string."""
    mapping = mapping or licence_map()
    text = (licence or "").strip()
    if not text or text.lower() == "unknown":
        return mapping.get("default", "unknown")
    for category, patterns in (mapping.get("categories") or {}).items():
        for pat in patterns:
            try:
                if re.search(pat, text, re.I):
                    return category
            except re.error:
                continue
    return mapping.get("default", "unknown")


def load_cache() -> dict:
    return fd.read_json(fd.data_path("licence_cache.json"), {}) or {}


def save_cache(cache: dict) -> str:
    return fd.write_json(fd.data_path("licence_cache.json", mkdir=True), cache)


# ---------------------------------------------------------------------------
# the run
# ---------------------------------------------------------------------------
def run(repo: str, clone_dir: str, transport=None, write: bool = True) -> list[dict]:
    """Every dependency of ``repo`` with its licence and category; writes
    ``data/dependencies.json[repo]`` and updates the licence cache."""
    rows: list[dict] = []
    seen: set[tuple[str, str]] = set()
    for rel in find_manifests(clone_dir):
        try:
            with open(os.path.join(clone_dir, rel), encoding="utf-8", errors="replace") as f:
                text = f.read()
        except OSError:
            continue
        for r in parse_manifest(rel, text):
            key = (r["ecosystem"], r["name"].lower())
            if key in seen:
                continue
            seen.add(key)
            rows.append(r)
    cache = load_cache()
    mapping = licence_map()
    for r in rows:
        lic, source = resolve_licence(r["name"], r["ecosystem"], transport=transport, cache=cache)
        r["licence"] = lic
        r["licence_source"] = source
        r["category"] = categorise(lic, mapping)
    rows.sort(key=lambda r: (r["ecosystem"], r["name"].lower()))
    if write:
        save_cache(cache)
        p = fd.data_path("dependencies.json", mkdir=True)
        all_deps = fd.read_json(p, {}) or {}
        all_deps[repo] = rows
        fd.write_json(p, all_deps)
    return rows


def summary(rows: list[dict]) -> dict:
    """Counts by category and by ecosystem — the card's dependency line."""
    by_cat: dict[str, int] = {}
    by_eco: dict[str, int] = {}
    for r in rows:
        by_cat[r.get("category", "unknown")] = by_cat.get(r.get("category", "unknown"), 0) + 1
        by_eco[r.get("ecosystem", "")] = by_eco.get(r.get("ecosystem", ""), 0) + 1
    return {"total": len(rows), "by_category": by_cat, "by_ecosystem": by_eco}
