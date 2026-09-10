"""Showcase builder (F8.1) — the interactive product on GitHub Pages.

Builds ``dist/showcase/`` — a static site that runs the REAL product surfaces
(Workspace, Admin, Curator, Sign-in, Telemetry) with no server. It does this by:

1. seeding an in-memory fabric with the self-contained demo corpus and driving
   a realistic spread of questions across roles (so telemetry, analytics and
   usage are populated);
2. starting the real HTTP handler in-process and capturing the actual JSON that
   every endpoint returns, per role, into ``snapshot.json``;
3. emitting each surface's real HTML into its own folder, with ``/static``
   asset paths rewritten to relative and the browser-side ``engine.js`` injected
   before the runtime — the engine answers every ``fetch`` from the snapshot;
4. writing a brand landing page (explainability + a chat widget) at the root.

Zero third-party dependencies; standard library + the project only. Relative
asset paths, ``.nojekyll`` at the root, no external URLs — so it serves under
any base path (the Pages repo path, localhost, an AWS subpath).

Usage:  python scripts/build_showcase.py --out dist/showcase
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
import threading
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer

ROOT = os.path.abspath(os.path.dirname(os.path.dirname(__file__)))
sys.path.insert(0, ROOT)
os.environ.setdefault("KF_MODEL_MODE", "mock")  # deterministic, no network

from knowledge_fabric.answer.service import AnswerService  # noqa: E402
from knowledge_fabric.evaluation import bank as qbank  # noqa: E402
from knowledge_fabric.surfaces import http_api  # noqa: E402
from knowledge_fabric.surfaces.admin_ui import ADMIN_HTML  # noqa: E402
from knowledge_fabric.surfaces.ask_ui import ASK_HTML  # noqa: E402
from knowledge_fabric.surfaces.curator_ui import CURATOR_HTML  # noqa: E402
from knowledge_fabric.surfaces.dashboard import DASHBOARD_HTML  # noqa: E402
from knowledge_fabric.surfaces.signin_ui import SIGNIN_HTML  # noqa: E402
from knowledge_fabric.tenants import demo  # noqa: E402

BRAND_SRC = os.path.join(ROOT, "knowledge_fabric", "surfaces", "static", "assets", "brand")
ENGINE_SRC = os.path.join(ROOT, "scripts", "showcase", "engine.js")
LANDING_SRC = os.path.join(ROOT, "scripts", "showcase", "landing.html")
TENANT = "qualizeal"

# Real knowledge: the curated QualiZeal product/service briefs, vendored from the
# GitHub source they came from. Ingested through the live 7-step pipeline at build
# time so the fabric answers real questions from day one (products, services, the
# company) and the corpus counts, galaxy and citations are genuine.
CORPUS_DIR = os.path.join(ROOT, "corpus")
CORPUS_SOURCE = "github"
CORPUS_REPO = "prashobh-ai/Knowledge-Fabric"
DOCX_MIME = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"

# A realistic run over the real corpus so analytics / usage / cache have
# something to show — QualiZeal products, services and company knowledge.
SCRIPT = [
    ("asker.public", "what is QMentisAI?"),
    ("asker.public", "what does ValidAIte do?"),
    ("asker.public", "what is NexaAI?"),
    ("asker.public", "what is QMentisAI?"),  # cache hit
    ("curator", "what does QualiZeal offer for performance testing?"),
    ("curator", "what is QualiZeal's approach to security testing?"),
    ("asker.public", "what does QualiZeal offer for test automation?"),
    (
        "asker.restricted",
        "what is QMentisAI and how does it use generative AI for quality engineering?",
    ),
    ("asker.public", "qu'est-ce que QMentisAI?"),  # FR
    ("asker.public", "¿qué es ValidAIte?"),  # ES
    ("qa-agent", "what does QualiZeal offer for AI and ML model testing?"),
    ("asker.public", "what is the capital of France?"),  # gap (out of corpus)
    ("curator", "what is QualiCentral?"),
]

# Extra questions to bake answers for (so the chatbot / Workspace answer freely).
EXTRA_Q = [
    "what is QMentisAI?",
    "what does ValidAIte do?",
    "what is NexaAI?",
    "what is QualiCentral?",
    "what does QualiZeal offer for performance testing?",
    "what is QualiZeal's approach to security testing?",
    "what does QualiZeal offer for test automation?",
    "what does QualiZeal offer for AI and ML model testing?",
    "what is QMentisAI and how does it use generative AI for quality engineering?",
]

ROLES = ["asker.public", "asker.restricted", "curator", "admin", "qa-agent"]
# GET endpoints to capture per bucket (askers use only the /api/* set).
ASKER_GETS = ["/api/corpus"]
CURATOR_GETS = ["/curator/quality", "/curator/gaps", "/curator/documents", "/admin/sources"]
ADMIN_GETS = [
    "/admin/users",
    "/admin/runs?limit=12",
    "/admin/audit?limit=40",
    "/admin/sources",
    "/admin/connectors",
    "/admin/budget",
    "/admin/authority",
    "/curator/quality",
    "/curator/gaps",
    "/curator/documents",
]


def _load_corpus(p):
    """Ingest the vendored QualiZeal .docx corpus through the real 7-step
    pipeline, tagged as the GitHub source it came from. Provenance is
    ``github://prashobh-ai/Knowledge-Fabric/docs_source/<file>`` so the Admin
    console shows GitHub as the origin; the reference converter reads .docx with
    the standard library (no engine needed).
    """
    import glob
    import re

    from knowledge_fabric.ingestion.intake import IngestWorker, Intake

    intake, worker = Intake(p), IngestWorker(p, None)
    worker.intake = intake
    paths = sorted(glob.glob(os.path.join(CORPUS_DIR, "*.docx")))
    # Tests build against a small slice (the leading product briefs) to stay
    # fast; the real Pages build ingests the whole corpus.
    limit = os.environ.get("KF_SHOWCASE_CORPUS_LIMIT")
    if limit:
        paths = paths[: int(limit)]
    for path in paths:
        name = os.path.basename(path)
        title = re.sub(r"^\d+_", "", os.path.splitext(name)[0]).replace("_", " ")
        with open(path, "rb") as fh:
            data = fh.read()
        raw = intake.canonical(
            TENANT,
            CORPUS_SOURCE,
            f"github://{CORPUS_REPO}/docs_source/{name}",
            title,
            data,
            mime=DOCX_MIME,
            acl=["public"],
        )
        intake.submit(raw)
    worker.drain()
    # Register the GitHub repo as the source in the Admin console (freshness,
    # item count). Other repos are added by an admin via the allow-list — this
    # one ships enabled so the fabric is useful on day one.
    import time as _time

    p.db.execute(
        "INSERT INTO connector_cursors(tenant,source,cursor,last_sync,items) "
        "VALUES(?,?,?,?,?) ON CONFLICT(tenant,source) DO UPDATE SET "
        "cursor=excluded.cursor, last_sync=excluded.last_sync, items=excluded.items",
        (TENANT, CORPUS_SOURCE, str(len(paths)), int(_time.time() * 1000), len(paths)),
    )
    return len(paths)


def _seed():
    p = http_api.Platform(
        db_path=":memory:", blob_root=os.path.join(ROOT, "data", "showcase-blobs")
    )
    demo.seed(p, [TENANT])
    p.policy.set_budget(TENANT, 20.0)
    _load_corpus(p)  # real QualiZeal knowledge, ingested through the live pipeline
    qbank.generate(p, TENANT)  # bank from the loaded corpus
    svc = AnswerService(p)
    for subject, q in SCRIPT:
        try:
            svc.ask(demo.principal_for(p, TENANT, subject), q)
        except Exception:
            pass
    return p


class _Client:
    """Tiny in-process HTTP client against the running showcase server."""

    def __init__(self, base):
        self.base = base

    def call(self, method, path, body=None, token=None):
        data = json.dumps(body).encode() if body is not None else None
        req = urllib.request.Request(self.base + path, data=data, method=method)
        if token:
            req.add_header("Authorization", "Bearer " + token)
        if data is not None:
            req.add_header("Content-Type", "application/json")
        try:
            with urllib.request.urlopen(req, timeout=60) as r:
                return r.status, json.loads(r.read() or b"{}")
        except urllib.error.HTTPError as e:
            try:
                return e.code, json.loads(e.read() or b"{}")
            except Exception:
                return e.code, {}


def _bake(client) -> dict:
    snap: dict = {
        "login": {},
        "get": {"asker": {}, "curator": {}, "admin": {}},
        "answers": {},
        "galaxy": {},
        "usage": {},
        "suggestions": {},
        "analytics": {},
        "versions": {},
        "doctor": {},
        "bank": [],
    }
    tokens = {}
    for subject in ROLES:
        code, j = client.call("POST", "/login", {"tenant": TENANT, "subject": subject})
        if code == 200 and "token" in j:
            snap["login"][subject] = j
            tokens[subject] = j["token"]

    def norm(q):
        import re

        return re.sub(r"\s+", " ", re.sub(r"[^a-z0-9\s]", " ", q.lower())).strip()

    # answers + galaxy (bake as the broad asker so citations are full)
    asker = tokens.get("asker.public")
    restricted = tokens.get("asker.restricted")
    seen = set()
    # every accessible suggestion + the curated extras
    for tok, key in ((asker, "public"), (restricted, "restricted")):
        if not tok:
            continue
        _, s = client.call("GET", "/api/suggestions", token=tok)
        snap["suggestions"][key] = s
        for item in s.get("suggestions") or []:
            q = item.get("question")
            if q:
                EXTRA_Q.append(q)
    for q in EXTRA_Q:
        n = norm(q)
        if n in seen:
            continue
        seen.add(n)
        _, a = client.call("POST", "/ask", {"question": q}, token=asker)
        if a and a.get("kind"):
            snap["answers"][n] = a
            snap["bank"].append(q)
            tid = a.get("trajectory_id")
            if tid:
                _, g = client.call("GET", "/api/galaxy?trace_id=" + tid, token=asker)
                snap["galaxy"][tid] = g

    # ---- follow-up vocabulary (coreference "it/that" -> the topic) ----------
    # Data-driven: whatever the corpus actually answers about becomes a subject
    # the chatbot can resolve a pronoun to. A "subject" is a proper-noun token
    # from the baked questions with an internal capital (QMentisAI, ValidAIte,
    # NexaAI, QualiCentral, QualiZeal) — product/brand names, never plain words
    # like "France". `related` lists the baked questions per subject so a
    # follow-up we can't answer becomes a clarify with real, clickable offers.
    import re as _re

    subjects: dict[str, str] = {}
    for q in snap["bank"]:
        for tok in _re.findall(r"[A-Za-z][A-Za-z0-9]{3,}", q):
            if _re.search(r"[a-z][A-Z]", tok):
                subjects.setdefault(tok.lower(), tok)
    snap["subjects"] = subjects
    snap["related"] = {
        key: [q for q in snap["bank"] if key in q.lower()][:6] for key in subjects
    }

    # per-subject usage
    for subject in ROLES:
        tok = tokens.get(subject)
        if not tok:
            continue
        _, u = client.call("GET", "/api/usage", token=tok)
        snap["usage"][subject] = u

    # role-bucketed GETs
    for path in ASKER_GETS:
        _, snap["get"]["asker"][path.split("?")[0]] = client.call("GET", path, token=asker)
    # The corpus strip (documents/passages/entities/relationships/domains) reads
    # the top-level "corpus" key from the engine; publish the real counts there.
    snap["corpus"] = snap["get"]["asker"].get("/api/corpus", {})
    for path in CURATOR_GETS:
        code, j = client.call("GET", path, token=tokens.get("curator"))
        if code == 200:
            snap["get"]["curator"][path.split("?")[0]] = j
    for path in ADMIN_GETS:
        code, j = client.call("GET", path, token=tokens.get("admin"))
        if code == 200:
            snap["get"]["admin"][path.split("?")[0]] = j

    # analytics per window (curator+admin dashboards / telemetry page)
    for win in ("24h", "7d", "all"):
        code, j = client.call("GET", "/api/analytics?window=" + win, token=tokens.get("admin"))
        if code == 200:
            snap["analytics"][win] = j
            snap["analytics"][{"24h": "24h", "7d": "7d", "all": "all"}[win]] = j

    # doctor targets (admin AWS-readiness panel)
    for target in ("", "aws", "model", "cache"):
        code, j = client.call("GET", "/admin/doctor?target=" + target, token=tokens.get("admin"))
        if code == 200:
            snap["doctor"][target] = j

    # versions for the curator doc table (first few documents)
    docs = (snap["get"]["curator"].get("/curator/documents") or {}).get("documents") or []
    for d in docs[:8]:
        did = d.get("document_id") or d.get("id")
        if did:
            code, j = client.call(
                "GET", "/curator/versions?document_id=" + did, token=tokens.get("curator")
            )
            if code == 200:
                snap["versions"][did] = j
    return snap


# --------------------------------------------------------------------------
# emit surfaces
# --------------------------------------------------------------------------
def _rewrite(html: str, surface: str, asset_prefix: str, engine_href: str) -> str:
    """Rewrite a served page for static hosting: relative assets + the engine."""
    html = html.replace("/static/assets/", asset_prefix)
    # root links (the lockup on standalone pages) point at the showcase root.
    html = html.replace('href="/"', 'href="../"')
    inject = (
        f'<script>window.KF_SURFACE={surface!r};</script>\n<script src="{engine_href}"></script>\n'
    )
    # the engine must run before ANY page script (it installs the fetch shim and
    # sets KF_BASE). Inject before the first <script> — works for the shared
    # runtime pages and the standalone telemetry page alike.
    i = html.find("<script")
    if i < 0:
        return html
    return html[:i] + inject + html[i:]


def build(out_dir: str) -> None:
    out = os.path.abspath(out_dir)
    if os.path.isdir(out):
        shutil.rmtree(out)
    os.makedirs(out, exist_ok=True)
    open(os.path.join(out, ".nojekyll"), "w").close()
    shutil.copytree(BRAND_SRC, os.path.join(out, "assets", "brand"))
    shutil.copy(ENGINE_SRC, os.path.join(out, "engine.js"))

    # seed + run server + bake
    p = _seed()
    # The seed run populated telemetry (incl. cache hits, so analytics shows the
    # savings story). Clear the answer cache before baking so every demo answer
    # runs fresh retrieval and carries a populated galaxy.
    p.cache.invalidate(TENANT)
    saved = (http_api._platform, http_api._svc)
    http_api._platform = p
    http_api._svc = AnswerService(p)
    srv = ThreadingHTTPServer(("127.0.0.1", 0), http_api.Handler)
    base = f"http://127.0.0.1:{srv.server_address[1]}"
    th = threading.Thread(target=srv.serve_forever, daemon=True)
    th.start()
    try:
        snap = _bake(_Client(base))
    finally:
        srv.shutdown()
        srv.server_close()
        http_api._platform, http_api._svc = saved

    with open(os.path.join(out, "snapshot.json"), "w", encoding="utf-8") as fh:
        json.dump(snap, fh, separators=(",", ":"), default=str)

    surfaces = {
        "workspace": ASK_HTML,
        "admin": ADMIN_HTML,
        "curator": CURATOR_HTML,
        "signin": SIGNIN_HTML,
        "dashboard": DASHBOARD_HTML,
    }
    for name, html in surfaces.items():
        folder = os.path.join(out, name)
        os.makedirs(folder, exist_ok=True)
        page = _rewrite(html, name, "../assets/", "../engine.js")
        with open(os.path.join(folder, "index.html"), "w", encoding="utf-8") as fh:
            fh.write(page)

    # landing page (root) — brand hero, explainability + chat widget
    with open(LANDING_SRC, encoding="utf-8") as fh:
        landing = fh.read()
    landing = landing.replace("__BANK__", json.dumps(snap["bank"][:8]))
    with open(os.path.join(out, "index.html"), "w", encoding="utf-8") as fh:
        fh.write(landing)

    entries = ", ".join(sorted(os.listdir(out)))
    print(
        f"showcase built at {out}\n  {entries}\n"
        f"  answers={len(snap['answers'])} galaxies={len(snap['galaxy'])} bank={len(snap['bank'])}"
    )


def main(argv=None):
    ap = argparse.ArgumentParser(prog="build_showcase")
    ap.add_argument("--out", default="dist/showcase")
    args = ap.parse_args(argv)
    build(args.out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
