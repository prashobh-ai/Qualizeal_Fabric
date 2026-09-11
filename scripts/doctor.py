"""Deployment readiness doctor.

    python3 scripts/doctor.py --target local          # laptop / compose shape
    python3 scripts/doctor.py --target aws            # lists exactly what is missing for AWS
    python3 scripts/doctor.py --target aws --json     # machine-readable (same as /admin/doctor)
    python3 scripts/doctor.py --target local --skip-tests --no-live-health
    python3 scripts/doctor.py --require anthropic     # T35: prove the key + pin the models

Exit status (readiness): 0 when there are no blockers, 1 when the target is
not ready, 2 on bad arguments. The report never contacts AWS; it inspects the
repository, the image build file, the IaC module and the process environment.

``--require anthropic`` (T35) is the provider gate every Actions run passes
through first. It exits:
    2  ANTHROPIC_API_KEY is empty (names the secret)
    3  the key is rejected by GET /v1/models
    4  KF_MODEL_SMALL / KF_MODEL_LARGE names a model outside the allowed set
    5  claude-sonnet-4-6 is not available to this key
    6  the ping (POST /v1/messages, max_tokens 8) is not 200
and on success writes ``data/provider_status.json`` and appends the provider
line to ``$GITHUB_STEP_SUMMARY``.
"""

from __future__ import annotations

import argparse
import datetime as _dt
import hashlib
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("KF_MODEL_MODE", "mock")

from knowledge_fabric.adapters import model as _model  # noqa: E402
from knowledge_fabric.ops import readiness  # noqa: E402
from knowledge_fabric.telemetry import api_ledger  # noqa: E402

ALLOWED = _model.ALLOWED_MODELS


def require_anthropic(out=print) -> int:
    """T35 provider pinning + proof of use. Returns the exit code (0 = ok)."""
    key = os.environ.get("ANTHROPIC_API_KEY", "").strip()
    if not key:
        out(
            "doctor: ANTHROPIC_API_KEY is empty. Store the key under "
            "Actions → Secrets with the exact name ANTHROPIC_API_KEY."
        )
        return 2
    client = _model.AnthropicModelClient()

    # 2. GET /v1/models
    try:
        models_available = client.models_available()
    except _model.ProviderError as e:
        if e.status == 401:
            out("doctor: Key rejected (401 from GET /v1/models).")
        else:
            out(f"doctor: could not list models (HTTP {e.status}): {e.body[:300]}")
        return 3

    # 3. Pin the two tiers; refuse anything outside the allowed set.
    model_small = (
        _model.PREFERRED_SMALL
        if _model.PREFERRED_SMALL in models_available
        else (_model.DEFAULT_LARGE)
    )
    model_large = _model.DEFAULT_LARGE
    for var in ("KF_MODEL_SMALL", "KF_MODEL_LARGE"):
        val = os.environ.get(var, "").strip()
        if val and val not in ALLOWED:
            out(f"doctor: Model id outside the allowed set: {var}={val!r} (allowed: {ALLOWED})")
            return 4
    if os.environ.get("KF_MODEL_SMALL", "").strip():
        model_small = os.environ["KF_MODEL_SMALL"].strip()
    if os.environ.get("KF_MODEL_LARGE", "").strip():
        model_large = os.environ["KF_MODEL_LARGE"].strip()
    if _model.DEFAULT_LARGE not in models_available:
        out(f"doctor: Sonnet 4.6 not available to this key (models: {models_available})")
        return 5

    # 4. Ping with the small model — recorded to the ledger as doctor_ping.
    try:
        ping = client.messages(
            {
                "model": model_small,
                "max_tokens": 8,
                "messages": [{"role": "user", "content": "ping"}],
            },
            purpose="doctor_ping",
        )
    except _model.ProviderError as e:
        out(f"doctor: ping failed (HTTP {e.status}): {e.body[:600]}")
        return 6
    usage = ping.get("usage", {}) or {}
    ping_usage = {
        "input_tokens": int(usage.get("input_tokens", 0) or 0),
        "output_tokens": int(usage.get("output_tokens", 0) or 0),
    }

    # 5. provider_status.json — the runtime's single source of truth.
    fingerprint = hashlib.sha256(key.encode("utf-8")).hexdigest()[:16]
    status = {
        "provider": "anthropic",
        "key_fingerprint": fingerprint,
        "models_available": models_available,
        "model_small": model_small,
        "model_large": model_large,
        "verified_at": _dt.datetime.now(_dt.UTC).isoformat(timespec="seconds"),
        "ping_usage": ping_usage,
        "ping_request_id": ping.get("request_id", ""),
    }
    root = _model.data_root()
    os.makedirs(root, exist_ok=True)
    with open(os.path.join(root, "provider_status.json"), "w", encoding="utf-8") as f:
        json.dump(status, f, indent=2, sort_keys=True)

    # 6. The provider line — in the log and in the Actions run summary.
    line = (
        f"Provider: Anthropic · key …{fingerprint[-4:]} · small {model_small} · "
        f"large {model_large} · ping {ping_usage['input_tokens']}/"
        f"{ping_usage['output_tokens']} tokens"
    )
    out(line)
    api_ledger.append_step_summary(line)
    return 0


def check_model_ids(json_only: bool = False) -> dict:
    """Validate the configured model ids against the provider's model list.
    Runs only when the credential is set; ``skipped`` otherwise. The defaults
    are the T35 allowed pair — nothing else is ever defaulted."""
    small = os.environ.get("KF_MODEL_SMALL", _model.PREFERRED_SMALL)
    large = os.environ.get("KF_MODEL_LARGE", _model.DEFAULT_LARGE)
    out: dict = {"small": small, "large": large, "allowed": list(ALLOWED), "providers": {}}
    for m in (small, large):
        if m not in ALLOWED:
            out["providers"]["anthropic"] = {
                "status": "not-allowed",
                "detail": f"{m!r} is outside the allowed set {ALLOWED}",
            }
            return out

    key = os.environ.get("ANTHROPIC_API_KEY", "").strip()
    if not key:
        out["providers"]["anthropic"] = {"status": "skipped", "reason": "ANTHROPIC_API_KEY not set"}
    else:
        try:
            ids = set(_model.AnthropicModelClient().models_available())
            out["providers"]["anthropic"] = {
                "status": "ok" if (small in ids and large in ids) else "stale",
                "small_valid": small in ids,
                "large_valid": large in ids,
                "known_ids": sorted(i for i in ids if isinstance(i, str))[:20],
            }
        except _model.ProviderError as e:
            out["providers"]["anthropic"] = {
                "status": "error",
                "detail": f"HTTP {e.status}: {e.body[:200]}",
            }

    if not os.environ.get("OPENAI_API_KEY", "").strip():
        out["providers"]["openai"] = {"status": "skipped", "reason": "OPENAI_API_KEY not set"}
    else:
        out["providers"]["openai"] = {
            "status": "configured",
            "reason": "model-list check lands with the OpenAI adapter (F7.4)",
        }
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
    ap.add_argument(
        "--require",
        choices=["anthropic"],
        default=None,
        help="T35: verify the provider key, pin the models, ping, write provider_status.json",
    )
    args = ap.parse_args(argv)

    if args.require == "anthropic":
        return require_anthropic()

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
        print(f"  small: {m['small']}   large: {m['large']}   allowed: {m['allowed']}")
        for name, info in m["providers"].items():
            status = info.get("status", "unknown")
            extra = ""
            if status == "stale":
                extra = (
                    f"  (small_valid={info.get('small_valid')} "
                    f"large_valid={info.get('large_valid')})"
                )
            elif status in ("error", "not-allowed"):
                extra = f"  ({info.get('detail', '')})"
            elif "reason" in info:
                extra = f"  — {info['reason']}"
            print(f"  {name:<10} {status}{extra}")
    return 0 if rep["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
