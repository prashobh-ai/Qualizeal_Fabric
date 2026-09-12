"""T97/T98 — reproducible source configuration for the AI CoE site.

The live Jira and Confluence connectors are already real (``jira_live`` /
``confluence``). What remained was to *configure* them for QualiZeal's V1
Platform board and the CoE Confluence spaces — in a form that is committed,
auditable and re-appliable, not click-ops.

These helpers upsert the per-tenant ``connector_config`` with the documented
settings and allow-lists through :mod:`connectors.admin` (so every change is
audited). The **secrets** — ``JIRA_TOKEN`` / ``CONFLUENCE_TOKEN`` and the two
emails — stay in the environment and are never written here; only the non-secret
settings (site URL, board id, project/space allow-lists, refresh interval) are
stored. ``scripts/configure_sources.py`` applies them and reports readiness.

Overridable defaults keep the V1 board reproducible while allowing a different
site or board for another tenant.
"""

from __future__ import annotations

import os

from . import admin

# The AI CoE Atlassian site and the V1 Platform board (T97). Overridable by env
# so the same code configures a different site/board without an edit.
COE_SITE = "https://qualizeal-team-aicoe.atlassian.net"
V1_PROJECT = "V1"
V1_BOARD_ID = 34
JIRA_INTERVAL = "15m"

#: env vars that carry the secrets (never stored in connector_config)
JIRA_SECRET_ENV = ("JIRA_URL", "JIRA_EMAIL", "JIRA_TOKEN")
CONFLUENCE_SECRET_ENV = ("CONFLUENCE_URL", "CONFLUENCE_EMAIL", "CONFLUENCE_TOKEN")

__all__ = [
    "COE_SITE",
    "V1_PROJECT",
    "V1_BOARD_ID",
    "JIRA_INTERVAL",
    "configure_jira_v1",
    "configure_confluence",
    "jira_ready",
    "confluence_ready",
]


def _missing(env_names: tuple[str, ...]) -> list[str]:
    return [n for n in env_names if not os.environ.get(n)]


def configure_jira_v1(
    platform,
    tenant: str,
    *,
    site: str | None = None,
    project: str = V1_PROJECT,
    board_id: int = V1_BOARD_ID,
    interval: str = JIRA_INTERVAL,
    by_subject: str = "system",
) -> dict:
    """Configure ``jira_live`` for the V1 Platform board: site URL, board id, a
    15-minute refresh, and the ``[project]`` allow-list. ``sprint_field`` is left
    unset so the connector resolves it from the board configuration at sync time.
    Returns the stored connector record (audited)."""
    config = {"url": site or COE_SITE, "board_id": int(board_id), "interval": interval}
    return admin.upsert(
        platform,
        tenant,
        "jira_live",
        enabled=True,
        config=config,
        allow=[project],
        by_subject=by_subject,
    )


def configure_confluence(
    platform,
    tenant: str,
    spaces: list[str],
    *,
    site: str | None = None,
    by_subject: str = "system",
) -> dict:
    """Configure ``confluence`` for the CoE spaces: the site's ``/wiki`` URL and
    the ``spaces`` allow-list. Pages ingest as documents with paragraph citations;
    facts write page counts per space. Returns the stored record (audited)."""
    base = (site or COE_SITE).rstrip("/")
    config = {"url": base if base.endswith("/wiki") else base + "/wiki"}
    return admin.upsert(
        platform,
        tenant,
        "confluence",
        enabled=True,
        config=config,
        allow=list(spaces),
        by_subject=by_subject,
    )


def jira_ready(platform, tenant: str) -> dict:
    """Whether the V1 board can sync now: config present, allow-list set, and the
    three secrets available in the environment. Never reveals a secret's value."""
    cfg = admin.effective_config(platform, tenant, "jira_live", {})
    missing = _missing(JIRA_SECRET_ENV)
    return {
        "source": "jira_live",
        "configured": bool(cfg.get("url") and cfg.get("projects")),
        "projects": list(cfg.get("projects") or []),
        "board_id": cfg.get("board_id"),
        "interval": cfg.get("interval"),
        "missing_secrets": missing,
        "ready": bool(cfg.get("projects")) and not missing,
    }


def confluence_ready(platform, tenant: str) -> dict:
    """Whether Confluence can sync now: config present, spaces allow-listed, and
    the three secrets available in the environment."""
    cfg = admin.effective_config(platform, tenant, "confluence", {})
    missing = _missing(CONFLUENCE_SECRET_ENV)
    return {
        "source": "confluence",
        "configured": bool(cfg.get("url") and cfg.get("spaces")),
        "spaces": list(cfg.get("spaces") or []),
        "missing_secrets": missing,
        "ready": bool(cfg.get("spaces")) and not missing,
    }
