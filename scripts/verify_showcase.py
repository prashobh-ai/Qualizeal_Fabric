"""Verify the built showcase before Pages deploys it (F0.2).

Fails the CI job if:
  * an `http://` or `https://` reference appears in any served HTML;
  * `.nojekyll` is missing at the artifact root;
  * `index.html` is missing.

F8.1 will extend this to check every `data/*.json` file the StaticAdapter
reads is present.
"""

from __future__ import annotations

import argparse
import os
import re
import sys

_EXTERNAL = re.compile(r'\b(?:src|href)\s*=\s*["\'](https?://[^"\']+)', re.I)


REQUIRED = [".nojekyll", "index.html", "engine.js", "snapshot.json"]
SURFACES = ["workspace", "admin", "curator", "signin", "dashboard"]


def verify(directory: str) -> list[str]:
    errors: list[str] = []
    d = os.path.abspath(directory)
    if not os.path.isdir(d):
        return [f"{d} does not exist"]
    for req in REQUIRED:
        if not os.path.exists(os.path.join(d, req)):
            errors.append(f"{req} missing at site root")
    for name in SURFACES:
        if not os.path.isfile(os.path.join(d, name, "index.html")):
            errors.append(f"{name}/index.html missing")
    # the snapshot must carry the sign-in directory and at least one answer
    snap_path = os.path.join(d, "snapshot.json")
    if os.path.isfile(snap_path):
        import json

        try:
            snap = json.load(open(snap_path, encoding="utf-8"))
            if not snap.get("login"):
                errors.append("snapshot.json has no login directory")
            if not snap.get("answers"):
                errors.append("snapshot.json has no baked answers")
        except Exception as e:  # noqa: BLE001
            errors.append(f"snapshot.json is not valid JSON: {e}")
    for root, _dirs, names in os.walk(d):
        for name in names:
            if not name.lower().endswith((".html", ".htm")):
                continue
            path = os.path.join(root, name)
            with open(path, encoding="utf-8") as fh:
                text = fh.read()
            for m in _EXTERNAL.finditer(text):
                errors.append(f"{os.path.relpath(path, d)}: external URL {m.group(1)}")
    return errors


# --------------------------------------------------------------------------
# T124b — post-ingest assertion: each source actually produced documents/facts
# in fabric-data. A source whose preflight passed but whose ingest produced
# nothing fails as a CONNECTOR bug, not a credential one, so the two are never
# confused.
# --------------------------------------------------------------------------
def verify_fabric_sources(
    facts: dict, sources: list[dict], preflight: dict | None = None
) -> list[str]:
    errors: list[str] = []
    by_area = (facts.get("documents") or {}).get("by_area") or {}
    pf_ok = {}
    for r in (preflight or {}).get("results", []):
        pf_ok[r.get("key")] = r.get("ok") and not r.get("skipped")

    def _passed_preflight(key: str) -> bool:
        return pf_ok.get(key, False)

    for src in sources:
        key, kind = src["key"], src["kind"]
        if kind == "website":
            n = int(by_area.get("website", 0))
            if n < 20:
                errors.append(_zero_or_low(key, n, 20, _passed_preflight(key), "documents"))
        elif kind == "github":
            repos = facts.get("repositories") or {}
            gh_docs = int(by_area.get("github", 0))
            if not repos:
                errors.append(_zero_or_low(key, 0, 1, _passed_preflight(key), "repositories"))
            elif gh_docs < 1:
                errors.append(
                    f"{key}: {len(repos)} repo(s) but 0 code/README docs chunked — connector bug"
                )
        elif kind == "jira":
            boards = facts.get("jira_boards") or {}
            dashboards = facts.get("jira_dashboards") or {}
            projects = facts.get("jira_projects") or {}
            for bid in src.get("boards", []):
                if str(bid) not in boards:
                    errors.append(
                        _zero_or_low(
                            f"{key}/board-{bid}", 0, 1, _passed_preflight(key), "board facts"
                        )
                    )
            # a board tracks a project; its by_status census must be non-empty
            if not any(
                (projects.get(p, {}).get("issues", {}) or {}).get("by_status")
                for p in src.get("projects", [])
            ):
                errors.append(f"{key}: board present but by_status census empty — connector bug")
            for did in src.get("dashboards", []):
                d = dashboards.get(str(did)) or {}
                if not (d.get("name") or d.get("gadgets")):
                    errors.append(
                        _zero_or_low(
                            f"{key}/dashboard-{did}", 0, 1, _passed_preflight(key), "dashboard fact"
                        )
                    )
        elif kind == "confluence":
            n = int(by_area.get("confluence", 0))
            if n < 1:
                errors.append(_zero_or_low(key, n, 1, _passed_preflight(key), "page documents"))
    return errors


def _zero_or_low(key: str, got: int, need: int, preflight_ok: bool, unit: str) -> str:
    if got == 0 and preflight_ok:
        return f"{key}: preflight OK but 0 {unit} — connector bug, not credentials"
    return f"{key}: only {got} {unit} (need ≥ {need})"


def _load_json(path: str, default):
    import json

    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return default


def run_fabric_check() -> int:
    """Assert each configured source produced documents/facts in fabric-data."""
    from knowledge_fabric import facts as factsmod

    root = os.path.abspath(os.path.dirname(os.path.dirname(__file__)))
    sources = _load_json(os.path.join(root, "data", "showcase_sources.json"), {}).get("sources", [])
    preflight = _load_json(os.path.join(root, "data", "preflight.json"), None)
    errors = verify_fabric_sources(factsmod.load_facts(), sources, preflight)
    if errors:
        for e in errors:
            print(f"FAIL: {e}", file=sys.stderr)
        return 1
    print(f"fabric sources verified ({len(sources)} sources have real documents)")
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="verify_showcase")
    ap.add_argument("--dir", default="dist/showcase", help="showcase directory")
    ap.add_argument(
        "--fabric",
        action="store_true",
        help="T124b — assert each source produced documents/facts in fabric-data (post-ingest)",
    )
    args = ap.parse_args(argv)
    if args.fabric:
        return run_fabric_check()
    errors = verify(args.dir)
    if errors:
        for e in errors:
            print(f"FAIL: {e}", file=sys.stderr)
        return 1
    print(f"showcase verified ({args.dir})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
