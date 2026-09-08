"""Intake: the three doors (drop-folder, upload API, CLI) that all emit the
SAME canonical record + one ``ingest.requested`` event onto the durable
queue, and the worker that drains it through the 7-step pipeline.
"""
from __future__ import annotations

import mimetypes
from pathlib import Path
from typing import Optional

from ..contracts.types import Job, RawItem, new_id
from .pipeline import IngestionPipeline


class Intake:
    def __init__(self, platform):
        self.p = platform
        self._raw_cache: dict[str, RawItem] = {}

    def canonical(self, tenant: str, source: str, uri: str, title: str, data: bytes,
                  mime: Optional[str] = None, acl: Optional[list[str]] = None,
                  source_version: str = "1", language: str = "en",
                  ontology: str = "quality-assurance") -> RawItem:
        mime = mime or mimetypes.guess_type(uri)[0] or "text/plain"
        return RawItem(tenant=tenant, source=source, source_version=source_version, uri=uri,
                       mime=mime, title=title, bytes_=data, language=language,
                       meta={"acl": acl or ["public"], "ontology": ontology})

    def submit(self, raw: RawItem) -> str:
        """Enqueue one ingest job (durable queue)."""
        job = Job(id=new_id("job_"), tenant=raw.tenant, kind="ingest",
                  payload={"uri": raw.uri, "source": raw.source})
        self._raw_cache[job.id] = raw
        self.p.queue.enqueue(raw.tenant, job)
        return job.id

    # --- door 1: watched drop folder --------------------------------
    def scan_drop_folder(self, tenant: str, folder: str, source: str = "files",
                         acl: Optional[list[str]] = None, ontology: str = "quality-assurance") -> list[str]:
        ids = []
        for path in sorted(Path(folder).glob("**/*")):
            if path.is_file():
                raw = self.canonical(tenant, source, f"file://{path.name}", path.stem,
                                     path.read_bytes(), acl=acl, ontology=ontology)
                ids.append(self.submit(raw))
        return ids

    # --- door 2: upload API (bytes) & door 3: CLI both call submit ---
    def upload(self, tenant: str, filename: str, data: bytes, acl=None,
               ontology="quality-assurance") -> str:
        return self.submit(self.canonical(tenant, "upload", f"upload://{filename}", Path(filename).stem,
                                          data, acl=acl, ontology=ontology))


class IngestWorker:
    def __init__(self, platform, intake: Intake, name: str = "worker-1"):
        self.p = platform
        self.intake = intake
        self.name = name
        self.pipeline = IngestionPipeline(platform)

    def drain(self, max_jobs: int = 1000) -> list[dict]:
        results = []
        for _ in range(max_jobs):
            job = self.p.queue.lease(self.name)
            if not job:
                break
            raw = self.intake._raw_cache.get(job.id)
            if not raw:
                self.p.queue.nack(job.id, "raw payload missing")
                continue
            try:
                res = self.pipeline.run(raw, ontology_name=raw.meta.get("ontology"))
                self.p.queue.ack(job.id)
                results.append(res)
            except Exception as e:  # pragma: no cover - defensive
                self.p.queue.nack(job.id, str(e))
        return results
