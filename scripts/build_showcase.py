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
import urllib.parse
import urllib.request
from http.server import ThreadingHTTPServer

ROOT = os.path.abspath(os.path.dirname(os.path.dirname(__file__)))
sys.path.insert(0, ROOT)
os.environ.setdefault("KF_LEDGER_PURPOSE", "answer_bake")
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
# Vendored browser libs (vis-network + the KFGalaxy view) served same-origin
# under /static/vendor/ live; copied to the showcase root as /vendor/ (T51).
VENDOR_SRC = os.path.join(ROOT, "knowledge_fabric", "surfaces", "static", "vendor")
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

# T25 — code into the fabric. The showcase ingests this repository's OWN source
# (the checkout is already present in the Pages build), so code questions answer
# with the real function and a line-anchored GitHub link, no external token
# needed. A curated, representative set keeps the build fast; the repository card
# answers "what does this repo do".
CODE_REPO = "prashobh-ai/Qualizeal_Fabric"
CODE_MIME = "text/x-python;code"
CODE_FILES = [
    # platform core
    "knowledge_fabric/answer/service.py",
    "knowledge_fabric/answer/selector.py",
    "knowledge_fabric/answer/search.py",
    "knowledge_fabric/adapters/converter.py",
    "knowledge_fabric/adapters/model.py",
    "knowledge_fabric/adapters/lexicalindex.py",
    "knowledge_fabric/ingestion/pipeline.py",
    "knowledge_fabric/ingestion/intake.py",
    "knowledge_fabric/connectors/github.py",
    # authentication / SSO / policy — the reusable building blocks discovery finds
    "knowledge_fabric/governance/policy.py",
    "knowledge_fabric/surfaces/signin_ui.py",
    "knowledge_fabric/surfaces/ui_common.py",
    "knowledge_fabric/surfaces/http_api.py",
    "scripts/build_showcase.py",
]
# Automation scripts — a tester asks "is there a script for <feature>?"; these
# real test suites are the answer, cited to the exact function and lines.
TEST_FILES = [
    "tests/test_governance.py",
    "tests/test_ingestion.py",
    "tests/test_connectors.py",
    "tests/test_stage2_refresh.py",
    "tests/test_t25_code.py",
]
# HR / learning / standards — any employee, any role, asks about policy or
# learning material and must not hit a blind gap.
ORG_DIR = os.path.join(ROOT, "corpus", "org")

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
    ("qa-agent", "how does subject boost work"),  # code answer (identifier tier)
    ("asker.restricted", "where is the docx converter"),  # code answer
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
    # T25 — code questions, answered from this repository's own source with
    # line-anchored GitHub citations (the identifier tier).
    "how does subject boost work",
    "where is the docx converter",
    "what does the ingestion pipeline do",
    "how does the answer cache work",
    "where is the github connector",
    "how does the model client pick a tier",
    "what does this repository do",
    # T24 — asset/capability discovery (search across the fabric + live sources)
    # and cross-domain org questions any employee, any role, may ask.
    "has anyone made sso and auth code which I can reuse",
    "can I find an automation script for ingestion",
    "is there a reusable github connector",
    "where is the leave policy",
    "what is our single sign-on and authentication standard",
    "find learning material for onboarding",
    "what does the test automation playbook say",
]

# T30 — two-turn follow-ups (subject, first question, follow-up) whose pronoun
# resolves against a distinctive product subject, so the baked telemetry carries
# context-resolved answers (T26) for the Explorer's Context dimension.
FOLLOWUPS = [
    ("developer", "what is QMentisAI?", "what about its pricing"),
    ("tester", "what does ValidAIte do?", "and for testers?"),
    ("cto", "what is QMentisAI?", "what about its pricing"),
    ("curator", "what is NexaAI?", "what about its pricing"),
]

# Access tiers plus the designation demo accounts (T27), so the telemetry spread
# and the baked directory cover every persona the showcase can sign in as.
ROLES = [
    "asker.public",
    "asker.restricted",
    "curator",
    "admin",
    "qa-agent",
    "developer",
    "tester",
    "architect",
    "delivery",
    "cto",
]
# GET endpoints to capture per bucket (askers use only the /api/* set).
ASKER_GETS = ["/api/corpus"]  # T47: carries the repositories/jira/confluence/tables/images tiles
CURATOR_GETS = [
    "/curator/quality",
    "/curator/gaps",
    "/curator/documents",
    "/curator/recommendations",  # T44 — documents whose generated questions failed
    "/admin/sources",  # T47: + github/jira/confluence cards
    "/curator/repositories",  # T47: facts.json + capabilities.json rows
    "/curator/tables",  # T47: extracted sheets (+ a read-only sample for the static preview)
    "/curator/insights",  # T47 + T57: capabilities/reuse + the graph community insights
    "/curator/curation-modes",  # T53: per-source + global curation mode settings
    "/curator/review",  # T53: the manual-mode review queue
    "/curator/timeline",  # T54: the ingestion timeline (per-month stacks)
    "/curator/registry",  # T82: the governed known-question registry
]
# T45 — the Workspace's "Get full answer" opens an `ask` issue on this repo.
KF_REPO = os.environ.get("GITHUB_REPOSITORY") or "prashobh-ai/QualiZeal_Fabric"


def norm(q):
    """The question key. Byte-identical to ``knowledge_fabric.baking.norm`` and
    to engine.js's ``norm`` (a test asserts it) — the baked-answer file name is
    ``sha256(norm(q))[:16]``."""
    import re

    return re.sub(r"\s+", " ", re.sub(r"[^a-z0-9\s]", " ", q.lower())).strip()


ADMIN_GETS = [
    "/admin/models",  # T35/T36 provider card + consumption (baked from this run's ledger)
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
    "/curator/repositories",
    "/curator/tables",
    "/curator/insights",
]
# T47 — the repository card overlay is keyed by repo (GET /curator/repository?repo=),
# so it is baked per repository under snap["repository"] rather than as a GET path.
REPO_CARD_GET = "/curator/repository?repo="


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


def _repo_card() -> str:
    """A deterministic repository-overview document (no model): README opening,
    the package map, entry points and CI — what answers 'what does this repo
    do'."""
    parts = ["QualiZeal_Fabric — repository overview."]
    try:
        with open(os.path.join(ROOT, "README.md"), encoding="utf-8") as fh:
            head = fh.read()
        intro = " ".join(
            ln.strip() for ln in head.splitlines()[:12] if ln.strip() and not ln.startswith("#")
        )
        if intro:
            parts.append(intro)
    except OSError:
        pass
    pkgs = sorted(
        d
        for d in os.listdir(os.path.join(ROOT, "knowledge_fabric"))
        if os.path.isdir(os.path.join(ROOT, "knowledge_fabric", d)) and not d.startswith("__")
    )
    parts.append("The knowledge_fabric package is organised into: " + ", ".join(pkgs) + ".")
    parts.append(
        "The answer service is the single governed path: it retrieves hybrid "
        "evidence, grounds it, routes across model levels, and composes a cited "
        "answer. The ingestion pipeline chunks documents and code with "
        "provenance. Connectors pull from GitHub and other sources. The scripts "
        "package builds the static showcase deployed to GitHub Pages."
    )
    wf = os.path.join(ROOT, ".github", "workflows")
    if os.path.isdir(wf):
        parts.append(
            "Continuous integration workflows: "
            + ", ".join(
                sorted(os.path.splitext(f)[0] for f in os.listdir(wf) if f.endswith(".yml"))
            )
            + "."
        )
    parts.append(
        "It runs on the Python standard library only, with a full test suite "
        "under tests and a Makefile for lint and test."
    )
    return "\n\n".join(parts)


def _load_code(p):
    """Ingest this repository's own source through the real pipeline so code
    questions answer with the actual function and a line-anchored GitHub link.
    Adds one synthetic repository-overview document for 'what does this repo do'.
    """
    import time as _time

    from knowledge_fabric.ingestion.intake import IngestWorker, Intake

    intake, worker = Intake(p), IngestWorker(p, None)
    worker.intake = intake
    n = 0
    limit = os.environ.get("KF_SHOWCASE_CODE_LIMIT")
    files = CODE_FILES[: int(limit)] if limit else (CODE_FILES + TEST_FILES)
    for rel in files:
        path = os.path.join(ROOT, rel)
        if not os.path.exists(path):
            continue
        with open(path, "rb") as fh:
            data = fh.read()
        raw = intake.canonical(
            TENANT,
            CORPUS_SOURCE,
            f"github://{CODE_REPO}/{rel}",
            os.path.basename(rel),
            data,
            mime=CODE_MIME,
            acl=["public"],
        )
        intake.submit(raw)
        n += 1
    card = _repo_card().encode()
    intake.submit(
        intake.canonical(
            TENANT,
            CORPUS_SOURCE,
            f"github://{CODE_REPO}/README.md",
            "QualiZeal_Fabric — repository overview",
            card,
            mime="text/markdown",
            acl=["public"],
        )
    )
    worker.drain()
    p.db.execute(
        "INSERT INTO connector_cursors(tenant,source,cursor,last_sync,items) "
        "VALUES(?,?,?,?,?) ON CONFLICT(tenant,source) DO UPDATE SET "
        "cursor=excluded.cursor, last_sync=excluded.last_sync, items=items+excluded.items",
        (TENANT, "github-code", str(n + 1), int(_time.time() * 1000), n + 1),
    )
    return n + 1


def _load_org(p):
    """Ingest the HR / learning / standards corpus (Markdown) so any employee,
    any role, can ask about policy or learning material without hitting a gap."""
    import glob
    import time as _time

    from knowledge_fabric.ingestion.intake import IngestWorker, Intake

    intake, worker = Intake(p), IngestWorker(p, None)
    worker.intake = intake
    paths = sorted(glob.glob(os.path.join(ORG_DIR, "*.md")))
    for path in paths:
        name = os.path.basename(path)
        title = os.path.splitext(name)[0].replace("_", " ").title()
        with open(path, "rb") as fh:
            data = fh.read()
        intake.submit(
            intake.canonical(
                TENANT,
                "internal",
                f"internal://qualizeal/handbook/{name}",
                title,
                data,
                mime="text/markdown",
                acl=["public"],
            )
        )
    worker.drain()
    if paths:
        p.db.execute(
            "INSERT INTO connector_cursors(tenant,source,cursor,last_sync,items) "
            "VALUES(?,?,?,?,?) ON CONFLICT(tenant,source) DO UPDATE SET "
            "cursor=excluded.cursor, last_sync=excluded.last_sync, items=excluded.items",
            (TENANT, "internal", str(len(paths)), int(_time.time() * 1000), len(paths)),
        )
    return len(paths)


def _seed():
    p = http_api.Platform(
        db_path=":memory:", blob_root=os.path.join(ROOT, "data", "showcase-blobs")
    )
    demo.seed(p, [TENANT])
    p.policy.set_budget(TENANT, 20.0)
    _load_corpus(p)  # real QualiZeal knowledge, ingested through the live pipeline
    _load_code(p)  # this repository's own source, so code questions cite real functions
    _load_org(p)  # HR / learning / standards, so any-role questions never blind-gap
    qbank.generate(p, TENANT)  # bank from the loaded corpus
    svc = AnswerService(p)
    for subject, q in SCRIPT:
        svc.ask(demo.principal_for(p, TENANT, subject), q)  # T35: a provider error is loud
    # T29 — drive a broad spread across every role so the telemetry Explorer has
    # real volume to filter and pivot (role x level x model x language x kind).
    if not os.environ.get("KF_SHOWCASE_CORPUS_LIMIT"):  # full build only
        for subject in ROLES:
            for q in EXTRA_Q:
                svc.ask(demo.principal_for(p, TENANT, subject), q)
        # T30 — drive a few two-turn follow-ups so the Explorer's context
        # dimension (T26) carries `resolved` rows beside the `direct` majority.
        for subject, first, follow in FOLLOWUPS:
            ctx = {"turns": [{"question": first}]}
            svc.ask(demo.principal_for(p, TENANT, subject), follow, context=ctx)
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
            except json.JSONDecodeError:
                return e.code, {}


def _export_index(p) -> dict:
    """Export a compact retrieval index into the snapshot so the browser engine
    runs REAL BM25 retrieval over the whole corpus (T24), not just a lookup of
    baked answers. One entry per passage with the tokens' source string, plus
    document-frequency, N and average length for BM25. Code passages index their
    symbol and path so an identifier query finds them in the browser too."""
    import re as _re

    from knowledge_fabric.answer.search import _kind_of

    def toks(s):
        return _re.findall(r"[a-z0-9]+", (s or "").lower())

    docs, passages, df, total_len = {}, [], {}, 0
    for pas in p.passages.for_tenant(TENANT):
        d = p.documents.get(TENANT, pas.document_id) or {}
        loc = getattr(pas.coordinate, "locator", {}) or {}
        is_code = pas.coordinate.kind.value == "symbol_line"
        kind = _kind_of(d, pas.coordinate)
        # what the answer shows: a code summary line, else the passage prose
        shown = (loc.get("summary_line") or pas.text) if is_code else pas.text
        # what BM25 scores over: prose (capped) plus code symbol/path identifiers
        idx = pas.text[:600]
        if is_code:
            idx += " " + loc.get("symbol", "") + " " + loc.get("path", "")
        tk = toks(idx)
        if not tk:
            continue
        for t in set(tk):
            df[t] = df.get(t, 0) + 1
        total_len += len(tk)
        did = pas.document_id
        docs.setdefault(
            did, {"id": did, "title": d.get("title", ""), "kind": kind, "url": loc.get("url", "")}
        )
        passages.append(
            {
                "doc": did,
                "text": shown[:600],
                "kind": kind,
                "url": loc.get("url", ""),
                "path": loc.get("path", ""),
                "symbol": loc.get("symbol", ""),
                "coord": pas.coordinate.render(),
                "idx": idx[:800],
            }
        )
    n = len(passages)
    return {
        "docs": list(docs.values()),
        "passages": passages,
        "df": df,
        "N": n,
        "avgdl": (total_len / n) if n else 0.0,
    }


def _bake(client, p=None) -> dict:
    snap: dict = {
        "login": {},
        "get": {"asker": {}, "curator": {}, "admin": {}},
        "answers": {},
        "galaxy": {},
        "galaxy_nodes": {},  # T51 — node side-sheets keyed by node id
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
    # T82 — bake /api/suggestions per role (keyed by subject) so each persona's
    # Home shows its own known questions; the engine serves by subject, then
    # falls back to scope. The known examples are added to the answer bake so a
    # known chip resolves in the static demo, model-free.
    for subject, tok in tokens.items():
        _, s = client.call("GET", "/api/suggestions", token=tok)
        snap["suggestions"][subject] = s
        for item in s.get("known") or []:
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

    # T51 — provider badge + the full-fabric galaxy + node side-sheets. The node
    # sheets are baked for every node the answer galaxies actually surface
    # (activated + halo), so a click in the static demo opens the same panel.
    if asker:
        _, snap["provider"] = client.call("GET", "/api/provider", token=asker)
        # The whole-fabric galaxy (Curator graph) needs curate scope.
        curator_tok = tokens.get("curator")
        if curator_tok:
            _, snap["galaxy_full"] = client.call("GET", "/api/galaxy/full", token=curator_tok)
        node_ids: set[str] = set()
        for g in list(snap["galaxy"].values()) + [snap.get("galaxy_full") or {}]:
            node_ids.update(g.get("activated_ids") or [])
            node_ids.update(g.get("halo_ids") or [])
        for nid in sorted(node_ids):
            code, nd = client.call(
                "GET", "/api/galaxy/node?id=" + urllib.parse.quote(str(nid)), token=asker
            )
            if code == 200 and nd and nd.get("id"):
                snap["galaxy_nodes"][nid] = nd

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
    snap["related"] = {key: [q for q in snap["bank"] if key in q.lower()][:6] for key in subjects}

    # T24 — the browser retrieval index (BM25 over the whole corpus). Baked
    # answers stay as a Level-0 cache; anything not baked is retrieved live in
    # the browser from this index instead of falling to a blind gap.
    if p is not None:
        snap["index"] = _export_index(p)
        # T29 — flat telemetry rows for the self-serve Explorer (filter/pivot any
        # dimension in one table).
        snap["events"] = p.telemetry.events(TENANT)

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
    # T47 — one repository card per analysed repository (keyed by repo).
    snap["repository"] = {}
    for row in snap["get"]["curator"].get("/curator/repositories") or []:
        repo = row.get("repo") if isinstance(row, dict) else None
        if not repo:
            continue
        code, j = client.call(
            "GET", REPO_CARD_GET + urllib.parse.quote(repo, safe=""), token=tokens.get("curator")
        )
        if code == 200:
            snap["repository"][repo] = j
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
    # Vendored galaxy libs live beside assets (../vendor/ for a surface page).
    html = html.replace("/static/vendor/", asset_prefix.replace("assets/", "vendor/"))
    # root links (the lockup on standalone pages) point at the showcase root.
    html = html.replace('href="/"', 'href="../"')
    inject = (
        f"<script>window.KF_SURFACE={surface!r};window.KF_REPO={KF_REPO!r};</script>\n"
        f'<script src="{engine_href}"></script>\n'
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
    shutil.copytree(VENDOR_SRC, os.path.join(out, "vendor"))
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
        snap = _bake(_Client(base), p)
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

    # T44/T45 — baked answers from the fabric-data checkout ship as static
    # files: the engine fetches answers/<hash>.json before retrieving.
    baked = _copy_answers(out)

    entries = ", ".join(sorted(os.listdir(out)))
    print(
        f"showcase built at {out}\n  {entries}\n"
        f"  answers={len(snap['answers'])} galaxies={len(snap['galaxy'])} bank={len(snap['bank'])} "
        f"baked_files={baked}"
    )


def _copy_answers(out: str) -> int:
    """Copy ``<fabric root>/answers/*.json`` to ``<out>/answers/``; returns the count."""
    from knowledge_fabric import fabric_data as fd

    src = fd.path("answers")
    dst = os.path.join(out, "answers")
    os.makedirs(dst, exist_ok=True)
    n = 0
    if os.path.isdir(src):
        for name in sorted(os.listdir(src)):
            if name.endswith(".json"):
                shutil.copy(os.path.join(src, name), os.path.join(dst, name))
                n += 1
    return n


def _api_summary() -> int:
    """T35/T36: the direct check that the key was used in THIS run. Prints and
    appends the ledger line for this run id; under KF_MODEL_MODE=anthropic a
    run with zero API calls exits 7."""
    from knowledge_fabric.telemetry import api_ledger

    line = api_ledger.step_summary_line()
    print(line)
    api_ledger.append_step_summary(line)
    mode = (os.environ.get("KF_MODEL_MODE") or "").lower()
    # The doctor's ping shares this run id in Actions; it proves the key, not
    # the bake. Exit 7 when the BAKE made no calls.
    rid = api_ledger.run_id()
    bake_calls = [
        r
        for r in api_ledger.rows(2)
        if r.get("run_id") == rid and r.get("purpose") != "doctor_ping"
    ]
    if mode == "anthropic" and not bake_calls:
        print(
            "build_showcase: KF_MODEL_MODE=anthropic but the ledger shows 0 API calls "
            "from the bake (only the doctor ping, if any)",
            file=sys.stderr,
        )
        return 7
    return 0


def main(argv=None):
    ap = argparse.ArgumentParser(prog="build_showcase")
    ap.add_argument("--out", default="dist/showcase")
    args = ap.parse_args(argv)
    build(args.out)
    return _api_summary()


if __name__ == "__main__":
    sys.exit(main())
