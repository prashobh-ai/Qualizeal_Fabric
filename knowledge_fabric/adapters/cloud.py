"""Cloud (AWS-shape) adapters behind the Section-4 contracts.

The application never learns which shape it runs in: ``Platform`` asks the
factories below for an ``ObjectStore`` / ``Queue`` / database, and the
factories choose by environment::

    KF_OBJECTSTORE = local | s3          (+ KF_S3_BUCKET, KF_S3_PREFIX, AWS_REGION)
    KF_QUEUE       = local | sqs         (+ KF_SQS_URL, KF_SQS_DLQ_URL, AWS_REGION)
    KF_DB_URL      = <sqlite path> | postgres://user:pw@host:5432/db

Every adapter here implements the *same* methods as its local twin
(``adapters/objectstore.FileObjectStore``, ``adapters/queue.SqlQueue``,
``stores/db.Database``) and keeps the same invariants: originals are
immutable and content-hash addressed (I8), every call is tenant-scoped (I5),
jobs retry then dead-letter.

Client libraries are **guarded imports**. The image stays standard-library
only; when ``boto3`` (Apache-2.0) or a Postgres driver (``pg8000``, BSD-3) is
absent the adapter still constructs, and raises :class:`CloudNotReady` with
the exact ``pip install`` fix the first time it is *used*. ``scripts/doctor.py``
reports the same facts before deployment.
"""
from __future__ import annotations

import importlib.util
import json
import math
import time
from typing import Any, Callable, Optional
from urllib.parse import urlparse

from ..contracts.types import Job
from ..stores.repositories import _guard

try:                                   # guarded: the image ships without boto3
    import boto3 as _boto3             # type: ignore[import-not-found]
except ImportError:                    # pragma: no cover - exercised via monkeypatch
    _boto3 = None

BOTO3_HINT = "boto3 not installed; run: pip install boto3"
PG_HINT = "no Postgres driver installed; run: pip install pg8000"

#: Postgres drivers we accept, most permissive licence first (pg8000 is BSD-3).
PG_DRIVERS = ("pg8000", "psycopg", "psycopg2")

OBJECTSTORE_MODES = ("local", "s3")
QUEUE_MODES = ("local", "sqs")
_S3_MISSING_CODES = {"404", "NoSuchKey", "NotFound"}


class CloudNotReady(RuntimeError):
    """A cloud adapter was selected but its client library (or target) is not available."""


# --------------------------------------------------------------------------
# library discovery (monkeypatch-able for tests: ``cloud._boto3 = None``)
# --------------------------------------------------------------------------
def pg_driver() -> Optional[str]:
    """Name of the first installed Postgres driver, or ``None``."""
    for name in PG_DRIVERS:
        try:
            if importlib.util.find_spec(name) is not None:
                return name
        except (ImportError, ValueError):     # broken namespace packages etc.
            continue
    return None


def libraries() -> dict:
    """Which optional client libraries are importable right now."""
    return {"boto3": _boto3 is not None, "pg_driver": pg_driver()}


def _region(env: dict) -> Optional[str]:
    return env.get("AWS_REGION") or env.get("AWS_DEFAULT_REGION") or None


def _error_code(exc: BaseException) -> str:
    """botocore ``ClientError`` carries ``.response['Error']['Code']``; fakes may too."""
    resp = getattr(exc, "response", None) or {}
    return str((resp.get("Error") or {}).get("Code", ""))


# --------------------------------------------------------------------------
# ObjectStore: S3, content-hash addressed, immutable (I8)
# --------------------------------------------------------------------------
class S3ObjectStore:
    """``ObjectStore`` on S3. Key layout mirrors the filesystem adapter:
    ``<prefix>/<tenant>/<hash[:2]>/<hash>`` so a bucket listing is tenant-partitioned.

    ``put`` never overwrites (head first, then put) and the IAM task role in
    ``deploy/aws`` grants no ``s3:DeleteObject`` — immutability is enforced
    twice. Pass ``client`` to inject a boto3-compatible client (tests use a fake).
    """

    def __init__(self, bucket: str, region: Optional[str] = None, prefix: str = "originals",
                 client: Any = None, sse: str = "AES256"):
        if not bucket:
            raise ValueError("S3ObjectStore requires a bucket name (KF_S3_BUCKET)")
        self.bucket = bucket
        self.region = region
        self.prefix = (prefix or "").strip("/")
        self.sse = sse
        self._client = client

    # -- plumbing -----------------------------------------------------------
    def _s3(self):
        if self._client is None:
            if _boto3 is None:
                raise CloudNotReady(BOTO3_HINT)
            kw = {"region_name": self.region} if self.region else {}
            self._client = _boto3.client("s3", **kw)
        return self._client

    def key(self, tenant: str, content_hash: str) -> str:
        _guard(tenant)
        if not content_hash or not isinstance(content_hash, str):
            raise ValueError("content_hash is required")
        parts = [self.prefix, tenant, content_hash[:2], content_hash]
        return "/".join(p for p in parts if p)

    # -- contract -------------------------------------------------------------
    def put(self, tenant: str, content_hash: str, data: bytes, meta: dict) -> str:
        key = self.key(tenant, content_hash)
        if self.exists(tenant, content_hash):          # immutable: never overwrite
            return self.url(tenant, content_hash)
        metadata = {str(k): str(v) for k, v in (meta or {}).items()}
        metadata["tenant"] = tenant
        kw: dict[str, Any] = {"Bucket": self.bucket, "Key": key, "Body": data,
                              "Metadata": metadata, "ServerSideEncryption": self.sse}
        if meta and meta.get("mime"):
            kw["ContentType"] = str(meta["mime"])
        self._s3().put_object(**kw)
        return self.url(tenant, content_hash)

    def get(self, tenant: str, content_hash: str) -> bytes:
        key = self.key(tenant, content_hash)
        resp = self._s3().get_object(Bucket=self.bucket, Key=key)
        return resp["Body"].read()

    def exists(self, tenant: str, content_hash: str) -> bool:
        key = self.key(tenant, content_hash)
        try:
            self._s3().head_object(Bucket=self.bucket, Key=key)
            return True
        except Exception as exc:                       # botocore.ClientError or a fake's error
            if _error_code(exc) in _S3_MISSING_CODES:
                return False
            raise

    def url(self, tenant: str, content_hash: str) -> str:
        return f"s3://{self.bucket}/{self.key(tenant, content_hash)}"


# --------------------------------------------------------------------------
# Queue: SQS with visibility-timeout leases and a dead-letter queue
# --------------------------------------------------------------------------
class SqsQueue:
    """``Queue`` on SQS.

    * ``enqueue``  -> ``SendMessage`` (body = the job, ``tenant`` as a message attribute)
    * ``lease``    -> ``ReceiveMessage`` with ``VisibilityTimeout = ttl_s`` — an expired
      lease re-appears on its own, which is the Runbook "kill a worker mid-ingest" case
    * ``ack``      -> ``DeleteMessage``
    * ``nack``     -> ``ChangeMessageVisibility(0)`` (retry now) or dead-letter after
      ``max_attempts`` (SQS's own redrive policy in ``deploy/aws`` uses the same count)
    * ``depth``    -> ``ApproximateNumberOfMessages``

    Receipt handles are kept per job id for the life of the lease. ``ack``/``nack`` on an
    unknown id are no-ops, matching ``SqlQueue`` (an UPDATE touching zero rows).
    """

    def __init__(self, queue_url: str, region: Optional[str] = None, dlq_url: Optional[str] = None,
                 client: Any = None, max_attempts: int = 5):
        if not queue_url:
            raise ValueError("SqsQueue requires a queue URL (KF_SQS_URL)")
        self.queue_url = queue_url
        self.dlq_url = dlq_url or None
        self.region = region
        self.max_attempts = max_attempts
        self._client = client
        self._leases: dict[str, dict] = {}

    def _sqs(self):
        if self._client is None:
            if _boto3 is None:
                raise CloudNotReady(BOTO3_HINT)
            kw = {"region_name": self.region} if self.region else {}
            self._client = _boto3.client("sqs", **kw)
        return self._client

    @staticmethod
    def _attrs(tenant: str, **extra: str) -> dict:
        out = {"tenant": {"DataType": "String", "StringValue": tenant}}
        for k, v in extra.items():
            out[k] = {"DataType": "String", "StringValue": str(v)}
        return out

    def enqueue(self, tenant: str, job: Job) -> str:
        _guard(tenant)
        body = {"id": job.id, "tenant": tenant, "kind": job.kind, "payload": job.payload,
                "created_at": time.time()}
        self._sqs().send_message(QueueUrl=self.queue_url, MessageBody=json.dumps(body, default=str),
                                 MessageAttributes=self._attrs(tenant, kind=job.kind))
        return job.id

    def lease(self, worker: str, ttl_s: float = 30.0) -> Optional[Job]:
        resp = self._sqs().receive_message(
            QueueUrl=self.queue_url, MaxNumberOfMessages=1, WaitTimeSeconds=0,
            VisibilityTimeout=max(1, int(math.ceil(ttl_s))),
            AttributeNames=["ApproximateReceiveCount"], MessageAttributeNames=["All"])
        msgs = resp.get("Messages") or []
        if not msgs:
            return None
        m = msgs[0]
        body = json.loads(m["Body"])
        attempts = int((m.get("Attributes") or {}).get("ApproximateReceiveCount", 1))
        lease_until = time.time() + ttl_s
        job = Job(id=body["id"], tenant=body["tenant"], kind=body["kind"],
                  payload=body.get("payload") or {}, state="leased",
                  attempts=attempts, lease=lease_until)
        self._leases[job.id] = {"receipt": m["ReceiptHandle"], "body": body,
                                "attempts": attempts, "worker": worker}
        return job

    def ack(self, job_id: str) -> None:
        lease = self._leases.pop(job_id, None)
        if lease:
            self._sqs().delete_message(QueueUrl=self.queue_url, ReceiptHandle=lease["receipt"])

    def nack(self, job_id: str, reason: str) -> None:
        lease = self._leases.get(job_id)
        if not lease:
            return
        if lease["attempts"] >= self.max_attempts:
            self.deadletter(job_id, reason)
            return
        self._sqs().change_message_visibility(QueueUrl=self.queue_url,
                                              ReceiptHandle=lease["receipt"], VisibilityTimeout=0)
        self._leases.pop(job_id, None)

    def deadletter(self, job_id: str, reason: str) -> None:
        lease = self._leases.pop(job_id, None)
        if not lease:
            return
        if self.dlq_url:
            dead = dict(lease["body"])
            dead.update({"dead_letter_reason": reason, "attempts": lease["attempts"]})
            self._sqs().send_message(QueueUrl=self.dlq_url, MessageBody=json.dumps(dead, default=str),
                                     MessageAttributes=self._attrs(dead["tenant"], reason=reason[:256]))
        self._sqs().delete_message(QueueUrl=self.queue_url, ReceiptHandle=lease["receipt"])

    def depth(self) -> int:
        resp = self._sqs().get_queue_attributes(QueueUrl=self.queue_url,
                                                AttributeNames=["ApproximateNumberOfMessages"])
        return int((resp.get("Attributes") or {}).get("ApproximateNumberOfMessages", 0))


# --------------------------------------------------------------------------
# Database: Postgres + pgvector target notice
# --------------------------------------------------------------------------
class PostgresNotice:
    """Stand-in for the Postgres+pgvector store named by ``KF_DB_URL``.

    It exposes the same call surface as ``stores.db.Database`` (``execute`` /
    ``query`` / ``one`` / ``conn``) so it can sit in ``Platform.db``; every call
    raises :class:`CloudNotReady` naming the exact gap (driver missing, or the
    Postgres repository adapter not yet shipped in this build). ``describe()``
    redacts the password and is what the doctor prints.
    """

    def __init__(self, db_url: str):
        if not db_url or not db_url.startswith(("postgres://", "postgresql://")):
            raise ValueError("PostgresNotice expects a postgres:// or postgresql:// URL")
        self.db_url = db_url
        self._u = urlparse(db_url)

    def describe(self) -> dict:
        u = self._u
        query = dict(p.split("=", 1) for p in u.query.split("&") if "=" in p) if u.query else {}
        return {
            "engine": "postgres",
            "host": u.hostname or "", "port": u.port or 5432,
            "database": (u.path or "/").lstrip("/"), "user": u.username or "",
            "sslmode": query.get("sslmode", "prefer"),
            "driver": pg_driver(),
            "extensions_required": ["vector"],
            "redacted_url": self.redacted(),
        }

    def redacted(self) -> str:
        u = self._u
        auth = u.username or ""
        if u.password:
            auth += ":***"
        host = f"{u.hostname or ''}" + (f":{u.port}" if u.port else "")
        return f"{u.scheme}://{auth + '@' if auth else ''}{host}{u.path or ''}"

    def connect(self):
        """Return a DB-API connection using the first installed driver (pg8000 preferred)."""
        name = pg_driver()
        if name is None:
            raise CloudNotReady(PG_HINT)
        d = self.describe()
        if name == "pg8000":
            import pg8000.dbapi as drv                     # type: ignore[import-not-found]
            return drv.connect(user=d["user"], password=self._u.password or "", host=d["host"],
                               port=d["port"], database=d["database"],
                               ssl_context=(d["sslmode"] not in ("disable", "allow")) or None)
        drv = importlib.import_module(name)
        return drv.connect(self.db_url)

    # ``Database``-shaped surface: every use fails closed with the exact gap.
    def _not_ready(self):
        if pg_driver() is None:
            raise CloudNotReady(PG_HINT)
        raise CloudNotReady("Postgres store adapter is not shipped in this build; "
                            "the SQLite store is the only implemented store (docs/AWS_READINESS.md)")

    def execute(self, sql: str, params: tuple = ()):
        self._not_ready()

    def query(self, sql: str, params: tuple = ()):
        self._not_ready()

    def one(self, sql: str, params: tuple = ()):
        self._not_ready()

    def conn(self):
        self._not_ready()


# --------------------------------------------------------------------------
# factories: select by env; never construct the cloud client eagerly
# --------------------------------------------------------------------------
def _mode(env: dict, key: str, default: str, allowed: tuple) -> str:
    mode = (env.get(key) or default).strip().lower()
    if mode not in allowed:
        raise ValueError(f"unknown {key}={mode!r} (expected {'|'.join(allowed)})")
    return mode


def build_objectstore(env: dict, local_factory: Callable[[], Any]) -> Any:
    """``KF_OBJECTSTORE=local`` -> ``local_factory()``; ``s3`` -> :class:`S3ObjectStore`."""
    mode = _mode(env, "KF_OBJECTSTORE", "local", OBJECTSTORE_MODES)
    if mode == "local":
        return local_factory()
    bucket = env.get("KF_S3_BUCKET", "")
    if not bucket:
        raise ValueError("KF_OBJECTSTORE=s3 requires KF_S3_BUCKET")
    return S3ObjectStore(bucket, region=_region(env), prefix=env.get("KF_S3_PREFIX", "originals"))


def build_queue(env: dict, local_factory: Callable[[], Any]) -> Any:
    """``KF_QUEUE=local`` -> ``local_factory()``; ``sqs`` -> :class:`SqsQueue`."""
    mode = _mode(env, "KF_QUEUE", "local", QUEUE_MODES)
    if mode == "local":
        return local_factory()
    url = env.get("KF_SQS_URL", "")
    if not url:
        raise ValueError("KF_QUEUE=sqs requires KF_SQS_URL")
    return SqsQueue(url, region=_region(env), dlq_url=env.get("KF_SQS_DLQ_URL") or None)


def database_target(env: dict) -> dict:
    """Classify ``KF_DB_URL`` (falling back to ``KF_DB``) without opening anything.

    Returns ``{"engine": "sqlite", "path": ...}`` or ``{"engine": "postgres", ...describe()}``.
    """
    url = (env.get("KF_DB_URL") or "").strip()
    if not url:
        return {"engine": "sqlite", "path": env.get("KF_DB", ":memory:"), "driver": "sqlite3"}
    if url.startswith(("postgres://", "postgresql://")):
        return PostgresNotice(url).describe()
    if url.startswith("sqlite:///"):
        return {"engine": "sqlite", "path": url[len("sqlite:///"):] or ":memory:", "driver": "sqlite3"}
    if "://" in url:
        raise ValueError(f"unsupported KF_DB_URL scheme in {url.split('://', 1)[0]}:// "
                         "(expected a sqlite path or postgres://)")
    return {"engine": "sqlite", "path": url, "driver": "sqlite3"}


def build_database(env: dict, local_factory: Callable[[str], Any]) -> Any:
    """sqlite -> ``local_factory(path)``; postgres -> :class:`PostgresNotice` (fails closed on use)."""
    target = database_target(env)
    if target["engine"] == "sqlite":
        return local_factory(target["path"])
    return PostgresNotice(env["KF_DB_URL"].strip())


def selection(env: dict) -> dict:
    """What the factories *would* pick for ``env`` and exactly what is missing to make
    each choice usable — no clients constructed, no network. Used by the doctor."""
    libs = libraries()
    out: dict[str, Any] = {"libraries": libs}

    def choose(key: str, default: str, allowed: tuple) -> tuple[str, list[str]]:
        try:
            return _mode(env, key, default, allowed), []
        except ValueError as e:
            return (env.get(key) or default), [str(e)]

    os_mode, problems = choose("KF_OBJECTSTORE", "local", OBJECTSTORE_MODES)
    missing = list(problems)
    if os_mode == "s3":
        if not env.get("KF_S3_BUCKET"):
            missing.append("KF_S3_BUCKET")
        if not _region(env):
            missing.append("AWS_REGION")
        if not libs["boto3"]:
            missing.append(f"boto3 ({BOTO3_HINT.split('; ')[1]})")
    out["objectstore"] = {"mode": os_mode, "adapter": "S3ObjectStore" if os_mode == "s3" else "FileObjectStore",
                          "ready": not missing, "missing": missing}

    q_mode, problems = choose("KF_QUEUE", "local", QUEUE_MODES)
    missing = list(problems)
    if q_mode == "sqs":
        if not env.get("KF_SQS_URL"):
            missing.append("KF_SQS_URL")
        if not _region(env):
            missing.append("AWS_REGION")
        if not libs["boto3"]:
            missing.append(f"boto3 ({BOTO3_HINT.split('; ')[1]})")
    out["queue"] = {"mode": q_mode, "adapter": "SqsQueue" if q_mode == "sqs" else "SqlQueue",
                    "ready": not missing, "missing": missing}

    missing = []
    try:
        db = database_target(env)
    except ValueError as e:
        db = {"engine": "unknown"}
        missing.append(str(e))
    if db.get("engine") == "postgres":
        if not libs["pg_driver"]:
            missing.append(f"pg8000 ({PG_HINT.split('; ')[1]})")
        missing.append("Postgres store adapter (not shipped in this build; SQLite is the only store)")
    out["database"] = {"mode": db.get("engine", "unknown"), "target": db,
                       "adapter": "PostgresNotice" if db.get("engine") == "postgres" else "Database(sqlite)",
                       "ready": not missing, "missing": missing}
    out["ready"] = all(out[k]["ready"] for k in ("objectstore", "queue", "database"))
    return out
