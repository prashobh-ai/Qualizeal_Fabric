"""Durable transactional queue backed by the SQLite ``jobs`` table.

Supports lease/ack/nack, retry with a max-attempts dead-letter, and a
lease-expiry sweep so a job whose worker died is re-leased and completed
(Runbook Section 8 "kill a worker mid-ingest"). The cloud adapter is a
managed queue (SQS-class) behind the same methods.
"""
from __future__ import annotations

import json
import time
from typing import Optional

from ..contracts.types import Job
from ..stores.db import Database

MAX_ATTEMPTS = 5


class SqlQueue:
    def __init__(self, db: Database):
        self.db = db

    def enqueue(self, tenant: str, job: Job) -> str:
        self.db.execute(
            """INSERT INTO jobs(id,tenant,kind,payload,state,attempts,lease,dead_letter_reason,created_at)
               VALUES(?,?,?,?,?,?,?,?,?)""",
            (job.id, tenant, job.kind, json.dumps(job.payload), "pending", 0, None, None, time.time()))
        return job.id

    def _reclaim_expired(self) -> None:
        now = time.time()
        self.db.execute(
            "UPDATE jobs SET state='pending', lease=NULL WHERE state='leased' AND lease IS NOT NULL AND lease < ?",
            (now,))

    def lease(self, worker: str, ttl_s: float = 30.0) -> Optional[Job]:
        with self.db._lock:
            self._reclaim_expired()
            r = self.db.one(
                "SELECT * FROM jobs WHERE state='pending' ORDER BY created_at LIMIT 1")
            if not r:
                return None
            lease_until = time.time() + ttl_s
            self.db.execute(
                "UPDATE jobs SET state='leased', lease=?, attempts=attempts+1 WHERE id=? AND state='pending'",
                (lease_until, r["id"]))
            return Job(id=r["id"], tenant=r["tenant"], kind=r["kind"],
                       payload=json.loads(r["payload"]), state="leased",
                       attempts=r["attempts"] + 1, lease=lease_until)

    def ack(self, job_id: str) -> None:
        self.db.execute("UPDATE jobs SET state='done', lease=NULL WHERE id=?", (job_id,))

    def nack(self, job_id: str, reason: str) -> None:
        r = self.db.one("SELECT attempts FROM jobs WHERE id=?", (job_id,))
        if r and r["attempts"] >= MAX_ATTEMPTS:
            self.deadletter(job_id, reason)
        else:
            self.db.execute("UPDATE jobs SET state='pending', lease=NULL WHERE id=?", (job_id,))

    def deadletter(self, job_id: str, reason: str) -> None:
        self.db.execute("UPDATE jobs SET state='dead', lease=NULL, dead_letter_reason=? WHERE id=?",
                        (reason, job_id))

    def depth(self) -> int:
        r = self.db.one("SELECT COUNT(*) c FROM jobs WHERE state='pending'")
        return r["c"] if r else 0
