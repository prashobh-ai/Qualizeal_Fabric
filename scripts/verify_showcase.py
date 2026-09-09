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


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="verify_showcase")
    ap.add_argument("--dir", default="dist/showcase", help="showcase directory")
    args = ap.parse_args(argv)
    errors = verify(args.dir)
    if errors:
        for e in errors:
            print(f"FAIL: {e}", file=sys.stderr)
        return 1
    print(f"showcase verified ({args.dir})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
