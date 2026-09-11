"""Repository card (T38) — a deterministic document built from facts and the
clone, never from a model: purpose line, languages, top-level directories,
entry points, dependency count, tests, CI, container, infrastructure,
contributors, commits, pull requests, deployments, last push.

``write`` stores ``analysis/<repo>/card.md``; ``ingest`` files it through the
real pipeline as ``<repo> — repository card`` (``source_kind: analysis``,
authority 65) so the card is searchable and citeable like any document.
"""

from __future__ import annotations

import os
import re

from .. import fabric_data as fd

SKIP_DIRS = {".git", "node_modules", "__pycache__", ".venv", "venv", "dist", "build"}
ENTRY_FILES = (
    "main.py",
    "app.py",
    "manage.py",
    "cli.py",
    "__main__.py",
    "index.js",
    "server.js",
    "main.go",
    "Program.cs",
    "Main.java",
    "wsgi.py",
    "asgi.py",
)
INFRA_HINTS = ("terraform", "infra", "k8s", "kubernetes", "helm", "deploy", "iac")
AUTHORITY = 65


def _n(v) -> str:
    try:
        return f"{int(v):,}"
    except (TypeError, ValueError):
        return str(v or 0)


def _top_dirs(clone_dir: str) -> list[str]:
    try:
        names = sorted(os.listdir(clone_dir))
    except OSError:
        return []
    return [n for n in names if os.path.isdir(os.path.join(clone_dir, n)) and n not in SKIP_DIRS]


def _entry_points(clone_dir: str) -> list[str]:
    found: list[str] = []
    for root, dirs, files in os.walk(clone_dir):
        dirs[:] = [d for d in dirs if d not in SKIP_DIRS and not d.startswith(".")]
        depth = os.path.relpath(root, clone_dir).count(os.sep)
        if depth > 2:
            dirs[:] = []
            continue
        for f in files:
            if f in ENTRY_FILES:
                found.append(os.path.relpath(os.path.join(root, f), clone_dir).replace(os.sep, "/"))
    for name in ("Makefile", "package.json", "Dockerfile", "pyproject.toml"):
        p = os.path.join(clone_dir, name)
        if os.path.exists(p):
            found.append(name)
    return sorted(set(found))[:12]


def _count_tests(clone_dir: str) -> tuple[list[str], int]:
    dirs_found: list[str] = []
    n = 0
    for root, dirs, files in os.walk(clone_dir):
        dirs[:] = [d for d in dirs if d not in SKIP_DIRS and not d.startswith(".")]
        rel = os.path.relpath(root, clone_dir)
        base = os.path.basename(root)
        if base in ("tests", "test", "__tests__", "spec") and rel not in dirs_found:
            dirs_found.append(rel.replace(os.sep, "/"))
        for f in files:
            if re.match(
                r"(test_.*\.py|.*_test\.(py|go)|.*\.(test|spec)\.[jt]sx?|.*Tests?\.(cs|java))$", f
            ):
                n += 1
    return dirs_found[:6], n


def _exists(clone_dir: str, *names: str) -> list[str]:
    return [n for n in names if os.path.exists(os.path.join(clone_dir, n))]


def _ci(clone_dir: str) -> list[str]:
    wf = os.path.join(clone_dir, ".github", "workflows")
    out = []
    if os.path.isdir(wf):
        out += [
            f".github/workflows/{f}"
            for f in sorted(os.listdir(wf))
            if f.endswith((".yml", ".yaml"))
        ]
    out += _exists(
        clone_dir, ".gitlab-ci.yml", "Jenkinsfile", "azure-pipelines.yml", ".circleci/config.yml"
    )
    return out[:10]


def _infra(clone_dir: str) -> list[str]:
    out = []
    for name in sorted(_top_dirs(clone_dir)):
        if any(h in name.lower() for h in INFRA_HINTS):
            out.append(name + "/")
    for root, dirs, files in os.walk(clone_dir):
        dirs[:] = [d for d in dirs if d not in SKIP_DIRS and not d.startswith(".")]
        if os.path.relpath(root, clone_dir).count(os.sep) > 2:
            dirs[:] = []
            continue
        for f in files:
            if f.endswith(".tf") or f in ("Chart.yaml", "kustomization.yaml"):
                out.append(os.path.relpath(os.path.join(root, f), clone_dir).replace(os.sep, "/"))
    return sorted(set(out))[:10]


def build(
    repo: str,
    facts: dict | None,
    clone_dir: str,
    *,
    symbols: list[dict] | None = None,
    dependencies: list[dict] | None = None,
    capabilities: list[dict] | None = None,
) -> str:
    """The card markdown. Every line is a fact from ``facts`` or the clone."""
    f = facts or {}
    langs = f.get("languages") or {}
    lang_line = ", ".join(
        f"{k} ({round(float((v or {}).get('share') or 0) * 100)}%)"
        for k, v in sorted(langs.items(), key=lambda kv: -float((kv[1] or {}).get("share") or 0))
    )
    contributors = f.get("contributors") or []
    top = ", ".join(f"{c.get('login')} ({_n(c.get('contributions'))})" for c in contributors[:5])
    prs = f.get("pull_requests") or {}
    dep = f.get("deployments") or {}
    tests_dirs, n_tests = _count_tests(clone_dir)
    deps = dependencies or []
    caps = sorted({c.get("capability", "") for c in (capabilities or []) if c.get("capability")})
    syms = symbols or []
    containers = _exists(
        clone_dir, "Dockerfile", "docker-compose.yml", "compose.yaml", "compose.yml"
    )
    lines = [
        f"# {repo} — repository card",
        "",
        f"**Purpose.** {f.get('description') or 'No description recorded.'}"
        + (f" Homepage: {f['homepage']}." if f.get("homepage") else ""),
        "",
        f"- **Languages:** {lang_line or 'not recorded'}"
        + (f" (primary {f['primary_language']})" if f.get("primary_language") else ""),
        f"- **Top-level directories:** {', '.join(_top_dirs(clone_dir)) or 'none'}",
        f"- **Entry points:** {', '.join(_entry_points(clone_dir)) or 'none found'}",
        f"- **Symbols analysed:** {_n(len(syms))}",
        f"- **Dependencies:** {_n(len(deps))} declared"
        + (
            " ("
            + ", ".join(
                f"{k} {v}"
                for k, v in sorted(_count_by(deps, "category").items(), key=lambda kv: -kv[1])
            )
            + ")"
            if deps
            else ""
        ),
        f"- **Capabilities:** {', '.join(caps) or 'none detected'}",
        f"- **Tests:** {_n(n_tests)} test files"
        + (f" in {', '.join(tests_dirs)}" if tests_dirs else " (no tests directory)"),
        f"- **CI:** {', '.join(_ci(clone_dir)) or 'none'}",
        f"- **Container:** {', '.join(containers) or 'none'}",
        f"- **Infrastructure:** {', '.join(_infra(clone_dir)) or 'none'}",
        f"- **Contributors:** {_n(f.get('contributors_count') or len(contributors))}"
        + (f" — top: {top}" if top else ""),
        f"- **Commits:** {_n((f.get('commits') or {}).get('total'))} on "
        f"{f.get('default_branch') or 'main'}",
        f"- **Pull requests:** {_n(prs.get('total'))} total — {_n(prs.get('merged'))} merged, "
        f"{_n(prs.get('open'))} open, {_n(prs.get('closed'))} closed",
        f"- **Deployments:** {_n(dep.get('count'))}"
        + (
            f" across {', '.join(dep.get('environments') or [])}" if dep.get("environments") else ""
        ),
        f"- **Releases:** {_n(len(f.get('releases') or []))}"
        + (f" (latest {f['releases'][0].get('tag')})" if f.get("releases") else ""),
        "- **Workflows:** "
        + (
            ", ".join(
                f"{w.get('name')} ({w.get('last_conclusion') or '—'})"
                for w in f.get("workflows") or []
            )
            or "none"
        ),
        f"- **Visibility:** {f.get('visibility') or 'unknown'}"
        + (" · archived" if f.get("archived") else "")
        + (f" · licence {f['license_spdx']}" if f.get("license_spdx") else ""),
        f"- **Last push:** {f.get('pushed_at') or 'unknown'} "
        f"(facts as of {f.get('as_of') or 'unknown'})",
        "",
    ]
    return "\n".join(lines)


def _count_by(rows: list[dict], key: str) -> dict[str, int]:
    out: dict[str, int] = {}
    for r in rows:
        k = str(r.get(key) or "unknown")
        out[k] = out.get(k, 0) + 1
    return out


def write(repo: str, card_md: str) -> str:
    p = fd.path("analysis", fd.repo_slug(repo), "card.md")
    os.makedirs(os.path.dirname(p), exist_ok=True)
    with open(p, "w", encoding="utf-8") as f:
        f.write(card_md)
    return p


def ingest(
    platform,
    tenant: str,
    repo: str,
    card_md: str,
    *,
    acl: list[str] | None = None,
    kind: str = "card",
    title: str | None = None,
) -> dict:
    """File ``card_md`` through the real pipeline as an analysis document
    (``analysis://<repo>/<kind>``). Idempotent by content hash."""
    from ..ingestion.intake import IngestWorker, Intake

    intake, worker = Intake(platform), IngestWorker(platform, None)
    worker.intake = intake
    raw = intake.canonical(
        tenant,
        "analysis",
        f"analysis://{repo}/{kind}",
        title or f"{repo} — repository {kind}",
        card_md.encode("utf-8"),
        mime="text/markdown",
        acl=list(acl or ["public"]),
    )
    raw.meta.update(
        {
            "source_kind": "analysis",
            "citation_url": f"https://github.com/{repo}",
            "authority": AUTHORITY,
            "repo": repo,
        }
    )
    intake.submit(raw)
    results = worker.drain()
    return results[0] if results else {"status": "unknown"}
