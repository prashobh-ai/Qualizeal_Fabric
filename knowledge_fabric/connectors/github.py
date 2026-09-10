"""GitHub connector (Section 9): repos, code, PRs, issues, comments.

Read-only, allow-listed to named repos, change-detecting by ``updated_at``
cursor, tombstoning on source deletion. Live API calls go through a fetcher;
for offline demo/tests an in-memory ``records`` list is injected so the
connector's contract (auth/pull/incremental/tombstone) is fully testable.
Provenance maps to repo/path/commit/line.
"""

from __future__ import annotations

from ..contracts.types import RawItem
from .base import BaseConnector


class GitHubConnector(BaseConnector):
    source_name = "github"

    def __init__(self, tenant: str, config: dict, records: list[dict] | None = None):
        super().__init__(tenant, config)
        # records: [{path, updated_at, content, deleted?}] — injectable for tests/offline
        self._records = records or []
        self.allow_repos = set(config.get("repos", []))
        self.last_tombstones: list[str] = []

    def scopes(self) -> list[str]:
        return ["repo:read"]  # read-only repo scope only

    def pull(self, cursor: str | None) -> tuple[list[RawItem], str | None]:
        since = int(cursor) if cursor else 0
        items, newest, tombstones = [], since, []
        for rec in sorted(self._records, key=lambda r: r["updated_at"]):
            if self.allow_repos and rec.get("repo") not in self.allow_repos:
                continue  # allow-list enforcement
            if rec["updated_at"] <= since:
                continue
            newest = max(newest, rec["updated_at"])
            if rec.get("deleted"):
                tombstones.append(f"github://{rec.get('repo', 'repo')}/{rec['path']}")
                continue
            items.append(
                RawItem(
                    tenant=self.tenant,
                    source=self.source_name,
                    source_version=str(rec.get("commit", rec["updated_at"])),
                    uri=f"github://{rec.get('repo', 'repo')}/{rec['path']}",
                    mime=rec.get("mime", "text/plain"),
                    title=rec["path"],
                    bytes_=rec["content"].encode(),
                    meta={
                        "acl": self.config.get("acl", ["public"]),
                        "tombstones": tombstones,
                        "provenance": {
                            "repo": rec.get("repo"),
                            "path": rec["path"],
                            "commit": rec.get("commit"),
                        },
                    },
                )
            )
        self.last_tombstones = tombstones
        return items, str(newest)
