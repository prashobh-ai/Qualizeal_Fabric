"""ModelClient adapters (I4 — the model is a dial, not a foundation).

* ``MockModelClient`` — deterministic latency and token counts, near-zero
  cost. Used for all load/budget-race tests (Runbook 6.1) so we test our own
  enforcement, not a provider's wallet.
* ``HostedModelClient`` — talks to an OpenAI-compatible endpoint via env
  (KF_MODEL_BASE_URL / KF_MODEL_API_KEY). Same three methods.
* ``DisabledModelClient`` — ``available()`` is False; proves the extractive
  core still answers with citations when the model is removed.

Prices are per-token illustrative tiers; the point is that cost is real,
attributable and capped, not that these are any provider's list prices.
"""

from __future__ import annotations

import os
import time

_TIER_PRICE = {"fast": 1e-6, "deep": 5e-6, "escalation": 1e-5}


# ---- multi-model gateway: one gateway in front of several models/providers ----
# Each tier maps to a NAMED model (roadmap WS2: "one gateway in front of three AI
# providers"). Names are env-overridable so local / AWS / client differ by config
# only. The answer records model_name so the Ask card and dashboard can show
# "which model ran, when, and why".
def model_for_tier(tier: str) -> str:
    defaults = {"fast": "kf-mock-small", "deep": "kf-mock-mid", "escalation": "kf-mock-large"}
    env = {
        "fast": os.environ.get("KF_MODEL_FAST"),
        "deep": os.environ.get("KF_MODEL_DEEP"),
        "escalation": os.environ.get("KF_MODEL_ESCALATION"),
    }
    if tier in ("", "none", None):
        return "none (extractive core)"
    return env.get(tier) or defaults.get(tier, "kf-mock-small")


COMPLEXITY_OF_TIER = {
    "none": "simple",
    "fast": "medium",
    "deep": "complex",
    "escalation": "complex",
}


class MockModelClient:
    def __init__(self, latency_ms: float = 5.0):
        self.latency_ms = latency_ms

    def available(self) -> bool:
        return True

    def complete(self, tenant: str, tier: str, messages: list[dict], opts: dict) -> dict:
        time.sleep(self.latency_ms / 1000.0)
        # Deterministic "synthesis": echo the provided extractive draft. The
        # answer service never lets the model introduce uncited content — the
        # post-check drops anything the passages don't support.
        draft = ""
        for m in messages:
            if m.get("role") == "user":
                draft = m.get("content", "")
        out_tokens = max(1, len(draft.split()))
        in_tokens = sum(len(m.get("content", "").split()) for m in messages)
        tokens = in_tokens + out_tokens
        cost = tokens * _TIER_PRICE.get(tier, 1e-6)
        return {
            "text": draft,
            "usage": {"tokens": tokens, "in": in_tokens, "out": out_tokens},
            "cost": cost,
            "model_name": model_for_tier(tier),
        }


class DisabledModelClient:
    def available(self) -> bool:
        return False

    def complete(self, tenant: str, tier: str, messages: list[dict], opts: dict) -> dict:
        raise RuntimeError("model disabled (I4): core must answer extractively")


class HostedModelClient:
    def __init__(self):
        self.base = os.environ.get("KF_MODEL_BASE_URL", "")
        self.key = os.environ.get("KF_MODEL_API_KEY", "")
        self.model = os.environ.get(
            "KF_MODEL_NAME", "gpt-4o-mini"
        )  # fallback when a tier has no name

    def available(self) -> bool:
        return bool(self.base and self.key)

    def complete(self, tenant: str, tier: str, messages: list[dict], opts: dict) -> dict:
        import json
        import urllib.request

        model = (
            os.environ.get(
                {
                    "fast": "KF_MODEL_FAST",
                    "deep": "KF_MODEL_DEEP",
                    "escalation": "KF_MODEL_ESCALATION",
                }.get(tier, ""),
                "",
            )
            or self.model
        )
        body = json.dumps(
            {"model": model, "messages": messages, "temperature": opts.get("temperature", 0.0)}
        ).encode()
        req = urllib.request.Request(
            self.base.rstrip("/") + "/chat/completions",
            data=body,
            headers={"Authorization": f"Bearer {self.key}", "Content-Type": "application/json"},
        )
        with urllib.request.urlopen(req, timeout=30) as resp:
            data = json.loads(resp.read())
        text = data["choices"][0]["message"]["content"]
        usage = data.get("usage", {})
        tokens = usage.get("total_tokens", len(text.split()))
        return {
            "text": text,
            "usage": {
                "tokens": tokens,
                "in": usage.get("prompt_tokens", 0),
                "out": usage.get("completion_tokens", 0),
            },
            "cost": tokens * _TIER_PRICE.get(tier, 5e-6),
            "model_name": model,
        }


# ---------------------------------------------------------------------------
# AnthropicModelClient (F0.3) — first-class Anthropic provider using stdlib
# urllib only. The Anthropic Python SDK is not imported here because F7's
# grep rule reserves SDK imports for ``providers/`` (F7.4). Once that
# gateway lands the SDK-based client will replace this one behind the same
# three-method surface.
#
# Env:
#   ANTHROPIC_API_KEY           — required to enable this client
#   KF_MODEL_SMALL              — default ``claude-haiku-4-5`` (L2 tier)
#   KF_MODEL_LARGE              — default ``claude-opus-5``    (L3 tier)
#   ANTHROPIC_BASE_URL          — override endpoint (proxy / test); optional
#   ANTHROPIC_API_VERSION       — default ``2023-06-01``
# ---------------------------------------------------------------------------

# Illustrative-only fallback prices, used when the API does not return usage.
_ANTHROPIC_PRICE = {
    "fast": (1.0e-6, 5.0e-6),
    "deep": (2.0e-6, 10.0e-6),
    "escalation": (5.0e-6, 25.0e-6),
}


def _accepts_sampling(model: str) -> bool:
    """Opus 5 / Sonnet 5 / Opus 4.6-4.8 / Fable reject ``temperature`` (and the
    other sampling params) with a 400; Haiku 4.5 and older accept them."""
    m = (model or "").lower()
    return not any(
        b in m for b in ("opus-5", "opus-4-6", "opus-4-7", "opus-4-8", "sonnet-5", "fable")
    )


def _anthropic_model_for_tier(tier: str) -> str:
    """Anthropic model id for a router tier. ``fast/deep`` map to the small
    model; ``escalation`` maps to the large model. Names come from env with
    defaults picked from the Anthropic current-model set."""
    small = os.environ.get("KF_MODEL_SMALL", "claude-haiku-4-5")
    large = os.environ.get("KF_MODEL_LARGE", "claude-opus-5")
    if tier in ("", "none", None):
        return "none (extractive core)"
    if tier == "escalation":
        return large
    return small


class AnthropicModelClient:
    """Anthropic Messages API client, stdlib-only.

    Reads the API key from ``ANTHROPIC_API_KEY``. Never commits a key.
    ``available()`` is True only when the key is present.
    """

    def __init__(self):
        self.key = os.environ.get("ANTHROPIC_API_KEY", "").strip()
        self.base = os.environ.get("ANTHROPIC_BASE_URL", "https://api.anthropic.com").rstrip("/")
        self.version = os.environ.get("ANTHROPIC_API_VERSION", "2023-06-01")

    def available(self) -> bool:
        return bool(self.key)

    def complete(self, tenant: str, tier: str, messages: list[dict], opts: dict) -> dict:
        import json
        import urllib.request

        model = _anthropic_model_for_tier(tier)
        # Split any system-role message off into the top-level `system` field;
        # the Anthropic Messages API rejects `role: "system"` inside messages.
        system = None
        msgs = []
        for m in messages:
            role = m.get("role")
            content = m.get("content", "")
            if role == "system":
                system = (system + "\n\n" + content) if system else content
            elif role in ("user", "assistant"):
                msgs.append({"role": role, "content": content})
        body = {
            "model": model,
            "max_tokens": int(opts.get("max_tokens", 4096)),
            "messages": msgs,
        }
        if system is not None:
            body["system"] = system
        if "temperature" in opts and _accepts_sampling(model):
            body["temperature"] = float(opts["temperature"])
        req = urllib.request.Request(
            f"{self.base}/v1/messages",
            data=json.dumps(body).encode("utf-8"),
            headers={
                "x-api-key": self.key,
                "anthropic-version": self.version,
                "Content-Type": "application/json",
            },
        )
        with urllib.request.urlopen(req, timeout=60) as resp:
            data = json.loads(resp.read())
        # `content` is a list of blocks; concatenate every text block.
        text = "".join(
            b.get("text", "") for b in data.get("content", []) if b.get("type") == "text"
        )
        usage = data.get("usage", {})
        in_tok = int(usage.get("input_tokens", 0))
        out_tok = int(usage.get("output_tokens", 0))
        pin, pout = _ANTHROPIC_PRICE.get(tier, _ANTHROPIC_PRICE["deep"])
        cost = in_tok * pin + out_tok * pout
        return {
            "text": text,
            "usage": {
                "tokens": in_tok + out_tok,
                "in": in_tok,
                "out": out_tok,
                "cache_read": int(usage.get("cache_read_input_tokens", 0)),
                "cache_creation": int(usage.get("cache_creation_input_tokens", 0)),
            },
            "cost": cost,
            "model_name": data.get("model") or model,
        }


# ---- Provider registry: what KF_MODEL_MODE picks, and the escalation order.
# F7.4 will formalise this behind ``providers/gateway.py``; F0.3 records the
# order here so the answer service already knows the intended chain.
PROVIDER_ORDER = ("anthropic", "openai", "bedrock", "vllm")


def _default_mode() -> str:
    """Default provider: anthropic when its key is present, else mock (F0.3)."""
    if os.environ.get("ANTHROPIC_API_KEY", "").strip():
        return "anthropic"
    return "mock"


def build_model_client() -> object:
    mode = (os.environ.get("KF_MODEL_MODE") or _default_mode()).lower()
    if mode == "off":
        return DisabledModelClient()
    if mode == "anthropic":
        client = AnthropicModelClient()
        return client if client.available() else MockModelClient()
    if mode == "openai":
        # OpenAI (and OpenAI-compatible) endpoints use the existing hosted
        # client (KF_MODEL_BASE_URL / KF_MODEL_API_KEY). Falls back to mock
        # when unconfigured — the OPENAI_API_KEY placeholder in .env.example
        # is empty until the company key arrives (F0.3).
        hosted = HostedModelClient()
        return hosted if hosted.available() else MockModelClient()
    if mode == "hosted":  # legacy alias for `openai`
        hosted = HostedModelClient()
        return hosted if hosted.available() else MockModelClient()
    if mode in ("bedrock", "vllm"):
        # Reserved; the concrete adapters land in F7.5. Fall back to mock so
        # the doctor's stale-defaults check can run before deployment.
        return MockModelClient()
    return MockModelClient()
