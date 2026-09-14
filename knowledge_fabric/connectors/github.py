"""GitHub connector (Section 9): repos, code, PRs, issues, comments.

Read-only, allow-listed to named repos, change-detecting by ``updated_at``
cursor, tombstoning on source deletion. Live API calls go through a fetcher;
for offline demo/tests an in-memory ``records`` list is injected so the
connector's contract (auth/pull/incremental/tombstone) is fully testable.
Provenance maps to repo/path/commit/line.
"""

from __future__ import annotations

import re

from ..contracts.types import RawItem
from .base import BaseConnector

# T141 — directories and file patterns never ingested from any repo: a repo's own
# tests, CI config and generated docs are engineering artefacts, not the knowledge
# a reader asks about. Excluding them keeps a test function from ever winning an
# answer over the organisation's real documents.
_EXCLUDED_DIRS = ("tests", "test", "__tests__", "spec", ".github", "docs/progress")
_EXCLUDED_FILE = re.compile(
    r"(^|/)(test_[^/]+\.py|[^/]+_test\.py|[^/]+\.spec\.[^/]+|conftest\.py)$"
)


def _is_excluded_path(path: str) -> bool:
    p = (path or "").replace("\\", "/").strip("/")
    if not p:
        return False
    if _EXCLUDED_FILE.search(p):
        return True
    segments = p.split("/")
    for d in _EXCLUDED_DIRS:
        parts = d.split("/")
        # match the excluded directory as a path prefix or any nested segment run
        for i in range(len(segments) - len(parts) + 1):
            if segments[i : i + len(parts)] == parts:
                return True
    return False


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
            if _is_excluded_path(rec.get("path", "")):
                continue  # T141 — never ingest a repo's tests / CI / docs into the fabric
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
