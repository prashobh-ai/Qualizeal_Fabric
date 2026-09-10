"""Filesystem ObjectStore keyed by content hash (I8 — originals never mutated).

Layout: <root>/<tenant>/<hash prefix>/<hash>. Writing the same hash twice is
a no-op (idempotent). The cloud adapter (S3-class) implements the identical
four methods.
"""

from __future__ import annotations

from pathlib import Path


class FileObjectStore:
    def __init__(self, root: str):
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)

    def _path(self, tenant: str, content_hash: str) -> Path:
        d = self.root / tenant / content_hash[:2]
        d.mkdir(parents=True, exist_ok=True)
        return d / content_hash

    def put(self, tenant: str, content_hash: str, data: bytes, meta: dict) -> str:
        p = self._path(tenant, content_hash)
        if not p.exists():  # immutable: never overwrite
            p.write_bytes(data)
        return str(p)

    def get(self, tenant: str, content_hash: str) -> bytes:
        return self._path(tenant, content_hash).read_bytes()

    def exists(self, tenant: str, content_hash: str) -> bool:
        return self._path(tenant, content_hash).exists()

    def url(self, tenant: str, content_hash: str) -> str:
        return f"objectstore://{tenant}/{content_hash}"
