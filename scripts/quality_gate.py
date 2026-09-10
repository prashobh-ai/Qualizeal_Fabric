"""Answer-quality gate (T28) — the CI/local guard for answering behaviours.

    python3 scripts/quality_gate.py            # human report, exit 0 = all golden cases hold
    python3 scripts/quality_gate.py --json     # machine-readable result

Runs the golden suite in ``knowledge_fabric/evaluation/quality.py`` against a
fresh, self-contained fabric with the model disabled (the deployed extractive
path), and exits non-zero if ANY curated case regresses — so a drop in
grounding, citations, decline behaviour, code answers, discovery, follow-up
context, or persona conditioning fails the build.
"""

from __future__ import annotations

import json
import os
import sys

ROOT = os.path.abspath(os.path.dirname(os.path.dirname(__file__)))
sys.path.insert(0, ROOT)
os.environ.setdefault("KF_MODEL_MODE", "off")  # evaluate the true, model-free floor

from knowledge_fabric.evaluation import quality  # noqa: E402


def main(argv: list[str]) -> int:
    as_json = "--json" in argv
    result = quality.run_quality_suite()
    if as_json:
        print(
            json.dumps(
                {
                    "passed": result.passed,
                    "score": result.score,
                    "passing": result.passing,
                    "total": result.total,
                    "dimensions": result.dimensions,
                    "failures": result.failures,
                },
                indent=2,
            )
        )
    else:
        print(quality.format_report(result))
    return 0 if result.passed else 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
