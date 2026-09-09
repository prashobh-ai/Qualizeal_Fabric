"""Deployment readiness doctor.

    python3 scripts/doctor.py --target local          # laptop / compose shape
    python3 scripts/doctor.py --target aws            # lists exactly what is missing for AWS
    python3 scripts/doctor.py --target aws --json     # machine-readable (same dict as /admin/doctor)
    python3 scripts/doctor.py --target local --skip-tests --no-live-health

Exit status: 0 when there are no blockers, 1 when the target is not ready,
2 on bad arguments. The report never contacts AWS; it inspects the repository,
the image build file, the IaC module and the process environment.
"""
from __future__ import annotations

import argparse
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("KF_MODEL_MODE", "mock")

from knowledge_fabric.ops import readiness   # noqa: E402


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="doctor", description=__doc__.strip().splitlines()[0])
    ap.add_argument("--target", choices=readiness.TARGETS, default="local")
    ap.add_argument("--json", action="store_true", help="print the report as JSON")
    ap.add_argument("--skip-tests", action="store_true", help="do not run the unittest suite")
    ap.add_argument("--no-live-health", action="store_true",
                    help="check the /health route statically instead of serving it on loopback")
    ap.add_argument("--repo", default=None, help="repository root (default: this checkout)")
    args = ap.parse_args(argv)

    rep = readiness.report(target=args.target, repo_root=args.repo, run_tests=not args.skip_tests,
                           live_health=not args.no_live_health)
    if args.json:
        print(json.dumps(rep, indent=2, sort_keys=True))
    else:
        print(readiness.render(rep))
    return 0 if rep["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
