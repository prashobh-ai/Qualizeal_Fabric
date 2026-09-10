"""Generic connector SDK (Section 9). Adding a source is additive only:
subclass BaseConnector, map source records to canonical RawItems, declare
scopes. Nothing downstream (pipeline/stores/answer/surfaces) changes.

Every connector is READ-ONLY by default (I13), declares required scopes, and
supports change detection via a cursor (updated-at / etag).
"""

from __future__ import annotations

from ..contracts.types import RawItem


class BaseConnector:
    source_name = "base"
    read_only = True

    def __init__(self, tenant: str, config: dict):
        self.tenant = tenant
        self.config = config

    def scopes(self) -> list[str]:
        raise NotImplementedError

    def discover(self, config: dict) -> dict:
        return {"source": self.source_name, "scopes": self.scopes(), "read_only": self.read_only}

    def pull(self, cursor: str | None) -> tuple[list[RawItem], str | None]:
        """Return (canonical records newer than cursor, next cursor)."""
        raise NotImplementedError

    # Read-only enforcement: a write path must be explicitly enabled per tenant.
    def write_back(self, *a, **k):
        raise PermissionError(f"{self.source_name} connector is read-only (I13)")
