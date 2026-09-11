"""ModelClient adapters (I4 — the model is a dial, not a foundation).

* ``AnthropicModelClient`` — the production provider (T35). Stdlib ``urllib``
  only (the SDK is reserved for a ``providers/`` gateway, F7.4). Every call is
  recorded to the API ledger (T36) with its real usage, and every failure is
  LOUD: a non-2xx response raises ``ProviderError`` carrying the status and the
  response body; nothing is ever swallowed or replaced by a mock.
* ``MockModelClient`` — deterministic latency and token counts, near-zero
  cost. An EXPLICIT test mode (``KF_MODEL_MODE=mock``) for load/budget-race
  tests, never a fallback.
* ``DisabledModelClient`` — ``available()`` is False; the extractive core
  answers with citations when the model is removed (``KF_MODEL_MODE=extractive``,
  the only keyless mode).
* ``HostedModelClient`` — an OpenAI-compatible endpoint via env; raises when
  unconfigured.

Model ceiling (T35, fixed decision): the large tier is ``claude-sonnet-4-6``;
the small tier is ``claude-haiku-4-5`` when the key lists it, otherwise
``claude-sonnet-4-6``. No other id is ever configured, defaulted or tried —
``resolve_models()`` raises ``ModelNotAllowed`` on anything else.
"""

from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.request

# ---- the allowed set (T35). Nothing outside it is ever used. ---------------
ALLOWED_MODELS = ("claude-sonnet-4-6", "claude-haiku-4-5")
DEFAULT_LARGE = "claude-sonnet-4-6"
PREFERRED_SMALL = "claude-haiku-4-5"  # used when the key lists it (doctor decides)

_TIER_PRICE = {"fast": 1e-6, "deep": 5e-6, "escalation": 1e-5}  # mock/hosted tiers only


class ProviderUnavailableError(RuntimeError):
    """The configured provider cannot be constructed (missing key/endpoint).
    Raised instead of falling back to a mock — keyless must be explicit."""


class ModelNotAllowedError(ValueError):
    """A model id outside the allowed set was configured (T35 ceiling)."""


# The spec's names (T35) — aliases of the Error-suffixed classes above.
ProviderUnavailable = ProviderUnavailableError
ModelNotAllowed = ModelNotAllowedError


class ProviderError(RuntimeError):
    """A provider call failed. Carries the HTTP status and the response body so
    the failure is diagnosable in the run log — never hidden."""

    def __init__(self, status: int, body: str, model: str = "", detail: str = ""):
        self.status, self.body, self.model = status, body, model
        msg = detail or f"provider returned HTTP {status}"
        super().__init__(f"{msg} (model={model!r}): {body[:800]}")


def data_root() -> str:
    """Where runtime data lives — a ``fabric-data`` checkout (``KF_DATA_ROOT``)
    or this repository's ``data/``."""
    return os.environ.get("KF_DATA_ROOT") or os.path.join(
        os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), "data"
    )


def provider_status() -> dict:
    """``data/provider_status.json`` as written by ``doctor --require anthropic``
    — the single source of truth for which small model the key supports."""
    try:
        with open(os.path.join(data_root(), "provider_status.json"), encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def resolve_models() -> tuple[str, str]:
    """(small, large) — env override → doctor's provider_status → defaults.
    Raises ``ModelNotAllowed`` if either id is outside the allowed set."""
    st = provider_status()
    small = os.environ.get("KF_MODEL_SMALL") or st.get("model_small") or DEFAULT_LARGE
    large = os.environ.get("KF_MODEL_LARGE") or st.get("model_large") or DEFAULT_LARGE
    for m in (small, large):
        if m not in ALLOWED_MODELS:
            raise ModelNotAllowed(
                f"Model id outside the allowed set: {m!r} (allowed: {', '.join(ALLOWED_MODELS)})"
            )
    return small, large


# ---- multi-model gateway: one gateway in front of several models/providers ----
def model_for_tier(tier: str) -> str:
    """Display name for a router tier. Under a real provider this is the real
    id (``resolve_models``); the mock tiers keep their ``kf-mock-*`` names."""
    if tier in ("", "none", None):
        return "none (extractive core)"
    mode = (os.environ.get("KF_MODEL_MODE") or "anthropic").lower()
    if mode == "anthropic":
        try:
            small, large = resolve_models()
        except ModelNotAllowed:
            small, large = DEFAULT_LARGE, DEFAULT_LARGE
        return large if tier == "escalation" else small
    defaults = {"fast": "kf-mock-small", "deep": "kf-mock-mid", "escalation": "kf-mock-large"}
    env = {
        "fast": os.environ.get("KF_MODEL_FAST"),
        "deep": os.environ.get("KF_MODEL_DEEP"),
        "escalation": os.environ.get("KF_MODEL_ESCALATION"),
    }
    return env.get(tier) or defaults.get(tier, "kf-mock-small")


COMPLEXITY_OF_TIER = {
    "none": "simple",
    "fast": "medium",
    "deep": "complex",
    "escalation": "complex",
}


class MockModelClient:
    """Explicit test double (``KF_MODEL_MODE=mock``). Never a fallback."""

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
    """``KF_MODEL_MODE=extractive`` — the only keyless mode. The core answers
    extractively with citations; any attempt to call the model is a bug."""

    def available(self) -> bool:
        return False

    def complete(self, tenant: str, tier: str, messages: list[dict], opts: dict) -> dict:
        raise RuntimeError("model disabled (I4): core must answer extractively")


class HostedModelClient:
    def __init__(self):
        self.base = os.environ.get("KF_MODEL_BASE_URL", "")
        self.key = os.environ.get("KF_MODEL_API_KEY", "")
        self.model = os.environ.get("KF_MODEL_NAME", "gpt-4o-mini")

    def available(self) -> bool:
        return bool(self.base and self.key)

    def complete(self, tenant: str, tier: str, messages: list[dict], opts: dict) -> dict:
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
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                data = json.loads(resp.read())
        except urllib.error.HTTPError as e:
            raise ProviderError(e.code, e.read().decode("utf-8", "replace"), model) from e
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
# AnthropicModelClient (T35) — the production provider, stdlib urllib only.
#
# Env:
#   ANTHROPIC_API_KEY      — required; absence raises ProviderUnavailable
#   KF_MODEL_SMALL/LARGE   — optional overrides, validated against ALLOWED_MODELS
#   ANTHROPIC_BASE_URL     — override endpoint (proxy / test); optional
#   ANTHROPIC_API_VERSION  — default ``2023-06-01``
# ---------------------------------------------------------------------------
def _accepts_sampling(model: str) -> bool:
    """Opus 5 / Sonnet 5 / Opus 4.6-4.8 / Fable reject ``temperature`` with a
    400; Sonnet 4.6 and Haiku 4.5 accept it."""
    m = (model or "").lower()
    return not any(
        b in m for b in ("opus-5", "opus-4-6", "opus-4-7", "opus-4-8", "sonnet-5", "fable")
    )


class AnthropicModelClient:
    """Anthropic Messages API client. Reads ``ANTHROPIC_API_KEY``; never
    commits a key. ``available()`` is True only when the key is present.

    ``messages(body, purpose)`` is the single ledgered entry point (T36): it
    sends any Messages-API body (text, images, tools, ``cache_control``),
    records the real usage to the API ledger, and returns the parsed response.
    ``complete()`` is the three-method surface the answer service uses."""

    def __init__(self):
        self.key = os.environ.get("ANTHROPIC_API_KEY", "").strip()
        self.base = os.environ.get("ANTHROPIC_BASE_URL", "https://api.anthropic.com").rstrip("/")
        self.version = os.environ.get("ANTHROPIC_API_VERSION", "2023-06-01")

    def available(self) -> bool:
        return bool(self.key)

    # -- low-level ----------------------------------------------------------
    def _headers(self) -> dict:
        return {
            "x-api-key": self.key,
            "anthropic-version": self.version,
            "Content-Type": "application/json",
        }

    def models_available(self) -> list[str]:
        """``GET /v1/models`` — the ids this key may use. 401 raises
        ``ProviderError(401)`` so the doctor can say "Key rejected"."""
        req = urllib.request.Request(f"{self.base}/v1/models?limit=100", headers=self._headers())
        try:
            with urllib.request.urlopen(req, timeout=20) as r:
                data = json.loads(r.read())
        except urllib.error.HTTPError as e:
            raise ProviderError(
                e.code, e.read().decode("utf-8", "replace"), detail="models list failed"
            ) from e
        except urllib.error.URLError as e:
            raise ProviderError(0, str(e.reason), detail="models list unreachable") from e
        return sorted(m.get("id") for m in data.get("data", []) if isinstance(m.get("id"), str))

    def messages(
        self,
        body: dict,
        purpose: str = "answer_bake",
        *,
        repo: str = "",
        doc_id: str = "",
        question_hash: str = "",
    ) -> dict:
        """``POST /v1/messages`` with ``body`` (must carry ``model``). Returns the
        parsed response plus ``request_id`` and ``latency_ms``; records the call
        to the API ledger. Any non-2xx or network failure raises
        ``ProviderError`` with the body — never swallowed."""
        from ..telemetry import api_ledger

        model = body.get("model", "")
        if model not in ALLOWED_MODELS:
            raise ModelNotAllowed(f"Model id outside the allowed set: {model!r}")
        req = urllib.request.Request(
            f"{self.base}/v1/messages",
            data=json.dumps(body).encode("utf-8"),
            headers=self._headers(),
        )
        t0 = time.perf_counter()
        try:
            with urllib.request.urlopen(req, timeout=int(body.get("_timeout", 120))) as resp:
                raw = resp.read()
                request_id = resp.headers.get("request-id", "") or ""
        except urllib.error.HTTPError as e:
            raise ProviderError(e.code, e.read().decode("utf-8", "replace"), model) from e
        except urllib.error.URLError as e:
            raise ProviderError(0, str(e.reason), model, detail="provider unreachable") from e
        latency_ms = (time.perf_counter() - t0) * 1000.0
        data = json.loads(raw)
        usage = data.get("usage", {}) or {}
        row = api_ledger.record(
            purpose=purpose,
            model=data.get("model") or model,
            usage=usage,
            latency_ms=latency_ms,
            request_id=request_id,
            repo=repo,
            doc_id=doc_id,
            question_hash=question_hash,
        )
        data["request_id"] = request_id
        data["latency_ms"] = round(latency_ms, 2)
        data["cost_usd"] = row["cost_usd"]
        return data

    # -- the three-method surface -------------------------------------------
    def complete(self, tenant: str, tier: str, messages: list[dict], opts: dict) -> dict:
        small, large = resolve_models()
        model = large if tier == "escalation" else small
        # Split any system-role message off into the top-level `system` field;
        # the Messages API rejects `role: "system"` inside messages.
        system = None
        msgs = []
        for m in messages:
            role = m.get("role")
            content = m.get("content", "")
            if role == "system":
                system = (system + "\n\n" + content) if system else content
            elif role in ("user", "assistant"):
                msgs.append({"role": role, "content": content})
        body: dict = {
            "model": model,
            "max_tokens": int(opts.get("max_tokens", 4096)),
            "messages": msgs,
        }
        if system is not None:
            # The stable preamble is cached (T43 layout: stable → volatile).
            body["system"] = [
                {"type": "text", "text": system, "cache_control": {"type": "ephemeral"}}
            ]
        if "temperature" in opts and _accepts_sampling(model):
            body["temperature"] = float(opts["temperature"])
        data = self.messages(
            body,
            purpose=opts.get("purpose", "answer_bake"),
            repo=opts.get("repo", ""),
            doc_id=opts.get("doc_id", ""),
            question_hash=opts.get("question_hash", ""),
        )
        text = "".join(
            b.get("text", "") for b in data.get("content", []) if b.get("type") == "text"
        )
        usage = data.get("usage", {}) or {}
        in_tok = int(usage.get("input_tokens", 0))
        out_tok = int(usage.get("output_tokens", 0))
        return {
            "text": text,
            "usage": {
                "tokens": in_tok + out_tok,
                "in": in_tok,
                "out": out_tok,
                "cache_read": int(usage.get("cache_read_input_tokens", 0) or 0),
                "cache_creation": int(usage.get("cache_creation_input_tokens", 0) or 0),
            },
            "cost": float(data.get("cost_usd", 0.0)),
            "model_name": data.get("model") or model,
            "request_id": data.get("request_id", ""),
            "stop_reason": data.get("stop_reason"),
        }


# ---- Provider registry: what KF_MODEL_MODE picks. -------------------------
PROVIDER_ORDER = ("anthropic", "openai", "bedrock", "vllm")

KEYLESS_MODES = ("extractive", "off")  # `off` is the legacy alias of `extractive`


def build_model_client() -> object:
    """Construct the configured provider or raise — never a silent mock.

    * ``anthropic`` (the default when unset) — requires ``ANTHROPIC_API_KEY``.
    * ``extractive`` / ``off`` — the only keyless mode; must be explicit.
    * ``mock`` — an explicit test double.
    * ``openai`` / ``hosted`` — requires the hosted endpoint + key.
    """
    mode = (os.environ.get("KF_MODEL_MODE") or "anthropic").lower()
    if mode in KEYLESS_MODES:
        return DisabledModelClient()
    if mode == "mock":
        return MockModelClient()
    if mode == "anthropic":
        client = AnthropicModelClient()
        if not client.available():
            raise ProviderUnavailable(
                "KF_MODEL_MODE=anthropic but ANTHROPIC_API_KEY is not set. Store it under "
                "Actions → Secrets as ANTHROPIC_API_KEY, or set KF_MODEL_MODE=extractive "
                "for the explicit keyless mode."
            )
        return client
    if mode in ("openai", "hosted"):
        hosted = HostedModelClient()
        if not hosted.available():
            raise ProviderUnavailable(
                f"KF_MODEL_MODE={mode} requires KF_MODEL_BASE_URL and KF_MODEL_API_KEY."
            )
        return hosted
    if mode in ("bedrock", "vllm"):
        raise ProviderUnavailable(f"KF_MODEL_MODE={mode}: adapter not configured (F7.5).")
    raise ProviderUnavailable(
        f"KF_MODEL_MODE={mode!r} is not a provider. Use anthropic, extractive, mock, or openai."
    )
