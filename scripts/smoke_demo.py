"""T124c — one demo question per source, asserted against a live pull.

Runs after ingest (secrets in the env). It pulls each configured source into a
fresh in-memory fabric — bounded and on the model-free extractive path, so it
is cheap and needs no credits — then asks ONE question per source and asserts a
cited answer (``kind == answer`` with ≥ 1 citation), never a gap or clarify. A
gap here fails the build and names the source and question, so an empty tile is
caught in the log, not in front of leadership.

The ask/assert core (:func:`run_smoke`) takes a ready platform, so it is
unit-tested offline with a pre-populated fabric; ``main`` does the live pull.
"""

from __future__ import annotations

import json
import os
import sys

ROOT = os.path.abspath(os.path.dirname(os.path.dirname(__file__)))
sys.path.insert(0, ROOT)
os.environ.setdefault("KF_MODEL_MODE", "extractive")  # deterministic, no credits

TENANT = "qualizeal"

# one question per source; each must come back kind=answer with a citation.
QUESTIONS = {
    "website": "what does QualiZeal offer for security testing",
    "github": "how many repositories does QualiZeal have",
    "jira": "how many tasks are in progress on the Platform board",
    "confluence": "who is the project lead in the Project Plan page",
}


def run_smoke(platform, svc, sources: list[dict], asker=None) -> list[str]:
    """Ask one question per present source; return a list of failure strings."""
    from knowledge_fabric.tenants import demo

    asker = asker or demo.principal_for(platform, TENANT, "developer")
    errors: list[str] = []
    for src in sources:
        q = QUESTIONS.get(src["kind"])
        if not q:
            continue
        try:
            a = svc.ask(asker, q)
        except Exception as e:  # noqa: BLE001 — a smoke failure names the source
            errors.append(f"{src['key']}: ask raised {type(e).__name__}: {e} · {q!r}")
            continue
        kind = getattr(getattr(a, "kind", None), "value", "")
        cited = bool(getattr(a, "citations", None))
        if kind != "answer" or not cited:
            errors.append(
                f"{src['key']}: got kind={kind!r} cited={cited} (need a cited answer) · {q!r}"
            )
    return errors


# --------------------------------------------------------------------------
# live pull (main only) — bounded, so the smoke stays fast
# --------------------------------------------------------------------------
def _ingest_live(platform, sources: list[dict]) -> None:
    from knowledge_fabric.connectors.confluence import ConfluenceConnector
    from knowledge_fabric.connectors.github_live import GitHubLiveConnector
    from knowledge_fabric.connectors.jira_live import JiraLiveConnector
    from knowledge_fabric.connectors.website import WebsiteConnector
    from knowledge_fabric.ingestion.intake import IngestWorker, Intake

    def ingest(items):
        intake = Intake(platform)
        worker = IngestWorker(platform, intake)
        for it in items:
            it.meta.setdefault("ontology", "quality-assurance")
            intake.submit(it)
        worker.drain()

    for src in sources:
        kind = src["kind"]
        try:
            if kind == "website":
                url = src.get("url") or os.environ.get("KF_WEBSITE_URL", "")
                items, _ = WebsiteConnector(TENANT, {"url": url, "max_pages": 20}).pull(None)
                ingest(items)
            elif kind == "confluence" and src.get("pages"):
                items, _ = ConfluenceConnector(
                    TENANT, {"pages": src["pages"], "attachments": False}
                ).pull(None)
                ingest(items)
            elif kind == "jira":
                cfg = {k: src[k] for k in ("projects", "boards", "dashboards") if src.get(k)}
                cfg["board_id"] = (src.get("boards") or [None])[0]
                items, _ = JiraLiveConnector(TENANT, cfg).pull(None)
                ingest(items)
            elif kind == "github":
                # count only — list the org's repos (one cheap call, no clones)
                # and write the repositories fact the count question reads.
                from knowledge_fabric import facts as factsmod

                conn = GitHubLiveConnector(TENANT, {"org": src["org"], "clone": False})
                repos = conn.list_repositories()
                p = factsmod.fd.data_path("facts.json", mkdir=True)
                allf = factsmod.fd.read_json(p, {}) or {}
                allf.setdefault("repositories", {})
                for full in repos:
                    allf["repositories"].setdefault(full, {"full_name": full})
                factsmod.fd.write_json(p, allf)
        except Exception as e:  # noqa: BLE001 — reported by the assertion below
            print(f"  (live pull for {src['key']} raised {type(e).__name__}: {e})", file=sys.stderr)


def main(argv=None) -> int:
    from knowledge_fabric.answer.service import AnswerService
    from knowledge_fabric.app import Platform
    from knowledge_fabric.tenants import demo

    with open(os.path.join(ROOT, "data", "showcase_sources.json"), encoding="utf-8") as f:
        sources = json.load(f)["sources"]

    platform = Platform(db_path=":memory:", blob_root=os.path.join(ROOT, "data", "smoke-blobs"))
    demo.seed(platform)
    platform.policy.set_budget(TENANT, 1000.0)
    _ingest_live(platform, sources)

    errors = run_smoke(platform, AnswerService(platform), sources)
    for src in sources:
        q = QUESTIONS.get(src["kind"])
        if q:
            print(f"{src['key']}: asked {q!r}")
    if errors:
        for e in errors:
            print(f"FAIL: {e}", file=sys.stderr)
        print(f"\nSmoke: {len(errors)} source(s) did not answer with a citation.")
        return 1
    n = len([s for s in sources if QUESTIONS.get(s["kind"])])
    print(f"\nSmoke: {n}/4 sources answered with a citation.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
