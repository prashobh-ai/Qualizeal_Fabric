"""Stage-2 Section E: cloud adapters, env-driven selection, the readiness doctor, IaC.

No network, no boto3: the S3/SQS adapters are exercised with in-memory fakes that
speak the boto3 client dialect, and the "library missing" path is forced by
monkeypatching ``cloud._boto3``.
"""
from __future__ import annotations

import contextlib
import io
import json
import os
import re
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from knowledge_fabric.adapters import cloud
from knowledge_fabric.adapters.cloud import (
    CloudNotReady, PostgresNotice, S3ObjectStore, SqsQueue,
    build_database, build_objectstore, build_queue, database_target, selection,
)
from knowledge_fabric.adapters.objectstore import FileObjectStore
from knowledge_fabric.adapters.queue import SqlQueue
from knowledge_fabric.app import Platform
from knowledge_fabric.contracts.interfaces import ObjectStore, Queue
from knowledge_fabric.contracts.types import Job
from knowledge_fabric.ops import readiness

REPO = Path(__file__).resolve().parents[1]
T = "qualizeal"
OTHER = "isolation-check"

# A complete, well-shaped AWS environment (no real endpoints are contacted).
AWS_ENV = {
    "KF_DB_URL": "postgresql://fabric:s3cr3t-pw@kf-qualizeal-db.abc.eu-west-1.rds.amazonaws.com:5432/fabric?sslmode=require",
    "KF_OBJECTSTORE": "s3", "KF_S3_BUCKET": "kf-qualizeal-originals-123456789012", "KF_S3_PREFIX": "originals",
    "KF_QUEUE": "sqs", "KF_SQS_URL": "https://sqs.eu-west-1.amazonaws.com/123456789012/kf-qualizeal-ingest",
    "KF_SQS_DLQ_URL": "https://sqs.eu-west-1.amazonaws.com/123456789012/kf-qualizeal-ingest-dlq",
    "AWS_REGION": "eu-west-1",
    "KF_IDP_SECRET": "a" * 48,
    "KF_OIDC_ISSUER": "https://cognito-idp.eu-west-1.amazonaws.com/eu-west-1_abc123",
    "KF_MODEL_MODE": "off",
}
# The process-level KF_* variables that must not leak into env-driven tests.
KF_KEYS = [e["var"] for e in readiness.ENV_SPEC] + ["AWS_DEFAULT_REGION"]


def clean_env(**overrides: str) -> dict:
    env = {k: v for k, v in os.environ.items() if k not in KF_KEYS}
    env.update(overrides)
    return env


# ==========================================================================
# fakes speaking the boto3 client dialect
# ==========================================================================
class _ClientError(Exception):
    def __init__(self, code: str, op: str):
        super().__init__(f"An error occurred ({code}) when calling the {op} operation")
        self.response = {"Error": {"Code": code}}


class FakeS3:
    def __init__(self):
        self.objects: dict[tuple[str, str], dict] = {}
        self.calls: list[str] = []

    def head_object(self, Bucket, Key):
        self.calls.append("head")
        if (Bucket, Key) not in self.objects:
            raise _ClientError("404", "HeadObject")
        return {"ContentLength": len(self.objects[(Bucket, Key)]["Body"])}

    def put_object(self, **kw):
        self.calls.append("put")
        self.objects[(kw["Bucket"], kw["Key"])] = kw

    def get_object(self, Bucket, Key):
        self.calls.append("get")
        if (Bucket, Key) not in self.objects:
            raise _ClientError("NoSuchKey", "GetObject")
        return {"Body": io.BytesIO(self.objects[(Bucket, Key)]["Body"])}


class FakeSqs:
    """One queue per URL; visibility timeouts are honoured logically, not by clock."""

    def __init__(self):
        self.queues: dict[str, list[dict]] = {}
        self.invisible: dict[str, str] = {}      # receipt -> queue url (in flight)
        self._n = 0

    def _q(self, url):
        return self.queues.setdefault(url, [])

    def send_message(self, QueueUrl, MessageBody, MessageAttributes=None):
        self._n += 1
        self._q(QueueUrl).append({"MessageId": f"m{self._n}", "Body": MessageBody,
                                  "Attributes": {"ApproximateReceiveCount": "0"},
                                  "MessageAttributes": MessageAttributes or {}, "_visible": True})
        return {"MessageId": f"m{self._n}"}

    def receive_message(self, QueueUrl, MaxNumberOfMessages=1, WaitTimeSeconds=0,
                        VisibilityTimeout=30, AttributeNames=None, MessageAttributeNames=None):
        assert VisibilityTimeout >= 1
        for m in self._q(QueueUrl):
            if m["_visible"]:
                m["_visible"] = False
                m["Attributes"]["ApproximateReceiveCount"] = str(int(m["Attributes"]["ApproximateReceiveCount"]) + 1)
                self._n += 1
                m["ReceiptHandle"] = f"r{self._n}"
                self.invisible[m["ReceiptHandle"]] = QueueUrl
                return {"Messages": [dict(m)]}
        return {}

    def _find(self, QueueUrl, ReceiptHandle):
        for m in self._q(QueueUrl):
            if m.get("ReceiptHandle") == ReceiptHandle:
                return m
        raise _ClientError("ReceiptHandleIsInvalid", "DeleteMessage")

    def delete_message(self, QueueUrl, ReceiptHandle):
        m = self._find(QueueUrl, ReceiptHandle)
        self._q(QueueUrl).remove(m)
        self.invisible.pop(ReceiptHandle, None)

    def change_message_visibility(self, QueueUrl, ReceiptHandle, VisibilityTimeout):
        m = self._find(QueueUrl, ReceiptHandle)
        if VisibilityTimeout == 0:
            m["_visible"] = True
            self.invisible.pop(ReceiptHandle, None)

    def get_queue_attributes(self, QueueUrl, AttributeNames):
        return {"Attributes": {"ApproximateNumberOfMessages": str(sum(1 for m in self._q(QueueUrl) if m["_visible"]))}}


# ==========================================================================
# factories: selection by env
# ==========================================================================
class TestFactories(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="kf-cloud-")

    def test_defaults_are_local(self):
        store = build_objectstore({}, lambda: FileObjectStore(self.tmp))
        self.assertIsInstance(store, FileObjectStore)
        q = build_queue({}, lambda: "local-queue")
        self.assertEqual(q, "local-queue")
        self.assertEqual(database_target({}), {"engine": "sqlite", "path": ":memory:", "driver": "sqlite3"})
        self.assertEqual(build_database({"KF_DB": "/x/kf.db"}, lambda path: ("sqlite", path)), ("sqlite", "/x/kf.db"))

    def test_s3_and_sqs_selected_by_env_without_touching_network(self):
        env = {"KF_OBJECTSTORE": "S3", "KF_S3_BUCKET": "b", "KF_S3_PREFIX": "/orig/", "AWS_REGION": "eu-west-1",
               "KF_QUEUE": "sqs", "KF_SQS_URL": "https://sqs/q", "KF_SQS_DLQ_URL": "https://sqs/dlq"}
        store = build_objectstore(env, lambda: self.fail("local factory must not be called"))
        self.assertIsInstance(store, S3ObjectStore)
        self.assertEqual((store.bucket, store.prefix, store.region), ("b", "orig", "eu-west-1"))
        q = build_queue(env, lambda: self.fail("local factory must not be called"))
        self.assertIsInstance(q, SqsQueue)
        self.assertEqual((q.queue_url, q.dlq_url, q.region), ("https://sqs/q", "https://sqs/dlq", "eu-west-1"))
        # constructing never builds a client (that would need boto3 + credentials)
        self.assertIsNone(store._client)
        self.assertIsNone(q._client)

    def test_cloud_adapters_satisfy_the_frozen_contracts(self):
        self.assertIsInstance(S3ObjectStore("b"), ObjectStore)
        self.assertIsInstance(SqsQueue("https://sqs/q"), Queue)
        self.assertIsInstance(FileObjectStore(self.tmp), ObjectStore)

    def test_missing_coordinates_and_unknown_modes_fail_at_startup(self):
        with self.assertRaisesRegex(ValueError, "KF_S3_BUCKET"):
            build_objectstore({"KF_OBJECTSTORE": "s3"}, lambda: None)
        with self.assertRaisesRegex(ValueError, "KF_SQS_URL"):
            build_queue({"KF_QUEUE": "sqs"}, lambda: None)
        with self.assertRaisesRegex(ValueError, "KF_OBJECTSTORE"):
            build_objectstore({"KF_OBJECTSTORE": "gcs"}, lambda: None)
        with self.assertRaisesRegex(ValueError, "KF_QUEUE"):
            build_queue({"KF_QUEUE": "kafka"}, lambda: None)
        with self.assertRaisesRegex(ValueError, "unsupported KF_DB_URL scheme"):
            database_target({"KF_DB_URL": "mysql://h/db"})

    def test_database_url_forms(self):
        self.assertEqual(database_target({"KF_DB_URL": "sqlite:////data/kf.db"})["path"], "/data/kf.db")
        self.assertEqual(database_target({"KF_DB_URL": "./data/kf.db"})["path"], "./data/kf.db")
        pg = database_target({"KF_DB_URL": AWS_ENV["KF_DB_URL"]})
        self.assertEqual(pg["engine"], "postgres")
        self.assertEqual((pg["host"], pg["port"], pg["database"], pg["user"], pg["sslmode"]),
                         ("kf-qualizeal-db.abc.eu-west-1.rds.amazonaws.com", 5432, "fabric", "fabric", "require"))
        self.assertIn("vector", pg["extensions_required"])
        self.assertNotIn("s3cr3t-pw", json.dumps(pg))            # password never leaves the adapter
        db = build_database({"KF_DB_URL": AWS_ENV["KF_DB_URL"]}, lambda path: self.fail("sqlite factory must not run"))
        self.assertIsInstance(db, PostgresNotice)
        self.assertEqual(db.redacted(), "postgresql://fabric:***@kf-qualizeal-db.abc.eu-west-1.rds.amazonaws.com:5432/fabric")

    def test_selection_reports_exactly_what_is_missing(self):
        with mock.patch.object(cloud, "_boto3", None), mock.patch.object(cloud, "pg_driver", lambda: None):
            sel = selection({"KF_OBJECTSTORE": "s3", "KF_QUEUE": "sqs", "KF_DB_URL": AWS_ENV["KF_DB_URL"]})
        self.assertFalse(sel["ready"])
        self.assertEqual(sel["objectstore"]["missing"], ["KF_S3_BUCKET", "AWS_REGION", "boto3 (run: pip install boto3)"])
        self.assertEqual(sel["queue"]["missing"], ["KF_SQS_URL", "AWS_REGION", "boto3 (run: pip install boto3)"])
        self.assertEqual(sel["database"]["missing"][0], "pg8000 (run: pip install pg8000)")
        self.assertIn("not shipped", sel["database"]["missing"][1])
        # the compact view /health publishes: adapter names only, no targets, no secrets
        self.assertEqual(sel["selected"], {"objectstore": "S3ObjectStore", "queue": "SqsQueue", "database": "PostgresNotice"})
        self.assertNotIn("s3cr3t-pw", json.dumps(sel))
        local = selection({})
        self.assertTrue(local["ready"])
        self.assertEqual(local["selected"], {"objectstore": "FileObjectStore", "queue": "SqlQueue", "database": "Database(sqlite)"})


# ==========================================================================
# CloudNotReady: the guarded import fails closed on USE, not on construction
# ==========================================================================
class TestCloudNotReady(unittest.TestCase):
    def test_s3_without_boto3(self):
        with mock.patch.object(cloud, "_boto3", None):
            store = S3ObjectStore("b", region="eu-west-1")          # constructs fine
            with self.assertRaises(CloudNotReady) as cm:
                store.put(T, "ab" * 32, b"x", {})
            self.assertEqual(str(cm.exception), "boto3 not installed; run: pip install boto3")
            with self.assertRaises(CloudNotReady):
                store.exists(T, "ab" * 32)
            self.assertTrue(issubclass(CloudNotReady, RuntimeError))

    def test_sqs_without_boto3(self):
        with mock.patch.object(cloud, "_boto3", None):
            q = SqsQueue("https://sqs/q")
            with self.assertRaisesRegex(CloudNotReady, "pip install boto3"):
                q.enqueue(T, Job(id="j1", tenant=T, kind="ingest", payload={}))
            with self.assertRaisesRegex(CloudNotReady, "pip install boto3"):
                q.depth()
            self.assertIsNone(q.ack("unknown"))                      # no-op, never touches a client

    def test_postgres_notice_fails_closed_naming_the_gap(self):
        n = PostgresNotice(AWS_ENV["KF_DB_URL"])
        with mock.patch.object(cloud, "pg_driver", lambda: None):
            with self.assertRaisesRegex(CloudNotReady, "pip install pg8000"):
                n.query("select 1")
            with self.assertRaisesRegex(CloudNotReady, "pip install pg8000"):
                n.connect()
        with mock.patch.object(cloud, "pg_driver", lambda: "pg8000"):
            with self.assertRaisesRegex(CloudNotReady, "Postgres store adapter is not shipped"):
                n.execute("select 1")
        with self.assertRaises(ValueError):
            PostgresNotice("sqlite:///x.db")


# ==========================================================================
# S3ObjectStore against a fake client: contract parity with FileObjectStore
# ==========================================================================
class TestS3ObjectStore(unittest.TestCase):
    def setUp(self):
        self.s3 = FakeS3()
        self.store = S3ObjectStore("bkt", region="eu-west-1", prefix="originals", client=self.s3)
        self.h = "9f" + "0" * 62

    def test_key_layout_is_tenant_partitioned(self):
        self.assertEqual(self.store.key(T, self.h), f"originals/{T}/9f/{self.h}")
        self.assertEqual(self.store.url(T, self.h), f"s3://bkt/originals/{T}/9f/{self.h}")
        self.assertEqual(S3ObjectStore("bkt", prefix="", client=self.s3).key(T, self.h), f"{T}/9f/{self.h}")

    def test_every_call_is_tenant_guarded(self):
        for bad in ("", None):
            with self.assertRaises(PermissionError):
                self.store.put(bad, self.h, b"x", {})
            with self.assertRaises(PermissionError):
                self.store.get(bad, self.h)
            with self.assertRaises(PermissionError):
                self.store.exists(bad, self.h)
            with self.assertRaises(PermissionError):
                self.store.url(bad, self.h)
        with self.assertRaises(ValueError):
            self.store.key(T, "")

    def test_put_get_exists_and_immutability(self):
        self.assertFalse(self.store.exists(T, self.h))
        url = self.store.put(T, self.h, b"original", {"mime": "text/plain", "title": "Spec"})
        self.assertEqual(url, self.store.url(T, self.h))
        self.assertTrue(self.store.exists(T, self.h))
        self.assertEqual(self.store.get(T, self.h), b"original")
        stored = self.s3.objects[("bkt", self.store.key(T, self.h))]
        self.assertEqual(stored["ServerSideEncryption"], "AES256")
        self.assertEqual(stored["ContentType"], "text/plain")
        self.assertEqual(stored["Metadata"]["tenant"], T)
        # I8: a second put with the same hash is a no-op — the original is never overwritten
        puts_before = self.s3.calls.count("put")
        self.store.put(T, self.h, b"tampered", {})
        self.assertEqual(self.s3.calls.count("put"), puts_before)
        self.assertEqual(self.store.get(T, self.h), b"original")
        # tenant isolation: the same hash under another tenant is a different object
        self.assertFalse(self.store.exists(OTHER, self.h))

    def test_non_404_errors_propagate(self):
        class Angry:
            def head_object(self, **kw):
                raise _ClientError("403", "HeadObject")
        with self.assertRaises(_ClientError):
            S3ObjectStore("bkt", client=Angry()).exists(T, self.h)


# ==========================================================================
# SqsQueue against a fake client: lease / ack / nack / dead-letter parity with SqlQueue
# ==========================================================================
class TestSqsQueue(unittest.TestCase):
    def setUp(self):
        self.sqs = FakeSqs()
        self.q = SqsQueue("https://sqs/q", region="eu-west-1", dlq_url="https://sqs/dlq",
                          client=self.sqs, max_attempts=3)

    def job(self, i="j1"):
        return Job(id=i, tenant=T, kind="ingest", payload={"document_id": "d1"})

    def test_enqueue_lease_ack(self):
        self.assertEqual(self.q.enqueue(T, self.job()), "j1")
        self.assertEqual(self.q.depth(), 1)
        leased = self.q.lease("w1", ttl_s=2.5)
        self.assertEqual((leased.id, leased.tenant, leased.kind, leased.state, leased.attempts), ("j1", T, "ingest", "leased", 1))
        self.assertEqual(leased.payload, {"document_id": "d1"})
        self.assertIsNone(self.q.lease("w2"))                        # in flight: invisible to others
        self.q.ack("j1")
        self.assertEqual(self.q.depth(), 0)
        self.assertIsNone(self.q.lease("w1"))
        body = json.loads(self.sqs.queues["https://sqs/q"][0]["Body"]) if self.sqs.queues["https://sqs/q"] else None
        self.assertIsNone(body)

    def test_enqueue_is_tenant_guarded_and_tags_the_message(self):
        with self.assertRaises(PermissionError):
            self.q.enqueue("", self.job())
        self.q.enqueue(T, self.job())
        m = self.sqs.queues["https://sqs/q"][0]
        self.assertEqual(m["MessageAttributes"]["tenant"]["StringValue"], T)
        self.assertEqual(json.loads(m["Body"])["tenant"], T)

    def test_nack_retries_then_dead_letters(self):
        self.q.enqueue(T, self.job())
        for attempt in (1, 2):
            j = self.q.lease("w1")
            self.assertEqual(j.attempts, attempt)
            self.q.nack("j1", "converter crashed")
            self.assertEqual(self.q.depth(), 1)                      # back on the queue
        j = self.q.lease("w1")
        self.assertEqual(j.attempts, 3)
        self.q.nack("j1", "converter crashed")                       # third failure -> DLQ
        self.assertEqual(self.q.depth(), 0)
        dead = self.sqs.queues["https://sqs/dlq"]
        self.assertEqual(len(dead), 1)
        body = json.loads(dead[0]["Body"])
        self.assertEqual((body["id"], body["dead_letter_reason"], body["attempts"]), ("j1", "converter crashed", 3))
        self.assertEqual(dead[0]["MessageAttributes"]["tenant"]["StringValue"], T)
        self.assertIsNone(self.q.lease("w1"))

    def test_explicit_deadletter_and_unknown_ids_are_noops(self):
        self.q.enqueue(T, self.job())
        self.q.lease("w1")
        self.q.deadletter("j1", "poison")
        self.assertEqual(len(self.sqs.queues["https://sqs/dlq"]), 1)
        self.assertEqual(self.q.depth(), 0)
        self.q.ack("nope"); self.q.nack("nope", "x"); self.q.deadletter("nope", "x")   # SqlQueue parity

    def test_max_attempts_matches_the_iac_redrive_policy(self):
        self.assertEqual(SqsQueue("https://sqs/q").max_attempts, 5)
        main = (REPO / "deploy" / "aws" / "main.tf").read_text(encoding="utf-8")
        self.assertRegex(main, r"maxReceiveCount\s*=\s*5")


# ==========================================================================
# Platform: the SAME code picks adapters by env (app.py wiring)
# ==========================================================================
class TestPlatformEnvSelection(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="kf-plat-")

    def test_local_by_default(self):
        with mock.patch.dict(os.environ, clean_env(KF_MODEL_MODE="mock"), clear=True):
            p = Platform(db_path=":memory:", blob_root=self.tmp)
        self.assertIsInstance(p.objects, FileObjectStore)
        self.assertIsInstance(p.queue, SqlQueue)
        self.assertEqual(p.db.path, ":memory:")

    def test_s3_and_sqs_by_env_and_fail_closed_without_boto3(self):
        env = clean_env(KF_MODEL_MODE="mock", KF_OBJECTSTORE="s3", KF_S3_BUCKET="bkt", KF_QUEUE="sqs",
                        KF_SQS_URL="https://sqs/q", AWS_REGION="eu-west-1")
        with mock.patch.dict(os.environ, env, clear=True), mock.patch.object(cloud, "_boto3", None):
            p = Platform(db_path=":memory:", blob_root=self.tmp)
            self.assertIsInstance(p.objects, S3ObjectStore)
            self.assertIsInstance(p.queue, SqsQueue)
            self.assertEqual(p.db.path, ":memory:")                  # store still sqlite: KF_DB_URL unset
            self.assertEqual(p.documents.list(T), [])                # sqlite store works as before
            with self.assertRaisesRegex(CloudNotReady, "pip install boto3"):
                p.objects.put(T, "ab" * 32, b"x", {})
            with self.assertRaisesRegex(CloudNotReady, "pip install boto3"):
                p.queue.enqueue(T, Job(id="j", tenant=T, kind="ingest", payload={}))

    def test_postgres_url_selects_the_notice_and_every_store_call_fails_closed(self):
        env = clean_env(KF_MODEL_MODE="mock", KF_DB_URL=AWS_ENV["KF_DB_URL"])
        with mock.patch.dict(os.environ, env, clear=True):
            p = Platform(db_path=":memory:", blob_root=self.tmp)
        self.assertIsInstance(p.db, PostgresNotice)
        self.assertIsInstance(p.queue, SqlQueue)                     # local queue sits on the (notice) db
        with self.assertRaises(CloudNotReady):
            p.documents.list(T)
        with self.assertRaises(CloudNotReady):
            p.audit.write(T, "asha", False, "ask", "q", "allow", "t", 0)
        self.assertNotIn("s3cr3t-pw", json.dumps(p.db.describe()))

    def test_sqlite_url_form_is_honoured(self):
        path = os.path.join(self.tmp, "kf.db")
        with mock.patch.dict(os.environ, clean_env(KF_MODEL_MODE="mock", KF_DB_URL=f"sqlite:///{path}"), clear=True):
            p = Platform(blob_root=self.tmp)
        self.assertEqual(p.db.path, path)
        self.assertTrue(os.path.exists(path))


# ==========================================================================
# readiness report + doctor CLI
# ==========================================================================
class TestReadiness(unittest.TestCase):
    def test_local_target_passes_on_a_clean_checkout(self):
        rep = readiness.report(target="local", env={"KF_MODEL_MODE": "mock"}, run_tests=False, live_health=False)
        self.assertTrue(rep["ok"], rep["missing"])
        self.assertEqual(rep["summary"]["blocker"], 0)
        ids = [c["id"] for c in rep["checks"]]
        self.assertEqual(ids, ["python", "env", "adapters", "libraries", "stdlib", "image", "iac",
                               "secrets", "identity", "health", "tests"])
        by = {c["id"]: c for c in rep["checks"]}
        self.assertEqual(by["iac"]["status"], "ok", by["iac"])
        self.assertEqual(by["secrets"]["status"], "ok", by["secrets"])
        self.assertEqual(by["stdlib"]["status"], "ok", by["stdlib"])
        self.assertEqual(by["image"]["status"], "ok", by["image"])
        self.assertEqual(by["identity"]["status"], "warn")          # dev secret is a warning locally
        self.assertEqual(by["tests"]["status"], "skip")
        self.assertEqual(rep["adapters"]["selected"]["objectstore"], "FileObjectStore")
        self.assertIn("READY", readiness.render(rep))
        self.assertNotIn("NOT READY", readiness.render(rep))

    def test_aws_target_with_empty_env_lists_every_required_variable(self):
        with mock.patch.object(cloud, "_boto3", None), mock.patch.object(cloud, "pg_driver", lambda: None):
            rep = readiness.report(target="aws", env={}, run_tests=False, live_health=False)
        self.assertFalse(rep["ok"])
        by = {c["id"]: c for c in rep["checks"]}
        self.assertEqual(by["env"]["status"], "blocker")
        for var in readiness.REQUIRED_AWS:
            self.assertTrue(any(it.startswith(var + " ") for it in by["env"]["items"]), var)
        self.assertEqual(by["adapters"]["status"], "blocker")
        self.assertEqual(by["libraries"]["status"], "blocker")
        self.assertEqual(by["identity"]["status"], "blocker")
        self.assertEqual(by["iac"]["status"], "ok")
        missing = "\n".join(rep["missing"])
        for needle in ("KF_DB_URL", "KF_S3_BUCKET", "KF_SQS_URL", "KF_OIDC_ISSUER", "pip install boto3",
                       "pip install pg8000", "aws needs 's3'", "aws needs 'sqs'", "aws needs 'postgres'"):
            self.assertIn(needle, missing)
        self.assertIn("NOT READY", readiness.render(rep))

    def test_aws_target_with_full_env_and_libraries_leaves_only_the_postgres_store_gap(self):
        with mock.patch.object(cloud, "_boto3", object()), mock.patch.object(cloud, "pg_driver", lambda: "pg8000"):
            rep = readiness.report(target="aws", env=dict(AWS_ENV), run_tests=False, live_health=False)
        by = {c["id"]: c for c in rep["checks"]}
        for cid in ("python", "env", "libraries", "stdlib", "image", "iac", "secrets", "identity", "health"):
            self.assertEqual(by[cid]["status"], "ok", (cid, by[cid]))
        self.assertEqual(by["adapters"]["status"], "blocker")
        self.assertEqual(rep["missing"], ["database: Postgres store adapter (not shipped in this build; SQLite is the only store)"])
        # secrets are redacted everywhere in the report
        dumped = json.dumps(rep)
        self.assertNotIn("s3cr3t-pw", dumped)
        self.assertNotIn("a" * 48, dumped)
        env_map = {m["var"]: m for m in rep["env_mapping"]}
        self.assertEqual(env_map["KF_DB_URL"]["value"], "set (redacted)")
        self.assertEqual(env_map["KF_S3_BUCKET"]["value"], AWS_ENV["KF_S3_BUCKET"])

    def test_aws_env_shape_checks(self):
        env = dict(AWS_ENV, KF_OBJECTSTORE="local", KF_QUEUE="local", KF_DB_URL="/tmp/kf.db",
                   KF_OIDC_ISSUER="http://insecure", KF_IDP_SECRET=readiness.DEV_IDP_SECRET, KF_MODEL_MODE="hosted")
        rep = readiness.report(target="aws", env=env, run_tests=False, live_health=False)
        by = {c["id"]: c for c in rep["checks"]}
        items = "\n".join(by["env"]["items"] + by["identity"]["items"])
        for needle in ("KF_OBJECTSTORE must be s3", "KF_QUEUE must be sqs", "KF_DB_URL must be postgres://",
                       "KF_MODEL_MODE=hosted requires", "must be an https:// issuer", "development default"):
            self.assertIn(needle, items)

    def test_unknown_target_rejected(self):
        with self.assertRaises(ValueError):
            readiness.report(target="azure")

    def test_live_health_probe_has_the_expected_shape(self):
        status, body = readiness.probe_health()
        self.assertEqual(status, 200)
        self.assertEqual(body["status"], "ok")
        self.assertIsInstance(body["model"], bool)
        self.assertIsInstance(body["connectors"], list)
        self.assertEqual(set(body["adapters"]), {"objectstore", "queue", "database"})
        check = readiness.check_health(True, REPO)
        self.assertEqual(check["status"], "ok", check)

    def test_secret_scan_finds_a_planted_credential_and_ignores_placeholders(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            (root / "ok.py").write_text('KEY = "${SECRET_FROM_ENV}"\nidp = "local-dev-secret-change-me"\n')
            self.assertEqual(readiness.scan_secrets(root), [])
            (root / "leak.env").write_text("AWS_ACCESS_KEY_ID=AKIA" + "Q" * 16 + "\n")
            hits = readiness.scan_secrets(root)
            self.assertEqual(hits, ["leak.env:1: AWS access key id"])
        self.assertEqual(readiness.scan_secrets(REPO), [])            # this repository is clean


class TestDoctorCli(unittest.TestCase):
    def run_doctor(self, *args: str) -> subprocess.CompletedProcess:
        env = clean_env(KF_MODEL_MODE="mock", PYTHONPATH=str(REPO))
        return subprocess.run([sys.executable, str(REPO / "scripts" / "doctor.py"), *args],
                              cwd=str(REPO), env=env, capture_output=True, text=True, timeout=120)

    def test_local_passes(self):
        r = self.run_doctor("--target", "local", "--skip-tests", "--no-live-health")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("Result: READY", r.stdout)
        self.assertIn("[ OK ] iac", r.stdout)

    def test_aws_lists_exactly_what_is_missing_and_exits_nonzero(self):
        r = self.run_doctor("--target", "aws", "--skip-tests", "--no-live-health", "--json")
        self.assertEqual(r.returncode, 1, r.stdout + r.stderr)
        rep = json.loads(r.stdout)
        self.assertEqual(rep["target"], "aws")
        self.assertFalse(rep["ok"])
        self.assertIn("Missing for aws", readiness.render(rep))
        # doctor.py defaults KF_MODEL_MODE=mock itself, so every other required var must be listed
        for var in (v for v in readiness.REQUIRED_AWS if v != "KF_MODEL_MODE"):
            self.assertTrue(any(m.startswith(var + " ") for m in rep["missing"]), var)
        self.assertFalse(any(m.startswith("deploy/aws/") for m in rep["missing"]))

    def test_bad_target_is_a_usage_error(self):
        r = self.run_doctor("--target", "gcp")
        self.assertEqual(r.returncode, 2)

    def test_main_is_importable_for_the_admin_endpoint(self):
        import importlib.util
        spec = importlib.util.spec_from_file_location("kf_doctor", REPO / "scripts" / "doctor.py")
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            code = mod.main(["--target", "local", "--skip-tests", "--no-live-health", "--json"])
        self.assertEqual(code, 0)
        self.assertTrue(json.loads(out.getvalue())["ok"])


# ==========================================================================
# IaC: structural validity, least privilege, env parity with the doctor spec
# ==========================================================================
class TestIaC(unittest.TestCase):
    IAC = REPO / "deploy" / "aws"

    def read(self, name: str) -> str:
        return (self.IAC / name).read_text(encoding="utf-8")

    def test_all_module_files_present_and_balanced(self):
        for name in readiness.IAC_FILES:
            self.assertTrue((self.IAC / name).exists(), name)
        for tf in sorted(self.IAC.glob("*.tf")):
            self.assertEqual(readiness.hcl_problems(tf.read_text(encoding="utf-8")), [], tf.name)
        undeclared, unused = readiness.tf_variable_problems(self.IAC)
        self.assertEqual(undeclared, [])
        self.assertEqual(unused, [])

    def test_hcl_checker_catches_real_problems(self):
        self.assertEqual(readiness.hcl_problems('a = "x${var.y}" # {\nb = [1, {c = "}"}]\nd = <<EOT\n{ not hcl\nEOT\n'), [])
        self.assertTrue(readiness.hcl_problems('resource "x" "y" {\n  a = [1, 2\n}\n'))
        self.assertTrue(readiness.hcl_problems('a = "unterminated\n'))

    def test_required_resources_and_no_plaintext_secrets(self):
        check = readiness.check_iac("aws", REPO)
        self.assertEqual(check["status"], "ok", check)
        main = self.read("main.tf")
        types = set(re.findall(r'^resource\s+"([a-z0-9_]+)"', main, re.M))
        for r in readiness.IAC_REQUIRED_RESOURCES + ("aws_cognito_user_pool", "aws_appautoscaling_policy",
                                                     "aws_budgets_budget", "aws_iam_role_policy"):
            self.assertIn(r, types)

    def test_task_role_is_least_privilege(self):
        main = self.read("main.tf")
        task = main[main.index('data "aws_iam_policy_document" "task"'):main.index('resource "aws_iam_role_policy" "task"')]
        self.assertNotIn("DeleteObject", task)                       # I8: originals immutable
        self.assertNotIn('"*"', task)                                # no wildcard actions/resources
        self.assertIn("s3:PutObject", task)
        self.assertIn("sqs:ReceiveMessage", task)
        self.assertIn("originals/*", task)
        self.assertIn("enable_execute_command             = false", main)
        self.assertIn("publicly_accessible    = false", main)
        self.assertIn('"rds.force_ssl"', main)
        self.assertIn("block_public_acls       = true", main)

    def test_task_env_matches_the_doctor_spec_and_outputs(self):
        main = self.read("main.tf")
        app_env = main[main.index("app_env = {"):main.index("container_secrets = concat(")]
        injected = set(re.findall(r"^\s*([A-Z][A-Z0-9_]+)\s*=", app_env, re.M))
        secrets = set(re.findall(r'name = "([A-Z][A-Z0-9_]+)", valueFrom', main))
        spec = {e["var"] for e in readiness.ENV_SPEC}
        self.assertTrue(injected <= spec, injected - spec)
        self.assertTrue(secrets <= spec, secrets - spec)
        self.assertEqual(secrets, readiness.SECRET_VARS)
        self.assertTrue(set(readiness.REQUIRED_AWS) <= injected | secrets,
                        set(readiness.REQUIRED_AWS) - (injected | secrets))
        # every `terraform output X` the spec/doc cites exists in outputs.tf
        outputs = set(re.findall(r'^output\s+"([a-z0-9_]+)"', self.read("outputs.tf"), re.M))
        cited = set(re.findall(r"terraform output ([a-z0-9_]+)", json.dumps(readiness.ENV_SPEC)))
        self.assertTrue(cited <= outputs, cited - outputs)
        for must in ("s3_bucket", "sqs_queue_url", "sqs_dlq_url", "db_secret_arn", "idp_secret_arn",
                     "model_key_secret_arn", "oidc_issuer", "alb_dns_name", "log_group", "task_role_arn", "app_env"):
            self.assertIn(must, outputs)

    def test_readme_documents_the_operator_path(self):
        readme = self.read("README.md")
        for needle in ("tofu apply", "doctor.py --target aws", "least privilege", "Secrets Manager", "outputs.tf"):
            self.assertIn(needle.lower(), readme.lower(), needle)


# ==========================================================================
# docs/AWS_READINESS.md: the checklist carries every env var and statuses
# ==========================================================================
class TestReadinessDoc(unittest.TestCase):
    def test_doc_lists_every_env_var_with_status_legend(self):
        doc = (REPO / "docs" / "AWS_READINESS.md").read_text(encoding="utf-8")
        for e in readiness.ENV_SPEC:
            self.assertIn(f"`{e['var']}`", doc, e["var"])
        for mark in ("✅", "🟡", "⬜"):
            self.assertIn(mark, doc)
        for needle in ("12-factor", "Stateless", "Health checks", "Secrets", "IAM least privilege", "Logging",
                       "Scaling", "DR / backups", "Cost guardrails", "doctor.py --target aws", "CloudNotReady"):
            self.assertIn(needle, doc, needle)
        # the doc tells the truth about the one unshipped piece
        self.assertIn("Postgres + pgvector store** | ⬜", doc)


if __name__ == "__main__":
    unittest.main()
