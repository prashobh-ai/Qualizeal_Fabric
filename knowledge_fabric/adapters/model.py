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
    env = {"fast": os.environ.get("KF_MODEL_FAST"), "deep": os.environ.get("KF_MODEL_DEEP"),
           "escalation": os.environ.get("KF_MODEL_ESCALATION")}
    if tier in ("", "none", None):
        return "none (extractive core)"
    return env.get(tier) or defaults.get(tier, "kf-mock-small")


COMPLEXITY_OF_TIER = {"none": "simple", "fast": "medium", "deep": "complex", "escalation": "complex"}


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
        return {"text": draft, "usage": {"tokens": tokens, "in": in_tokens, "out": out_tokens},
                "cost": cost, "model_name": model_for_tier(tier)}


class DisabledModelClient:
    def available(self) -> bool:
        return False

    def complete(self, tenant: str, tier: str, messages: list[dict], opts: dict) -> dict:
        raise RuntimeError("model disabled (I4): core must answer extractively")


class HostedModelClient:
    def __init__(self):
        self.base = os.environ.get("KF_MODEL_BASE_URL", "")
        self.key = os.environ.get("KF_MODEL_API_KEY", "")
        self.model = os.environ.get("KF_MODEL_NAME", "gpt-4o-mini")   # fallback when a tier has no name

    def available(self) -> bool:
        return bool(self.base and self.key)

    def complete(self, tenant: str, tier: str, messages: list[dict], opts: dict) -> dict:
        import json
        import urllib.request
        model = os.environ.get({"fast": "KF_MODEL_FAST", "deep": "KF_MODEL_DEEP",
                                "escalation": "KF_MODEL_ESCALATION"}.get(tier, ""), "") or self.model
        body = json.dumps({"model": model, "messages": messages,
                           "temperature": opts.get("temperature", 0.0)}).encode()
        req = urllib.request.Request(
            self.base.rstrip("/") + "/chat/completions", data=body,
            headers={"Authorization": f"Bearer {self.key}", "Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=30) as resp:
            data = json.loads(resp.read())
        text = data["choices"][0]["message"]["content"]
        usage = data.get("usage", {})
        tokens = usage.get("total_tokens", len(text.split()))
        return {"text": text, "usage": {"tokens": tokens, "in": usage.get("prompt_tokens", 0),
                                        "out": usage.get("completion_tokens", 0)},
                "cost": tokens * _TIER_PRICE.get(tier, 5e-6), "model_name": model}


def build_model_client() -> object:
    mode = os.environ.get("KF_MODEL_MODE", "mock").lower()
    if mode == "off":
        return DisabledModelClient()
    if mode == "hosted":
        hosted = HostedModelClient()
        return hosted if hosted.available() else MockModelClient()
    return MockModelClient()
