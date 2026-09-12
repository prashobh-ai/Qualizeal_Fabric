"""Configure the live sources (T97/T98) — the V1 Platform Jira board and the
CoE Confluence spaces — reproducibly and idempotently.

    python scripts/configure_sources.py [--tenant qualizeal] \
        [--board 34] [--project V1] [--confluence-spaces AICOE,ENG]

Writes only non-secret settings into the per-tenant connector config (site URL,
board id, allow-lists, 15-minute refresh); the secrets stay in the environment
(JIRA_URL/JIRA_EMAIL/JIRA_TOKEN, CONFLUENCE_URL/CONFLUENCE_EMAIL/CONFLUENCE_TOKEN).
Prints a readiness line per source so you can see what is configured and which
secrets, if any, are still missing before a sync. Confluence spaces come from
``--confluence-spaces`` or the ``CONFLUENCE_SPACES`` env var.
"""

from __future__ import annotations

import argparse
import json
import os
import sys

ROOT = os.path.abspath(os.path.dirname(os.path.dirname(__file__)))
sys.path.insert(0, ROOT)

from knowledge_fabric import fabric_data as fd  # noqa: E402
from knowledge_fabric.connectors import provisioning  # noqa: E402

TENANT = "qualizeal"


def _platform(tenant: str):
    from knowledge_fabric.app import Platform
    from knowledge_fabric.tenants import demo

    platform = Platform(
        db_path=os.environ.get("KF_DB") or ":memory:",
        blob_root=os.path.join(fd.fabric_root(), "blobs"),
    )
    demo.seed(platform, [tenant])
    return platform


def run(
    *,
    tenant: str = TENANT,
    board_id: int = provisioning.V1_BOARD_ID,
    project: str = provisioning.V1_PROJECT,
    site: str | None = None,
    confluence_spaces: list[str] | None = None,
    platform=None,
    out=print,
) -> int:
    if platform is None:
        platform = _platform(tenant)
    spaces = confluence_spaces
    if spaces is None:
        raw = os.environ.get("CONFLUENCE_SPACES", "")
        spaces = [s.strip() for s in raw.split(",") if s.strip()]

    provisioning.configure_jira_v1(platform, tenant, site=site, project=project, board_id=board_id)
    if spaces:
        provisioning.configure_confluence(platform, tenant, spaces, site=site)

    jira = provisioning.jira_ready(platform, tenant)
    conf = provisioning.confluence_ready(platform, tenant)
    out("Configured live sources for tenant " + tenant + ":")
    out("  jira        " + json.dumps(jira, default=str))
    out("  confluence  " + json.dumps(conf, default=str))
    if jira["missing_secrets"]:
        out("  → set " + ", ".join(jira["missing_secrets"]) + " to sync the V1 board")
    if not spaces:
        out("  → pass --confluence-spaces or set CONFLUENCE_SPACES to enable Confluence")
    elif conf["missing_secrets"]:
        out("  → set " + ", ".join(conf["missing_secrets"]) + " to sync Confluence")
    return 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="configure_sources", description=__doc__.splitlines()[0])
    ap.add_argument("--tenant", default=TENANT)
    ap.add_argument("--board", type=int, default=provisioning.V1_BOARD_ID)
    ap.add_argument("--project", default=provisioning.V1_PROJECT)
    ap.add_argument("--site", default=None, help="Atlassian site URL (defaults to the CoE site)")
    ap.add_argument("--confluence-spaces", default=None, help="comma-separated space keys")
    args = ap.parse_args(argv)
    spaces = None
    if args.confluence_spaces is not None:
        spaces = [s.strip() for s in args.confluence_spaces.split(",") if s.strip()]
    return run(
        tenant=args.tenant,
        board_id=args.board,
        project=args.project,
        site=args.site,
        confluence_spaces=spaces,
    )


if __name__ == "__main__":
    sys.exit(main())
