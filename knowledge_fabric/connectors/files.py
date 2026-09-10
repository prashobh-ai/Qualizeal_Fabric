"""Files connector — the reference implementation of the canonical record
(a watched folder). Change detection is by file mtime cursor.
"""

from __future__ import annotations

from pathlib import Path

from ..contracts.types import RawItem
from .base import BaseConnector


class FilesConnector(BaseConnector):
    source_name = "files"

    def scopes(self) -> list[str]:
        return ["files:read"]

    def pull(self, cursor: str | None) -> tuple[list[RawItem], str | None]:
        folder = Path(self.config["folder"])
        allow = set(self.config.get("allow_ext", [".md", ".txt", ".csv", ".py", ".transcript"]))
        since = float(cursor) if cursor else 0.0
        items, newest = [], since
        for path in sorted(folder.glob("**/*")):
            if not path.is_file() or path.suffix not in allow:
                continue
            mtime = path.stat().st_mtime
            if mtime <= since:
                continue
            newest = max(newest, mtime)
            items.append(
                RawItem(
                    tenant=self.tenant,
                    source=self.source_name,
                    source_version=str(int(mtime)),
                    uri=f"file://{path.name}",
                    mime="text/plain",
                    title=path.stem,
                    bytes_=path.read_bytes(),
                    meta={"acl": self.config.get("acl", ["public"])},
                )
            )
        return items, str(newest)
