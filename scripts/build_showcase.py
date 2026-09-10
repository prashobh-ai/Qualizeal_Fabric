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
from tests.fixtures import synthetic_corpus  # noqa: E402

BRAND_SRC = os.path.join(ROOT, "knowledge_fabric", "surfaces", "static", "assets", "brand")
ENGINE_SRC = os.path.join(ROOT, "scripts", "showcase", "engine.js")
LANDING_SRC = os.path.join(ROOT, "scripts", "showcase", "landing.html")
TENANT = "qualizeal"

# A realistic run so analytics / usage / cache have something to show.
SCRIPT = [
    ("asker.public", "what must a release achieve before promotion?"),
    ("asker.public", "why does a component with an open defect block dependent releases?"),
    ("asker.public", "which requirement has a traceability gap?"),
    ("asker.public", "what blocks the release according to the standup?"),
    ("asker.public", "what must a release achieve before promotion?"),  # cache hit
    ("curator", "how fast must critical defects be triaged?"),
    ("curator", "compare acceptance criteria across the strategy and the runbook"),
    ("asker.public", "how fast must critical defects be triaged?"),
    (
        "asker.restricted",
        (
            "what must a release achieve before promotion and which requirement has a traceability "
            "gap?"
        ),
    ),
    ("asker.public", "quel est le critère d acceptation pour la couverture?"),  # FR
    ("asker.public", "¿cuál es el criterio de aceptación para la cobertura?"),  # ES
    ("qa-agent", "what is required before a release is promoted?"),
    ("asker.public", "what is the capital of France?"),  # gap
    ("curator", "why does an open defect block its dependent releases?"),
]

# Extra questions to bake answers for (so the chatbot / Workspace answer freely).
EXTRA_Q = [
    "what must a release achieve before promotion?",
    "how fast must critical defects be triaged?",
    "which requirement has a traceability gap?",
    "why does a component with an open defect block dependent releases?",
    "compare acceptance criteria across the strategy and the runbook",
    "what blocks the release according to the standup?",
    "what must a release achieve before promotion and which requirement has a traceability gap?",
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


def _seed():
    p = http_api.Platform(
        db_path=":memory:", blob_root=os.path.join(ROOT, "data", "showcase-blobs")
    )
    demo.seed(p, [TENANT])
    synthetic_corpus.load_into(p, TENANT)  # self-contained demo knowledge
    p.policy.set_budget(TENANT, 20.0)
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
