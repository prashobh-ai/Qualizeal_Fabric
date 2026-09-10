"""Connector administration (Stage-2 Section C): enable/disable, allow-lists,
scopes — the per-tenant *permission boundary* around every connector.

Backed by ``connector_config(tenant, source, enabled, config, allow, scopes,
updated_at)``. Semantics:

* **enabled** — a disabled connector is skipped by the refresh scheduler and
  "Sync now". Unconfigured connectors are enabled (``is_enabled`` → ``True``).
* **allow** — the allow-list the tenant admin grants (repos for GitHub,
  projects for Jira, extensions for Files). ``effective_config`` writes it
  into the connector's own allow-list key (``ALLOW_KEYS``) *last*, so an
  admin allow-list always overrides whatever a schedule or request config
  asked for. An empty allow-list means "no admin restriction" — matching the
  connectors' own semantics where an empty allow set is unrestricted.
* **scopes** — the permission scopes granted to the connector. Unconfigured,
  they default to what the connector *declares* (e.g. ``jira:read``);
  ``check_scopes`` reports declared vs granted vs missing.
* **config** — connector-level settings (base URL, credentials reference).
  Merged *under* the per-schedule config by ``effective_config``.

``list_all`` is the admin page's connector table: the registry ∪ configured
rows, each flagged ``registered`` (a configured-but-unregistered source is
shown so the admin can see and remove stale config).

Every read/write is tenant-filtered (I5); every write is audited.
"""

from __future__ import annotations

import json
import re
from typing import Any

from ..contracts.types import new_id, now_ms
from ..stores.repositories import _guard
from . import registry

__all__ = [
    "ALLOW_KEYS",
    "DEFAULT_ALLOW_KEY",
    "upsert",
    "get",
    "list_all",
    "is_enabled",
    "enable",
    "disable",
    "declared_scopes",
    "check_scopes",
    "effective_config",
    "allow_key",
]

#: connector-specific config key that carries its allow-list
ALLOW_KEYS: dict[str, str] = {"github": "repos", "jira": "projects", "files": "allow_ext"}
#: key used for connectors without a known allow-list key
DEFAULT_ALLOW_KEY = "allow"

_SOURCE_RE = re.compile(r"[A-Za-z0-9_.:-]{1,64}")
_AUDIT_SUBJECT = "system"


# --------------------------------------------------------------------------
# helpers
# --------------------------------------------------------------------------
def _check_source(source: str) -> str:
    if not isinstance(source, str) or not _SOURCE_RE.fullmatch(source):
        raise ValueError(f"invalid connector source name {source!r}")
    return source


def _loads(raw: Any, default: Any) -> Any:
    if raw in (None, ""):
        return default
    try:
        val = json.loads(raw)
    except (TypeError, ValueError):
        return default
    return val if isinstance(val, type(default)) else default


def _str_list(value: Any, field: str) -> list[str]:
    if isinstance(value, (str, bytes)) or not hasattr(value, "__iter__"):
        raise TypeError(f"{field} must be a list of strings")
    out: list[str] = []
    for v in value:
        if not isinstance(v, str) or not v.strip():
            raise TypeError(f"{field} entries must be non-empty strings, got {v!r}")
        if v not in out:
            out.append(v)
    return out


def allow_key(source: str) -> str:
    """The config key a connector reads its allow-list from."""
    return ALLOW_KEYS.get(source, DEFAULT_ALLOW_KEY)


def declared_scopes(source: str) -> list[str]:
    """Scopes the registered connector declares it needs (``[]`` if unregistered)."""
    cls = registry.REGISTRY.get(source)
    if cls is None:
        return []
    try:
        return list(cls("_probe", {}).scopes())
    except Exception:  # pragma: no cover - a connector that cannot be probed declares nothing
        return []


def _default(source: str) -> dict:
    return {
        "source": source,
        "enabled": True,
        "config": {},
        "allow": [],
        "scopes": declared_scopes(source),
        "updated_at": None,
        "registered": source in registry.REGISTRY,
    }


def _row_to_dict(r) -> dict:
    return {
        "source": r["source"],
        "enabled": bool(r["enabled"]),
        "config": _loads(r["config"], {}),
        "allow": _loads(r["allow"], []),
        "scopes": _loads(r["scopes"], []),
        "updated_at": r["updated_at"],
        "registered": r["source"] in registry.REGISTRY,
    }


# --------------------------------------------------------------------------
# contract: upsert / get / list_all / is_enabled
# --------------------------------------------------------------------------
def get(platform, tenant: str, source: str) -> dict | None:
    """The stored config for one connector, or ``None`` if never configured."""
    _guard(tenant)
    _check_source(source)
    r = platform.db.one(
        "SELECT * FROM connector_config WHERE tenant=? AND source=?", (tenant, source)
    )
    return _row_to_dict(r) if r else None


def upsert(
    platform,
    tenant: str,
    source: str,
    enabled: bool | None = None,
    config: dict | None = None,
    allow: list | None = None,
    scopes: list | None = None,
    by_subject: str = _AUDIT_SUBJECT,
) -> dict:
    """Create or partially update a connector's config; ``None`` leaves a field as is.

    A first upsert starts from the defaults (enabled, no allow-list, declared
    scopes). Returns the stored record and writes an audit entry.
    """
    _guard(tenant)
    _check_source(source)
    if config is not None and not isinstance(config, dict):
        raise TypeError("config must be a dict")
    rec = get(platform, tenant, source) or _default(source)
    if enabled is not None:
        rec["enabled"] = bool(enabled)
    if config is not None:
        rec["config"] = dict(config)
    if allow is not None:
        rec["allow"] = _str_list(allow, "allow")
    if scopes is not None:
        rec["scopes"] = _str_list(scopes, "scopes")
    rec["updated_at"] = now_ms()
    platform.db.execute(
        """INSERT INTO connector_config(tenant,source,enabled,config,allow,scopes,updated_at)
           VALUES(?,?,?,?,?,?,?)
           ON CONFLICT(tenant,source) DO UPDATE SET enabled=excluded.enabled,
           config=excluded.config, allow=excluded.allow, scopes=excluded.scopes,
           updated_at=excluded.updated_at""",
        (
            tenant,
            source,
            1 if rec["enabled"] else 0,
            json.dumps(rec["config"], default=str),
            json.dumps(rec["allow"]),
            json.dumps(rec["scopes"]),
            rec["updated_at"],
        ),
    )
    platform.audit.write(
        tenant,
        by_subject or _AUDIT_SUBJECT,
        False,
        "connector.upsert",
        f"connector:{source}",
        "enabled" if rec["enabled"] else "disabled",
        new_id("cfg_"),
        now_ms(),
    )
    return get(platform, tenant, source)


def list_all(platform, tenant: str) -> list[dict]:
    """Registry ∪ configured connectors, sorted by source, each with
    ``registered`` and ``declared_scopes``."""
    _guard(tenant)
    rows = {
        r["source"]: _row_to_dict(r)
        for r in platform.db.query(
            "SELECT * FROM connector_config WHERE tenant=? ORDER BY source", (tenant,)
        )
    }
    out = []
    for source in sorted(set(registry.REGISTRY) | set(rows)):
        entry = rows.get(source) or _default(source)
        entry["declared_scopes"] = declared_scopes(source)
        out.append(entry)
    return out


def is_enabled(platform, tenant: str, source: str) -> bool:
    """``True`` unless the tenant explicitly disabled the connector."""
    _guard(tenant)
    _check_source(source)
    r = platform.db.one(
        "SELECT enabled FROM connector_config WHERE tenant=? AND source=?", (tenant, source)
    )
    return True if r is None else bool(r["enabled"])


# --------------------------------------------------------------------------
# conveniences for the admin endpoints
# --------------------------------------------------------------------------
def enable(platform, tenant: str, source: str, by_subject: str = _AUDIT_SUBJECT) -> dict:
    return upsert(platform, tenant, source, enabled=True, by_subject=by_subject)


def disable(platform, tenant: str, source: str, by_subject: str = _AUDIT_SUBJECT) -> dict:
    return upsert(platform, tenant, source, enabled=False, by_subject=by_subject)


def check_scopes(platform, tenant: str, source: str) -> dict:
    """Declared vs granted scopes: ``{source, declared, granted, missing, ok}``."""
    rec = get(platform, tenant, source)
    declared = declared_scopes(source)
    granted = list(rec["scopes"]) if rec is not None else list(declared)
    missing = [s for s in declared if s not in granted]
    return {
        "source": source,
        "declared": declared,
        "granted": granted,
        "missing": missing,
        "ok": not missing,
    }


def effective_config(platform, tenant: str, source: str, base: dict | None = None) -> dict:
    """Config handed to the connector: stored admin config, overlaid with
    ``base`` (a schedule's or request's config), with the admin allow-list
    written last into the connector's allow-list key when one is set."""
    rec = get(platform, tenant, source)
    merged: dict[str, Any] = dict(rec["config"]) if rec else {}
    if base:
        if not isinstance(base, dict):
            raise TypeError("base config must be a dict")
        merged.update(base)
    if rec and rec["allow"]:
        merged[allow_key(source)] = list(rec["allow"])
    return merged
