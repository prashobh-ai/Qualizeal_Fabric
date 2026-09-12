"""Scheduled ingest (T46) — every source, then analysis, then the bake.

    python scripts/ingest.py --website --github --jira --confluence --files --analysis --bake
    python scripts/ingest.py --all [--tenant qualizeal] [--limit N]

Each flag calls its module when the module is present in this checkout and
reports clearly which steps ran and which were skipped, and why:

    --website     knowledge_fabric.connectors.website     .sync(platform, tenant)
    --github      knowledge_fabric.connectors.github_live .sync(platform, tenant)
    --jira        knowledge_fabric.connectors.jira_live   .sync(platform, tenant)
    --confluence  knowledge_fabric.connectors.confluence  .sync(platform, tenant)
    --files       the files connector over <fabric root>/corpus/public
    --analysis    knowledge_fabric.analysis               .run(platform, tenant)
    --bake        knowledge_fabric.baking.bake            (T44)

The vendored corpus (QualiZeal briefs, this repository's code, the handbook)
is always loaded first so the fabric exists on a fresh runner. Data lands
under ``KF_FABRIC_ROOT`` (the ``fabric-data`` checkout); the workflow commits
and pushes it. The ledger line for the run and the ingest line are printed
and appended to ``$GITHUB_STEP_SUMMARY``.
"""

from __future__ import annotations

import argparse
import datetime as _dt
import importlib
import json
import os
import sys
import time

ROOT = os.path.abspath(os.path.dirname(os.path.dirname(__file__)))
sys.path.insert(0, ROOT)
os.environ.setdefault("KF_LEDGER_PURPOSE", "answer_bake")
os.environ.setdefault("KF_MODEL_MODE", "extractive")

from knowledge_fabric import fabric_data as fd  # noqa: E402
from knowledge_fabric.telemetry import api_ledger  # noqa: E402

TENANT = "qualizeal"
STEPS = ("website", "github", "jira", "confluence", "files", "analysis", "bake")
_MODULES = {
    "website": ("knowledge_fabric.connectors.website", "sync"),
    "github": ("knowledge_fabric.connectors.github_live", "sync"),
    "jira": ("knowledge_fabric.connectors.jira_live", "sync"),
    "confluence": ("knowledge_fabric.connectors.confluence", "sync"),
    "analysis": ("knowledge_fabric.analysis", "run"),
}


def load_corpus(platform, tenant: str = TENANT, out=print) -> dict:
    """The vendored corpus through the real pipeline (idempotent by hash)."""
    from scripts import build_showcase as _b

    n_docs = _b._load_corpus(platform) if tenant == _b.TENANT else 0
    n_code = _b._load_code(platform) if tenant == _b.TENANT else 0
    n_org = _b._load_org(platform) if tenant == _b.TENANT else 0
    res = {"docs": n_docs, "code": n_code, "org": n_org}
    out(f"corpus: {n_docs} briefs · {n_code} code files · {n_org} handbook pages")
    return res


def _optional(name: str):
    """``(module, None)`` when importable, ``(None, reason)`` otherwise."""
    try:
        return importlib.import_module(name), None
    except ImportError as e:
        missing = getattr(e, "name", "") or ""
        if missing and name.startswith(missing) or missing == name:
            return None, f"module {name} not present in this checkout"
        return None, f"import failed: {e}"


def run_step(step: str, platform, tenant: str, *, limit=None, out=print) -> dict:
    """Run one step; returns ``{step, status: ran|skipped|failed, detail, seconds}``."""
    t0 = time.perf_counter()
    rec = {"step": step, "status": "ran", "detail": ""}
    try:
        if step in _MODULES:
            mod_name, fn_name = _MODULES[step]
            mod, why = _optional(mod_name)
            if mod is None:
                rec.update(status="skipped", detail=why)
            elif not hasattr(mod, fn_name):
                rec.update(status="skipped", detail=f"{mod_name} has no {fn_name}()")
            else:
                res = getattr(mod, fn_name)(platform, tenant)
                rec["detail"] = json.dumps(res, default=str)[:300] if res is not None else "ok"
        elif step == "files":
            folder = fd.path("corpus", "public")
            if not os.path.isdir(folder):
                rec.update(status="skipped", detail=f"{folder} does not exist")
            else:
                from knowledge_fabric.ingestion.sync import SyncManager

                res = SyncManager(platform).sync(
                    tenant,
                    "files",
                    {"folder": folder, "allow_ext": [".md", ".txt", ".csv", ".py", ".transcript"]},
                )
                rec["detail"] = f"pulled {res.get('pulled', 0)} · ingested {res.get('ingested', 0)}"
        elif step == "bake":
            from knowledge_fabric import baking

            s = baking.bake(platform, tenant, limit=limit, out=out)
            rec["detail"] = baking.summary_line(s)
            rec["summary"] = s
        else:
            rec.update(status="skipped", detail="unknown step")
    except Exception as e:  # noqa: BLE001 — reported per step; the run fails at the end
        rec.update(status="failed", detail=f"{type(e).__name__}: {e}")
    rec["seconds"] = round(time.perf_counter() - t0, 2)
    return rec


def report_line(records: list[dict]) -> str:
    ran = [r["step"] for r in records if r["status"] == "ran"]
    skipped = [r["step"] for r in records if r["status"] == "skipped"]
    failed = [r["step"] for r in records if r["status"] == "failed"]
    return (
        f"Ingest: ran {', '.join(ran) or '—'} · skipped {', '.join(skipped) or '—'} · "
        f"failed {', '.join(failed) or '—'}"
    )


def run(steps: list[str], *, tenant: str = TENANT, limit=None, platform=None, out=print) -> int:
    if platform is None:
        from knowledge_fabric.app import Platform
        from knowledge_fabric.tenants import demo

        platform = Platform(
            db_path=os.environ.get("KF_DB") or ":memory:",
            blob_root=os.path.join(fd.fabric_root(), "blobs"),
        )
        demo.seed(platform, [tenant])
    records = []
    t0 = time.perf_counter()
    try:
        load_corpus(platform, tenant, out=out)
        records.append({"step": "corpus", "status": "ran", "detail": "vendored corpus"})
    except Exception as e:  # noqa: BLE001
        records.append({"step": "corpus", "status": "failed", "detail": f"{type(e).__name__}: {e}"})
    for step in STEPS:
        if step in steps:
            records.append(run_step(step, platform, tenant, limit=limit, out=out))
        else:
            records.append({"step": step, "status": "skipped", "detail": "not requested"})
        if step == "files":
            # T42: the documents block of facts.json is the one fact only the
            # platform knows — refresh it once every source has landed.
            try:
                from knowledge_fabric import facts as factsmod

                factsmod.write_documents_facts(platform, tenant)
            except Exception as e:  # noqa: BLE001 — reported, never hidden
                records.append(
                    {"step": "facts", "status": "failed", "detail": f"{type(e).__name__}: {e}"}
                )
    # T99 — once every source has landed, rebuild the cross-source relationships
    # graph (Jira keys ↔ commits / PRs / documents) from the fabric's mentions,
    # so cross-source verification is a graph lookup rather than a re-scan.
    try:
        from knowledge_fabric import relationships as relmod

        rel = relmod.scan(platform, tenant)
        records.append(
            {"step": "relationships", "status": "ran", "detail": f"{rel['edges']} edges"}
        )
    except Exception as e:  # noqa: BLE001 — reported per step, never hidden
        records.append(
            {"step": "relationships", "status": "failed", "detail": f"{type(e).__name__}: {e}"}
        )
    for r in records:
        out(f"  {r['step']:<11} {r['status']:<8} {r.get('detail', '')}")
    line = report_line(records)
    ledger = api_ledger.step_summary_line()
    out(line)
    out(ledger)
    api_ledger.append_step_summary(line)
    api_ledger.append_step_summary(ledger)
    for r in records:
        if r["step"] == "bake" and r.get("summary"):
            api_ledger.append_step_summary(r["detail"])
    fd.append_jsonl(
        fd.data_path("ingest_runs.jsonl"),
        [
            {
                "at": _dt.datetime.now(_dt.UTC).isoformat(timespec="seconds"),
                "run_id": api_ledger.run_id(),
                "steps": [{k: v for k, v in r.items() if k != "summary"} for r in records],
                "seconds": round(time.perf_counter() - t0, 2),
            }
        ],
    )
    return 1 if any(r["status"] == "failed" for r in records) else 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="ingest", description=__doc__.splitlines()[0])
    for s in STEPS:
        ap.add_argument(f"--{s}", action="store_true")
    ap.add_argument("--all", action="store_true", help="every step")
    ap.add_argument("--tenant", default=TENANT)
    ap.add_argument("--limit", type=int, default=None, help="bake at most N documents")
    args = ap.parse_args(argv)
    steps = [s for s in STEPS if args.all or getattr(args, s)]
    if not steps:
        ap.error("nothing to do: pass one or more of --" + " --".join(STEPS) + " or --all")
    return run(steps, tenant=args.tenant, limit=args.limit)


if __name__ == "__main__":
    sys.exit(main())
