"""Deployment readiness ("the doctor") for the local and AWS shapes.

``report(target)`` runs a fixed set of checks and returns a JSON-able dict;
``render(report)`` prints it for a terminal. ``scripts/doctor.py`` is the CLI
and the integrator serves the same dict from ``GET /admin/doctor?target=``.

Checks (each ``ok`` | ``warn`` | ``blocker`` | ``skip``):

    python     interpreter version
    env        required / recommended environment variables for the target
    adapters   what ``adapters.cloud`` would select for that env, and what is missing
    libraries  optional client libraries (boto3, a Postgres driver)
    stdlib     the application imports only the standard library (same image everywhere)
    image      deploy/Dockerfile present with HEALTHCHECK, non-root USER, EXPOSE
    iac        deploy/aws/*.tf present, HCL brackets balanced, variables declared
    secrets    no credentials committed in the repository (pattern grep)
    identity   IdP secret is not the dev default; OIDC issuer configured for AWS
    health     ``GET /health`` served by the real handler has the expected shape
    tests      the unittest suite is green (opt-in: it is slow)

Any blocker makes ``report["ok"]`` False and lists the exact gap in
``report["missing"]``. The checks never talk to AWS: readiness is about the
repository, the image and the environment, not the account.
"""
from __future__ import annotations

import ast
import datetime as _dt
import json
import os
import re
import subprocess
import sys
import tempfile
import threading
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path
from typing import Any, Optional

from ..adapters import cloud

TARGETS = ("local", "aws")
DEV_IDP_SECRET = "local-dev-secret-change-me"
IAC_FILES = ("main.tf", "variables.tf", "outputs.tf", "README.md")
IAC_REQUIRED_RESOURCES = (
    "aws_vpc", "aws_subnet", "aws_security_group", "aws_s3_bucket", "aws_sqs_queue",
    "aws_db_instance", "aws_secretsmanager_secret", "aws_cloudwatch_log_group",
    "aws_iam_role", "aws_ecs_cluster", "aws_ecs_task_definition", "aws_ecs_service",
    "aws_lb", "aws_lb_target_group", "aws_lb_listener",
)
SKIP_DIRS = {".git", "data", "__pycache__", ".venv", "venv", "node_modules", ".terraform",
             ".pytest_cache", ".mypy_cache"}

# --------------------------------------------------------------------------
# The env-var mapping local -> AWS. Single source of truth for the doctor and
# docs/AWS_READINESS.md (a test asserts the doc lists every variable here).
# --------------------------------------------------------------------------
ENV_SPEC: list[dict] = [
    {"var": "KF_DB_URL", "local": "unset (SQLite via KF_DB)", "aws": "postgres://… from Secrets Manager (terraform output db_secret_arn)",
     "required_aws": True, "secret": True},
    {"var": "KF_DB", "local": ":memory: or /data/kf.db", "aws": "unused once KF_DB_URL is postgres", "required_aws": False, "secret": False},
    {"var": "KF_OBJECTSTORE", "local": "local", "aws": "s3", "required_aws": True, "secret": False},
    {"var": "KF_S3_BUCKET", "local": "—", "aws": "terraform output s3_bucket", "required_aws": True, "secret": False},
    {"var": "KF_S3_PREFIX", "local": "—", "aws": "originals", "required_aws": False, "secret": False},
    {"var": "KF_BLOBS", "local": "./data/blobs", "aws": "unused (container scratch only)", "required_aws": False, "secret": False},
    {"var": "KF_QUEUE", "local": "local", "aws": "sqs", "required_aws": True, "secret": False},
    {"var": "KF_SQS_URL", "local": "—", "aws": "terraform output sqs_queue_url", "required_aws": True, "secret": False},
    {"var": "KF_SQS_DLQ_URL", "local": "—", "aws": "terraform output sqs_dlq_url", "required_aws": False, "secret": False},
    {"var": "AWS_REGION", "local": "—", "aws": "var.region (task env)", "required_aws": True, "secret": False},
    {"var": "KF_IDP_SECRET", "local": DEV_IDP_SECRET, "aws": "Secrets Manager (terraform output idp_secret_arn)", "required_aws": True, "secret": True},
    {"var": "KF_OIDC_ISSUER", "local": "kf-local (built-in IdP)", "aws": "Cognito user-pool issuer or var.oidc_issuer", "required_aws": True, "secret": False},
    {"var": "KF_OIDC_AUDIENCE", "local": "knowledge-fabric", "aws": "Cognito app-client id or var.oidc_audience", "required_aws": False, "secret": False},
    {"var": "KF_MODEL_MODE", "local": "mock", "aws": "hosted | self-hosted | off (var.model_mode)", "required_aws": True, "secret": False},
    {"var": "KF_MODEL_BASE_URL", "local": "—", "aws": "model gateway URL (var.model_base_url)", "required_aws": False, "secret": False},
    {"var": "KF_MODEL_API_KEY", "local": "—", "aws": "Secrets Manager (terraform output model_key_secret_arn)", "required_aws": False, "secret": True},
    {"var": "KF_MODEL_FAST", "local": "kf-mock-small", "aws": "named model per tier (optional)", "required_aws": False, "secret": False},
    {"var": "KF_MODEL_DEEP", "local": "kf-mock-mid", "aws": "named model per tier (optional)", "required_aws": False, "secret": False},
    {"var": "KF_MODEL_ESCALATION", "local": "kf-mock-large", "aws": "named model per tier (optional)", "required_aws": False, "secret": False},
    {"var": "KF_GROUNDING_THRESHOLD", "local": "0.50", "aws": "var.grounding_threshold", "required_aws": False, "secret": False},
    {"var": "KF_BUDGET_CAP_USD", "local": "—", "aws": "var.budget_cap_usd (reserved: the cap is set via POST /admin/budget today)", "required_aws": False, "secret": False},
    {"var": "KF_PORT", "local": "8080", "aws": "8080 (var.container_port)", "required_aws": False, "secret": False},
    {"var": "KF_OTLP_ENDPOINT", "local": "—", "aws": "OTLP collector endpoint (optional)", "required_aws": False, "secret": False},
]
REQUIRED_AWS = [e["var"] for e in ENV_SPEC if e["required_aws"]]
SECRET_VARS = {e["var"] for e in ENV_SPEC if e["secret"]}

# --------------------------------------------------------------------------
# Secret patterns (the literal regex text never matches itself).
# --------------------------------------------------------------------------
SECRET_PATTERNS: list[tuple[str, re.Pattern]] = [
    ("AWS access key id", re.compile(r"\bAKIA[0-9A-Z]{16}\b")),
    ("AWS secret access key", re.compile(r"(?i)aws_secret_access_key\s*[=:]\s*[\"']?[A-Za-z0-9/+=]{40}\b")),
    ("private key block", re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH |DSA |PGP )?PRIVATE KEY-----")),
    ("provider API key", re.compile(r"\bsk-(?:ant-)?[A-Za-z0-9_\-]{20,}\b")),
    ("GitHub token", re.compile(r"\bgh[pousr]_[A-Za-z0-9]{36,}\b")),
    ("Slack token", re.compile(r"\bxox[baprs]-[A-Za-z0-9\-]{10,}\b")),
    ("quoted secret literal", re.compile(r"(?i)\b(?:api[_-]?key|secret|password|token)\b\s*[=:]\s*[\"']([^\"'\s]{16,})[\"']")),
]
_PLACEHOLDER = re.compile(r"(?i)\$\{|<|>|example|change-?me|placeholder|redacted|\*\*\*|your[_-]|xxx|dummy")


def _check(id_: str, name: str, status: str, detail: str, items: Optional[list] = None) -> dict:
    return {"id": id_, "name": name, "status": status, "detail": detail, "items": list(items or [])}


def _redact(var: str, value: str) -> str:
    if var in SECRET_VARS:
        return "set (redacted)"
    return value if len(value) <= 60 else value[:57] + "..."


# --------------------------------------------------------------------------
# individual checks
# --------------------------------------------------------------------------
def check_python() -> dict:
    v = sys.version_info
    ver = f"{v.major}.{v.minor}.{v.micro}"
    if (v.major, v.minor) >= (3, 11):
        return _check("python", "Python interpreter", "ok", f"Python {ver} (deploy/Dockerfile pins python:3.11-slim)")
    return _check("python", "Python interpreter", "blocker", f"Python {ver} < 3.11", [f"Python >= 3.11 (found {ver})"])


def check_env(target: str, env: dict) -> dict:
    present = [e["var"] for e in ENV_SPEC if env.get(e["var"])]
    items: list[str] = []
    if target == "local":
        detail = (f"{len(present)}/{len(ENV_SPEC)} known KF_* variables set; every one has a local default"
                  + (": " + ", ".join(f"{v}={_redact(v, env[v])}" for v in present) if present else ""))
        if (env.get("KF_MODEL_MODE", "mock").lower() == "hosted"
                and not (env.get("KF_MODEL_BASE_URL") and env.get("KF_MODEL_API_KEY"))):
            items.append("KF_MODEL_MODE=hosted without KF_MODEL_BASE_URL/KF_MODEL_API_KEY falls back to the mock model")
        return _check("env", "Environment variables", "warn" if items else "ok", detail, items)

    # aws: every required variable must be present with the expected shape
    by_var = {e["var"]: e for e in ENV_SPEC}
    for var in REQUIRED_AWS:
        if not env.get(var):
            items.append(f"{var} (aws: {by_var[var]['aws']})")
    wrong: list[str] = []
    if env.get("KF_OBJECTSTORE") and env["KF_OBJECTSTORE"].lower() != "s3":
        wrong.append(f"KF_OBJECTSTORE must be s3 on aws (is {env['KF_OBJECTSTORE']!r})")
    if env.get("KF_QUEUE") and env["KF_QUEUE"].lower() != "sqs":
        wrong.append(f"KF_QUEUE must be sqs on aws (is {env['KF_QUEUE']!r})")
    if env.get("KF_DB_URL") and not env["KF_DB_URL"].startswith(("postgres://", "postgresql://")):
        wrong.append("KF_DB_URL must be postgres:// on aws (container storage is ephemeral)")
    if env.get("KF_MODEL_MODE", "").lower() == "hosted" and not (env.get("KF_MODEL_BASE_URL") and env.get("KF_MODEL_API_KEY")):
        wrong.append("KF_MODEL_MODE=hosted requires KF_MODEL_BASE_URL and KF_MODEL_API_KEY (Secrets Manager)")
    items.extend(wrong)
    detail = (f"{len(REQUIRED_AWS) - sum(1 for v in REQUIRED_AWS if not env.get(v))}/{len(REQUIRED_AWS)} required variables set"
              + (f"; present: {', '.join(f'{v}={_redact(v, env[v])}' for v in present)}" if present else ""))
    return _check("env", "Environment variables", "blocker" if items else "ok", detail, items)


def check_adapters(target: str, env: dict) -> dict:
    sel = cloud.selection(env)
    picked = f"objectstore={sel['objectstore']['adapter']} queue={sel['queue']['adapter']} database={sel['database']['adapter']}"
    items = [f"{k}: {m}" for k in ("objectstore", "queue", "database") for m in sel[k]["missing"]]
    if target == "local":
        cloudy = [k for k in ("objectstore", "queue", "database") if sel[k]["mode"] not in ("local", "sqlite")]
        status = "blocker" if items else ("warn" if cloudy else "ok")
        detail = picked + (" (cloud adapters selected on a local target)" if cloudy else " (local defaults)")
        return _check("adapters", "Adapter selection", status, detail, items)
    expected = {"objectstore": "s3", "queue": "sqs", "database": "postgres"}
    for k, want in expected.items():
        if sel[k]["mode"] != want:
            items.append(f"{k}: env selects {sel[k]['mode']!r}, aws needs {want!r}")
    return _check("adapters", "Adapter selection", "blocker" if items else "ok", picked, items)


def check_libraries(target: str) -> dict:
    libs = cloud.libraries()
    have = [n for n, ok in (("boto3", libs["boto3"]), (libs["pg_driver"] or "pg-driver", bool(libs["pg_driver"]))) if ok]
    detail = "installed: " + (", ".join(have) if have else "none") + " (the image is stdlib-only by default)"
    if target == "local":
        return _check("libraries", "Optional client libraries", "ok", detail + "; none needed locally")
    items = []
    if not libs["boto3"]:
        items.append(f"boto3 — {cloud.BOTO3_HINT.split('; ')[1]} (Apache-2.0)")
    if not libs["pg_driver"]:
        items.append(f"Postgres driver — {cloud.PG_HINT.split('; ')[1]} (BSD-3)")
    return _check("libraries", "Optional client libraries", "blocker" if items else "ok", detail, items)


def check_stdlib(repo_root: Path) -> dict:
    """Prove the application imports only the standard library (unguarded imports)."""
    stdlib = set(sys.stdlib_module_names) | {"__future__"}
    offenders: list[str] = []
    pkg = repo_root / "knowledge_fabric"
    for path in sorted(pkg.rglob("*.py")):
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"))
        except (SyntaxError, UnicodeDecodeError) as e:
            offenders.append(f"{path.relative_to(repo_root)}: unparsable ({e.__class__.__name__})")
            continue
        for node in tree.body:                       # module top level only; try/except = guarded
            names: list[str] = []
            if isinstance(node, ast.Import):
                names = [a.name.split(".")[0] for a in node.names]
            elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
                names = [node.module.split(".")[0]]
            for n in names:
                if n not in stdlib and n != "knowledge_fabric":
                    offenders.append(f"{path.relative_to(repo_root)}: import {n}")
    detail = f"{len(list(pkg.rglob('*.py')))} modules scanned; unguarded third-party imports: {len(offenders)}"
    return _check("stdlib", "Standard-library-only image", "warn" if offenders else "ok", detail, offenders)


def check_image(repo_root: Path) -> dict:
    df = repo_root / "deploy" / "Dockerfile"
    if not df.exists():
        return _check("image", "Container image build file", "blocker", "deploy/Dockerfile missing",
                      ["deploy/Dockerfile"])
    text = df.read_text(encoding="utf-8")
    facts, items = [], []
    m = re.search(r"^FROM\s+(\S+)", text, re.M)
    facts.append(f"FROM {m.group(1)}" if m else "no FROM")
    for key, label in (("HEALTHCHECK", "HEALTHCHECK"), ("EXPOSE", "EXPOSE"), ("USER", "non-root USER")):
        if re.search(rf"^{key}\b", text, re.M):
            facts.append(label)
        else:
            items.append(f"Dockerfile lacks {label}")
    if re.search(r"^USER\s+root\b", text, re.M):
        items.append("Dockerfile runs as root")
    compose = repo_root / "deploy" / "compose" / "docker-compose.yml"
    facts.append("compose file present" if compose.exists() else "no compose file")
    status = "warn" if items else "ok"
    return _check("image", "Container image build file", status, "deploy/Dockerfile: " + ", ".join(facts), items)


# ---- HCL helpers (no terraform binary available: a structural self-check) ----
def hcl_problems(text: str) -> list[str]:
    """Bracket balance for HCL, aware of comments, strings, ``${}`` interpolation, heredocs."""
    problems: list[str] = []
    n = len(text)
    pairs = {"}": "{", "]": "[", ")": "("}
    stack: list[tuple[str, int]] = []

    def scan_string(i: int, line: int) -> tuple[int, int]:
        i += 1
        while i < n:
            c = text[i]
            if c == "\\":
                i += 2
                continue
            if c == "$" and text[i + 1:i + 2] == "{":
                i, line = scan_interp(i + 2, line)
                continue
            if c == "\n":
                line += 1
            if c == '"':
                return i + 1, line
            i += 1
        problems.append(f"line {line}: unterminated string")
        return i, line

    def scan_interp(i: int, line: int) -> tuple[int, int]:
        depth = 1
        while i < n and depth:
            c = text[i]
            if c == '"':
                i, line = scan_string(i, line)
                continue
            if c == "{":
                depth += 1
            elif c == "}":
                depth -= 1
            elif c == "\n":
                line += 1
            i += 1
        return i, line

    i, line = 0, 1
    while i < n:
        c = text[i]
        two = text[i:i + 2]
        if c == "#" or two == "//":
            while i < n and text[i] != "\n":
                i += 1
            continue
        if two == "/*":
            end = text.find("*/", i + 2)
            line += text.count("\n", i, end if end >= 0 else n)
            i = n if end < 0 else end + 2
            continue
        if c == '"':
            i, line = scan_string(i, line)
            continue
        if two == "<<":
            m = re.match(r"<<-?([A-Za-z_][A-Za-z0-9_]*)\n", text[i:])
            if m:
                marker = m.group(1)
                j = i + m.end()
                while j < n:
                    eol = text.find("\n", j)
                    eol = n if eol < 0 else eol
                    line += 1
                    if text[j:eol].strip() == marker:
                        j = eol
                        break
                    j = eol + 1
                i = j
                continue
        if c in "{[(":
            stack.append((c, line))
        elif c in "}])":
            if not stack or stack[-1][0] != pairs[c]:
                problems.append(f"line {line}: unexpected '{c}'")
            else:
                stack.pop()
        if c == "\n":
            line += 1
        i += 1
    for c, ln in stack:
        problems.append(f"line {ln}: unclosed '{c}'")
    return problems


def tf_variable_problems(iac_dir: Path) -> tuple[list[str], list[str]]:
    """(undeclared ``var.x`` references, declared-but-unused variables)."""
    declared: set[str] = set()
    vf = iac_dir / "variables.tf"
    if vf.exists():
        declared = set(re.findall(r'^variable\s+"([A-Za-z0-9_]+)"', vf.read_text(encoding="utf-8"), re.M))
    used: set[str] = set()
    for tf in iac_dir.glob("*.tf"):
        used |= set(re.findall(r"\bvar\.([A-Za-z0-9_]+)", tf.read_text(encoding="utf-8")))
    return sorted(used - declared), sorted(declared - used)


def check_iac(target: str, repo_root: Path) -> dict:
    iac = repo_root / "deploy" / "aws"
    missing = [f for f in IAC_FILES if not (iac / f).exists()]
    if missing:
        status = "blocker" if target == "aws" else "warn"
        return _check("iac", "Infrastructure as code (deploy/aws)", status,
                      f"missing {', '.join(missing)}", [f"deploy/aws/{f}" for f in missing])
    items: list[str] = []
    for tf in sorted(iac.glob("*.tf")):
        for p in hcl_problems(tf.read_text(encoding="utf-8")):
            items.append(f"{tf.name}: {p}")
    undeclared, unused = tf_variable_problems(iac)
    items += [f"var.{v} referenced but not declared in variables.tf" for v in undeclared]
    main = (iac / "main.tf").read_text(encoding="utf-8")
    types = set(re.findall(r'^resource\s+"([a-z0-9_]+)"', main, re.M))
    absent = [r for r in IAC_REQUIRED_RESOURCES if r not in types]
    items += [f"main.tf lacks resource {r}" for r in absent]
    for tf in sorted(iac.glob("*.tf")):
        for m in re.finditer(r'(?im)^\s*(password|secret_string|api_key|[a-z_]*secret)\s*=\s*"([^"$]{8,})"', tf.read_text(encoding="utf-8")):
            items.append(f"{tf.name}: plaintext secret literal for {m.group(1)}")
    warn_only = [f"unused variable var.{v}" for v in unused]
    detail = (f"{len(list(iac.glob('*.tf')))} .tf files, {len(types)} resource types, "
              f"{len(re.findall(r'^variable ', (iac / 'variables.tf').read_text(encoding='utf-8'), re.M))} variables, "
              f"{len(re.findall(r'^output ', (iac / 'outputs.tf').read_text(encoding='utf-8'), re.M))} outputs; "
              "structural HCL check only (no terraform binary)")
    if items:
        return _check("iac", "Infrastructure as code (deploy/aws)", "blocker" if target == "aws" else "warn", detail, items)
    return _check("iac", "Infrastructure as code (deploy/aws)", "warn" if warn_only else "ok", detail, warn_only)


def scan_secrets(repo_root: Path, max_bytes: int = 2_000_000) -> list[str]:
    hits: list[str] = []
    for dirpath, dirnames, filenames in os.walk(repo_root):
        dirnames[:] = sorted(d for d in dirnames if d not in SKIP_DIRS)
        for fn in sorted(filenames):
            p = Path(dirpath) / fn
            if fn.endswith((".db", ".db-wal", ".db-shm", ".pyc", ".png", ".jpg", ".gif", ".pdf", ".zip")):
                continue
            try:
                if p.stat().st_size > max_bytes:
                    continue
                raw = p.read_bytes()
            except OSError:
                continue
            if b"\x00" in raw[:1024]:
                continue
            text = raw.decode("utf-8", errors="ignore")
            for label, pat in SECRET_PATTERNS:
                for m in pat.finditer(text):
                    if label == "quoted secret literal" and _PLACEHOLDER.search(m.group(1)):
                        continue
                    ln = text.count("\n", 0, m.start()) + 1
                    hits.append(f"{p.relative_to(repo_root)}:{ln}: {label}")
                    break                                   # one hit per pattern per file is enough
    return hits


def check_secrets(target: str, repo_root: Path) -> dict:
    hits = scan_secrets(repo_root)
    items = list(hits)
    gi = repo_root / ".gitignore"
    ignored = gi.read_text(encoding="utf-8").splitlines() if gi.exists() else []
    for needed in (".env", "data/"):
        if needed not in [l.strip() for l in ignored]:
            items.append(f".gitignore does not list {needed}")
    dotenv = repo_root / ".env"
    if dotenv.exists() and dotenv.stat().st_size > 0:
        items.append(".env file present in the working tree" + (" (ignored by git)" if ".env" in ignored else " and NOT ignored"))
    status = "blocker" if hits or (target == "aws" and items) else ("warn" if items else "ok")
    detail = f"{len(SECRET_PATTERNS)} credential patterns grepped across the repository; hits: {len(hits)}"
    return _check("secrets", "No secrets committed", status, detail, items)


def check_identity(target: str, env: dict) -> dict:
    secret = env.get("KF_IDP_SECRET", DEV_IDP_SECRET)
    issuer = env.get("KF_OIDC_ISSUER", "")
    items: list[str] = []
    if secret == DEV_IDP_SECRET:
        items.append("KF_IDP_SECRET is the development default" + ("" if target == "local" else " — inject from Secrets Manager"))
    elif len(secret) < 32:
        items.append("KF_IDP_SECRET shorter than 32 characters")
    if target == "aws":
        if not issuer:
            items.append("KF_OIDC_ISSUER unset (Cognito user-pool issuer or corporate OIDC)")
        elif not issuer.startswith("https://"):
            items.append(f"KF_OIDC_ISSUER must be an https:// issuer (is {issuer!r})")
        status = "blocker" if items else "ok"
        detail = "HS256 local IdP is replaced by the OIDC issuer; token secret from Secrets Manager"
    else:
        status = "warn" if items else "ok"
        detail = "built-in HS256 IdP (real signed tokens, no cloud IAM)"
    return _check("identity", "Identity & token secret", status, detail, items)


class _QuietServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True


def probe_health(timeout_s: float = 10.0) -> tuple[int, dict]:
    """Serve the real ``/health`` handler on an ephemeral loopback port and fetch it."""
    from ..surfaces import http_api
    from ..app import Platform
    from ..answer.service import AnswerService
    from ..tenants import demo

    if http_api._platform is None:                          # seed a throwaway in-memory platform
        p = Platform(db_path=":memory:", blob_root=tempfile.mkdtemp(prefix="kf-doctor-"))
        demo.seed(p, ["q-quality"])
        http_api._platform, http_api._svc = p, AnswerService(p)
    srv = _QuietServer(("127.0.0.1", 0), http_api.Handler)
    port = srv.server_address[1]
    t = threading.Thread(target=srv.serve_forever, kwargs={"poll_interval": 0.05}, daemon=True)
    t.start()
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/health", timeout=timeout_s) as r:
            return r.status, json.loads(r.read().decode("utf-8"))
    finally:
        srv.shutdown()
        srv.server_close()


def check_health(live: bool, repo_root: Path) -> dict:
    if not live:
        src = repo_root / "knowledge_fabric" / "surfaces" / "http_api.py"
        has = src.exists() and '"/health"' in src.read_text(encoding="utf-8")
        return _check("health", "Health endpoint", "ok" if has else "blocker",
                      "static: /health route present in surfaces/http_api.py (live probe skipped)",
                      [] if has else ["GET /health route"])
    try:
        status, body = probe_health()
    except Exception as e:                                   # pragma: no cover - environment dependent
        return _check("health", "Health endpoint", "blocker", f"live probe failed: {e.__class__.__name__}: {e}",
                      ["GET /health must answer 200 on the container port"])
    items = []
    if status != 200:
        items.append(f"GET /health returned {status}")
    if body.get("status") != "ok":
        items.append("body.status != 'ok'")
    if not isinstance(body.get("model"), bool):
        items.append("body.model is not a boolean")
    if not isinstance(body.get("connectors"), list):
        items.append("body.connectors is not a list")
    detail = f"live GET /health -> {status} {json.dumps(body, sort_keys=True)}"
    return _check("health", "Health endpoint", "blocker" if items else "ok", detail, items)


def check_tests(repo_root: Path, run: bool, timeout_s: float = 900.0) -> dict:
    if not run:
        return _check("tests", "Test suite", "skip", "not run (--skip-tests); run: PYTHONPATH=. python3 -m unittest discover -s tests -p 'test_*.py'")
    env = dict(os.environ)
    env["PYTHONPATH"] = str(repo_root) + (os.pathsep + env["PYTHONPATH"] if env.get("PYTHONPATH") else "")
    env.setdefault("KF_MODEL_MODE", "mock")
    cmd = [sys.executable, "-m", "unittest", "discover", "-s", "tests", "-p", "test_*.py"]
    try:
        proc = subprocess.run(cmd, cwd=str(repo_root), env=env, capture_output=True, text=True, timeout=timeout_s)
    except subprocess.TimeoutExpired:
        return _check("tests", "Test suite", "blocker", f"timed out after {timeout_s:.0f}s", ["test suite must finish"])
    out = proc.stderr + proc.stdout
    m = re.search(r"Ran (\d+) tests? in ([0-9.]+)s", out)
    ran = f"{m.group(1)} tests in {m.group(2)}s" if m else "no summary line"
    if proc.returncode == 0 and "OK" in out:
        return _check("tests", "Test suite", "ok", f"green: {ran}")
    tail = "\n".join(out.strip().splitlines()[-5:])
    return _check("tests", "Test suite", "blocker", f"FAILED: {ran}", [f"test suite red (exit {proc.returncode}): {tail}"])


# --------------------------------------------------------------------------
# report + render
# --------------------------------------------------------------------------
def default_repo_root() -> Path:
    return Path(__file__).resolve().parents[2]


def report(target: str = "local", repo_root: Optional[os.PathLike] = None, env: Optional[dict] = None,
           run_tests: bool = False, live_health: bool = True) -> dict:
    """Run every check for ``target`` and return the readiness report."""
    if target not in TARGETS:
        raise ValueError(f"unknown target {target!r} (expected {'|'.join(TARGETS)})")
    root = Path(repo_root).resolve() if repo_root else default_repo_root()
    env = dict(os.environ) if env is None else dict(env)

    checks = [
        check_python(),
        check_env(target, env),
        check_adapters(target, env),
        check_libraries(target),
        check_stdlib(root),
        check_image(root),
        check_iac(target, root),
        check_secrets(target, root),
        check_identity(target, env),
        check_health(live_health, root),
        check_tests(root, run_tests),
    ]
    summary = {s: sum(1 for c in checks if c["status"] == s) for s in ("ok", "warn", "blocker", "skip")}
    missing = [it for c in checks if c["status"] == "blocker" for it in (c["items"] or [c["detail"]])]
    mapping = [{"var": e["var"], "local": e["local"], "aws": e["aws"], "required_aws": e["required_aws"],
                "present": bool(env.get(e["var"])), "value": _redact(e["var"], env[e["var"]]) if env.get(e["var"]) else None}
               for e in ENV_SPEC]
    return {
        "target": target,
        "generated_at": _dt.datetime.now(_dt.timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z"),
        "repo_root": str(root),
        "python": f"{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}",
        "image": "deploy/Dockerfile (single image for every shape)",
        "adapters": cloud.selection(env),
        "env_mapping": mapping,
        "checks": checks,
        "missing": missing,
        "summary": summary,
        "ok": summary["blocker"] == 0,
    }


_TAG = {"ok": "[ OK ]", "warn": "[WARN]", "blocker": "[FAIL]", "skip": "[SKIP]"}


def render(rep: dict) -> str:
    lines = [f"Knowledge Fabric readiness — target: {rep['target']}",
             f"repo: {rep['repo_root']} · python {rep['python']} · {rep['generated_at']}", ""]
    for c in rep["checks"]:
        lines.append(f"{_TAG[c['status']]} {c['id']:<10} {c['detail']}")
        for it in c["items"]:
            lines.append(f"           - {it}")
    a = rep["adapters"]
    lines += ["", f"adapters: objectstore={a['objectstore']['adapter']} ({a['objectstore']['mode']}) "
                  f"queue={a['queue']['adapter']} ({a['queue']['mode']}) database={a['database']['adapter']}"]
    if rep["missing"]:
        lines += ["", f"Missing for {rep['target']} ({len(rep['missing'])}):"]
        lines += [f"  - {m}" for m in rep["missing"]]
    s = rep["summary"]
    verdict = "READY" if rep["ok"] else "NOT READY"
    lines += ["", f"Result: {verdict} — {s['blocker']} blocker(s), {s['warn']} warning(s), {s['ok']} ok, {s['skip']} skipped"]
    return "\n".join(lines)
