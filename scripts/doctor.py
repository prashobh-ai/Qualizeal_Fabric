"""Deployment readiness doctor.

    python3 scripts/doctor.py --target local          # laptop / compose shape
    python3 scripts/doctor.py --target aws            # lists exactly what is missing for AWS
    python3 scripts/doctor.py --target aws --json     # machine-readable (same as /admin/doctor)
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

from knowledge_fabric.ops import readiness  # noqa: E402


def check_model_ids(json_only: bool = False) -> dict:
    """F0.3 — validate the configured model ids against each provider's
    model-list endpoint. Runs only for providers whose credential is set;
    reports ``skipped`` for the rest so stale defaults are caught before a
    demo without ever leaking a key or forcing a network call in CI.
    """
    import json as _json
    import urllib.request

    small = os.environ.get("KF_MODEL_SMALL", "claude-haiku-4-5")
    large = os.environ.get("KF_MODEL_LARGE", "claude-opus-5")
    out: dict = {"small": small, "large": large, "providers": {}}

    # Anthropic — GET /v1/models with the API key.
    key = os.environ.get("ANTHROPIC_API_KEY", "").strip()
    if not key:
        out["providers"]["anthropic"] = {"status": "skipped", "reason": "ANTHROPIC_API_KEY not set"}
    else:
        base = os.environ.get("ANTHROPIC_BASE_URL", "https://api.anthropic.com").rstrip("/")
        version = os.environ.get("ANTHROPIC_API_VERSION", "2023-06-01")
        try:
            req = urllib.request.Request(
                f"{base}/v1/models?limit=100",
                headers={"x-api-key": key, "anthropic-version": version},
            )
            with urllib.request.urlopen(req, timeout=15) as r:
                data = _json.loads(r.read())
            ids = {m.get("id") for m in data.get("data", [])}
            out["providers"]["anthropic"] = {
                "status": "ok" if (small in ids and large in ids) else "stale",
                "small_valid": small in ids,
                "large_valid": large in ids,
                "known_ids": sorted(i for i in ids if isinstance(i, str))[:20],
            }
        except Exception as e:  # noqa: BLE001 — surface any HTTP/network shape here
            out["providers"]["anthropic"] = {
                "status": "error",
                "detail": f"{type(e).__name__}: {e}",
            }

    # OpenAI — placeholder until the company key arrives.
    if not os.environ.get("OPENAI_API_KEY", "").strip():
        out["providers"]["openai"] = {"status": "skipped", "reason": "OPENAI_API_KEY not set"}
    else:
        out["providers"]["openai"] = {
            "status": "configured",
            "reason": "model-list check lands with the OpenAI adapter (F7.4)",
        }

    # Bedrock / vLLM — configured but inactive until F7.5.
    out["providers"]["bedrock"] = {"status": "deferred", "reason": "adapter lands in F7.5"}
    out["providers"]["vllm"] = {"status": "deferred", "reason": "adapter lands in F7.5"}
    return out


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="doctor", description=__doc__.strip().splitlines()[0])
    ap.add_argument("--target", choices=readiness.TARGETS, default="local")
    ap.add_argument("--json", action="store_true", help="print the report as JSON")
    ap.add_argument("--skip-tests", action="store_true", help="do not run the unittest suite")
    ap.add_argument(
        "--no-live-health",
        action="store_true",
        help="check the /health route statically instead of serving it on loopback",
    )
    ap.add_argument("--repo", default=None, help="repository root (default: this checkout)")
    args = ap.parse_args(argv)

    rep = readiness.report(
        target=args.target,
        repo_root=args.repo,
        run_tests=not args.skip_tests,
        live_health=not args.no_live_health,
    )
    rep["model_ids"] = check_model_ids(json_only=args.json)
    if args.json:
        print(json.dumps(rep, indent=2, sort_keys=True))
    else:
        print(readiness.render(rep))
        print()
        print("model ids —")
        m = rep["model_ids"]
        print(f"  small: {m['small']}   large: {m['large']}")
        for name, info in m["providers"].items():
            status = info.get("status", "unknown")
            extra = ""
            if status == "stale":
                extra = (
                    f"  (small_valid={info.get('small_valid')} "
                    f"large_valid={info.get('large_valid')})"
                )
            elif status == "error":
                extra = f"  ({info.get('detail', '')})"
            elif "reason" in info:
                extra = f"  — {info['reason']}"
            print(f"  {name:<10} {status}{extra}")
    return 0 if rep["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
