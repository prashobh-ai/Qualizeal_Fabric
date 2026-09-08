"""Platform wiring.

Constructs the concrete adapters (local defaults) behind the Section 4
contracts and exposes them to ingestion, the answer service and the
surfaces. Swapping local<->cloud is a change *here* (or by env), never in
application code.
"""
from __future__ import annotations

import os

from .adapters.converter import DoclingLite
from .adapters.embedder import HashingEmbedder
from .adapters.graphstore import SqlGraphStore
from .adapters.identity import LocalIdP, StubIdentity
from .adapters.lexicalindex import SqlLexicalIndex
from .adapters.model import build_model_client
from .adapters.objectstore import FileObjectStore
from .adapters.queue import SqlQueue
from .adapters.telemetry import SqlTelemetry
from .adapters.vectorindex import SqlVectorIndex
from .answer.cache import Cache
from .governance.policy import PolicyEngine
from .stores.db import Database
from .stores.repositories import (
    AuditRepo, CurationRepo, DocumentRepo, GraphRepo, PassageRepo,
)


class Platform:
    def __init__(self, db_path: str | None = None, blob_root: str | None = None,
                 idp_secret: str | None = None):
        db_path = db_path or os.environ.get("KF_DB", ":memory:")
        blob_root = blob_root or os.environ.get("KF_BLOBS", "./data/blobs")
        idp_secret = idp_secret or os.environ.get("KF_IDP_SECRET", "local-dev-secret-change-me")

        self.db = Database(db_path)
        self.objects = FileObjectStore(blob_root)
        self.queue = SqlQueue(self.db)
        self.embedder = HashingEmbedder()
        self.vindex = SqlVectorIndex(self.db, self.embedder.model_id())
        self.lindex = SqlLexicalIndex(self.db)
        self.telemetry = SqlTelemetry(self.db)
        self.converter = DoclingLite()
        self.model = build_model_client()

        self.documents = DocumentRepo(self.db)
        self.passages = PassageRepo(self.db)
        self.graph_repo = GraphRepo(self.db)
        self.graph = SqlGraphStore(self.graph_repo)
        self.audit = AuditRepo(self.db)
        self.curation = CurationRepo(self.db)
        self.policy = PolicyEngine(self.db)

        self.idp = LocalIdP(idp_secret)
        self.stub = StubIdentity()
        self.cache = Cache()   # WS3 five-layer cache with savings ledger

        # per-tenant tunables (Section 10 grounding threshold, Section 15 gate)
        self.grounding_threshold = float(os.environ.get("KF_GROUNDING_THRESHOLD", "0.50"))

    def model_available(self) -> bool:
        return self.model.available()
