"""Jira connector (Section 9 / roadmap WS1): projects, issues, comments,
transitions, decisions — the "Jira storyboard".

Read-only, allow-listed to named projects, change-detecting by ``updated``
cursor, tombstoning on issue deletion. Live API calls go through a fetcher;
for offline demo/tests an in-memory ``records`` list is injected so the
connector contract (auth/pull/incremental/tombstone) is fully testable.
Provenance maps to project/issue/field. Permissions on the issue are mirrored
onto the canonical record's ACL so permission-before-ranking holds (I6).
"""

from __future__ import annotations

from ..contracts.types import RawItem
from .base import BaseConnector


class JiraConnector(BaseConnector):
    source_name = "jira"

    def __init__(self, tenant: str, config: dict, records: list[dict] | None = None):
        super().__init__(tenant, config)
        # records: [{project, key, summary, description, status, updated, acl?, deleted?}]
        self._records = records or []
        self.allow_projects = set(config.get("projects", []))
        self.last_tombstones: list[str] = []

    def scopes(self) -> list[str]:
        return ["jira:read"]  # read-only, no transitions written back

    def pull(self, cursor: str | None) -> tuple[list[RawItem], str | None]:
        since = int(cursor) if cursor else 0
        items, newest, tombstones = [], since, []
        for rec in sorted(self._records, key=lambda r: r["updated"]):
            if self.allow_projects and rec.get("project") not in self.allow_projects:
                continue
            if rec["updated"] <= since:
                continue
            newest = max(newest, rec["updated"])
            if rec.get("deleted"):
                tombstones.append(f"jira://{rec['project']}/{rec['key']}")
                continue
            body = (
                f"[{rec['key']}] {rec.get('summary', '')}\n\n"
                f"Status: {rec.get('status', '')}\n\n{rec.get('description', '')}"
            )
            items.append(
                RawItem(
                    tenant=self.tenant,
                    source=self.source_name,
                    source_version=str(rec["updated"]),
                    uri=f"jira://{rec['project']}/{rec['key']}",
                    mime="text/markdown",
                    title=f"{rec['key']} · {rec.get('summary', '')}",
                    bytes_=body.encode(),
                    meta={
                        "acl": rec.get("acl", self.config.get("acl", ["public"])),
                        "tombstones": tombstones,
                        "provenance": {
                            "project": rec["project"],
                            "issue": rec["key"],
                            "field": "description",
                        },
                    },
                )
            )
        self.last_tombstones = tombstones
        return items, str(newest)
