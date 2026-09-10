"""Third-party notices generator (section 18 / T01).

    python3 scripts/notices.py                       # write THIRD_PARTY_NOTICES.md
    python3 scripts/notices.py --check               # exit 1 if the file is stale
    python3 scripts/notices.py --manifest ci/licence_manifest.json --out THIRD_PARTY_NOTICES.md

Reads the single source of truth — ``ci/licence_manifest.json`` — and writes the
attribution file that ships at the repository root. Only *approved* components
that are not rejected alternatives are listed; each row carries the name, the
declared licence, the linkage kind and the one-line reason it is present.

This file and the manifest are the ONLY places licence data lives. Neither is
served through the API or rendered in any console (13.10): notices are a
repository artefact, not a product surface. Pure standard library; the output
is deterministic (components sorted by linkage then name) so ``--check`` is a
stable CI guard.

Exit status: 0 ok · 1 stale (``--check`` only) · 2 manifest missing/invalid.
"""

from __future__ import annotations

import argparse
import json
import os
import sys

DEFAULT_MANIFEST = os.path.join("ci", "licence_manifest.json")
DEFAULT_OUT = "THIRD_PARTY_NOTICES.md"

# Linkage kinds in the order they appear in the notices, with a human heading.
_LINKAGE_ORDER = [
    ("runtime", "Runtime — linked into the shipped process"),
    ("optional-runtime", "Optional runtime — guarded imports, selected by environment"),
    ("model-weights", "Model weights — downloaded artefacts"),
    ("tooling", "Tooling — run at build or CI time, never shipped"),
    ("external-service", "External services — reached over the network, code never linked"),
    ("managed-service", "Managed services — cloud services behind an open protocol"),
    ("brand-asset", "Brand assets — first-party"),
]


def load(path: str) -> dict:
    """Read the manifest JSON; raise ``SystemExit(2)`` if missing or invalid."""
    try:
        with open(path, encoding="utf-8") as fh:
            return json.load(fh)
    except FileNotFoundError:
        sys.stderr.write(f"manifest not found: {path}\n")
        raise SystemExit(2) from None
    except ValueError as e:
        sys.stderr.write(f"manifest is not valid JSON: {e}\n")
        raise SystemExit(2) from None


def render(manifest: dict) -> str:
    """Return the full ``THIRD_PARTY_NOTICES.md`` text for a manifest."""
    title = manifest.get("title", "QualiZeal Knowledge Fabric")
    approved = [
        c
        for c in manifest.get("components", [])
        if c.get("approved") and c.get("status") != "rejected"
    ]
    by_linkage: dict[str, list[dict]] = {}
    for c in approved:
        by_linkage.setdefault(c.get("linkage", "other"), []).append(c)

    lines: list[str] = [
        f"# Third-party notices — {title}",
        "",
        "This product includes third-party software and services listed below. It is",
        "generated from `ci/licence_manifest.json` by `scripts/notices.py`; edit the",
        "manifest, then run `make notices`. Every component is used under its own",
        "licence, named against each entry.",
        "",
    ]
    for linkage, heading in _LINKAGE_ORDER:
        group = sorted(by_linkage.get(linkage, []), key=lambda c: c["name"].lower())
        if not group:
            continue
        lines.append(f"## {heading}")
        lines.append("")
        lines.append("| Component | Licence | Role |")
        lines.append("|---|---|---|")
        for c in group:
            licence = c.get("licence_name") or c.get("licence", "")
            role = (c.get("role") or "").replace("|", "\\|")
            lines.append(f"| {c['name']} | {licence} | {role} |")
        lines.append("")

    # Any linkage kind not in the fixed order (defensive; keeps every approved row).
    listed = {k for k, _ in _LINKAGE_ORDER}
    rest = sorted(
        (c for c in approved if c.get("linkage", "other") not in listed),
        key=lambda c: (c.get("linkage", "other"), c["name"].lower()),
    )
    if rest:
        lines.append("## Other")
        lines.append("")
        lines.append("| Component | Licence | Linkage | Role |")
        lines.append("|---|---|---|---|")
        for c in rest:
            licence = c.get("licence_name") or c.get("licence", "")
            role = (c.get("role") or "").replace("|", "\\|")
            lines.append(f"| {c['name']} | {licence} | {c.get('linkage', '')} | {role} |")
        lines.append("")

    return "\n".join(lines).rstrip() + "\n"


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Generate THIRD_PARTY_NOTICES.md from the manifest.")
    ap.add_argument("--manifest", default=DEFAULT_MANIFEST)
    ap.add_argument("--out", default=DEFAULT_OUT)
    ap.add_argument(
        "--check",
        action="store_true",
        help="do not write; exit 1 if the file on disk is missing or out of date",
    )
    args = ap.parse_args(argv)

    text = render(load(args.manifest))
    if args.check:
        try:
            with open(args.out, encoding="utf-8") as fh:
                current = fh.read()
        except FileNotFoundError:
            current = None
        if current != text:
            sys.stderr.write(f"{args.out} is out of date; run: make notices\n")
            return 1
        return 0

    with open(args.out, "w", encoding="utf-8") as fh:
        fh.write(text)
    sys.stdout.write(f"wrote {args.out}\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
