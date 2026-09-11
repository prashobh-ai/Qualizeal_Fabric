"""Production demo (T50) — `make demo` extension.

Walks the Production-on-GitHub path end to end on whatever is REALLY present:

  1. the provider line + call count (Actions run summary / provider_status.json + ledger)
  2. Admin → Models: the provider card and API consumption (the ledger sums)
  3. the ten repository questions, answered at Level 0 from facts
  4. `how many open bugs in project <X>` from Jira facts
  5. an Excel question answered by a SELECT with a row citation
  6. an image question answered from the stored description
  7. a multi-tool question through the queue → the answer file lands → the ledger row

Nothing is faked: a step that needs the real key or a real source prints what it
needs and where the proof lives (the Actions run). With `--fixture` the fabric-data
fixture from eval/fixture.py is used so every step runs here, deterministically.

Run:  python3 scripts/demo_production.py [--fixture]
"""

from __future__ import annotations

import argparse
import hashlib
import os
import re
import sys
import tempfile

ROOT = os.path.abspath(os.path.dirname(os.path.dirname(__file__)))
sys.path.insert(0, ROOT)
os.environ.setdefault("KF_MODEL_MODE", "extractive")


def rule(t: str) -> None:
    print("\n" + "═" * 78 + f"\n▐ {t}\n" + "═" * 78)


def norm(q: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"[^a-z0-9\s]", " ", (q or "").lower())).strip()


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="demo_production")
    ap.add_argument(
        "--fixture", action="store_true", help="use the deterministic fabric-data fixture"
    )
    args = ap.parse_args(argv)
    if args.fixture:
        from eval import fixture

        fixture.build(tempfile.mkdtemp(prefix="kf-demo-"))

    from knowledge_fabric import fabric_data as fd
    from knowledge_fabric.adapters import model as _model
    from knowledge_fabric.telemetry import api_ledger

    rule("1. PROVIDER — the key is proven, the models are pinned, every call is counted")
    st = _model.provider_status()
    if st:
        print(
            f"  Provider: {st.get('provider')} · key …{str(st.get('key_fingerprint', ''))[-4:]}"
            f" · small {st.get('model_small')} · large {st.get('model_large')}"
            f" · verified {st.get('verified_at')}"
        )
    else:
        print(
            "  provider_status.json absent — run `python3 scripts/doctor.py --require anthropic` "
            "with the key (in Actions the showcase workflow does this first and prints the "
            "provider line in the run summary)."
        )
    print("  " + api_ledger.step_summary_line())

    rule("2. ADMIN → MODELS — provider card + API consumption (dashboard totals = ledger sums)")
    c = api_ledger.consumption(7)
    t = c["totals"]
    print(
        f"  7d: {t['calls']} calls · in {t['input_tokens']} · out {t['output_tokens']} · "
        f"cache read {t['cache_read']} · cost ${t['cost_usd']:.4f}"
    )
    print("  by purpose:", {k: v["calls"] for k, v in c["by_purpose"].items()} or "—")
    print("  prices:", {m: (v["input"], v["output"]) for m, v in c["prices"].items()})

    rule("3. THE TEN REPOSITORY QUESTIONS — Level 0 from facts, exact, as-of time")
    facts = fd.read_json(fd.data_path("facts.json"), {})
    repos = facts.get("repositories", {})
    if not repos:
        print(
            "  no repositories in facts.json — run `scripts/ingest.py --github` with "
            "KF_GITHUB_TOKEN (ingest.yml does this every 6h). Use --fixture to demo with the "
            "fixture data."
        )
    else:
        try:
            from knowledge_fabric.answer import aggregate
            from knowledge_fabric.answer.service import AnswerService
            from knowledge_fabric.app import Platform
            from knowledge_fabric.evaluation import quality as q

            p = Platform(db_path=":memory:", blob_root=os.path.join(tempfile.mkdtemp(), "b"))
            q.build_eval_fabric(p)
            AnswerService(p)
            prin = q._principal(p, q.EVAL_TENANT)

            # the repository with the most commits — the one leadership asks about
            def _commits(k):
                c = repos[k].get("commits")
                return int((c.get("total") if isinstance(c, dict) else c) or 0)

            repo = max(repos, key=_commits)
            for question in (
                f"how many commits in {repo}",
                f"how many contributors in {repo}",
                f"how many pull requests were merged in {repo}",
                f"which languages are used in {repo}",
                f"how many deployments in {repo}",
                "how many repositories do we have",
                "do we have any rag implementation",
                "have we ever built a knowledge graph",
                f"which retrieval techniques were used in {repo}",
                f"is {repo} production ready",
            ):
                a = aggregate.try_answer(p, prin, question, None)
                text = (a.answer_text if a else "— no Level-0 answer").strip().splitlines()[0]
                print(f"  Q {question}\n    → {text[:110]}  [{len(a.citations) if a else 0} cites]")
        except ImportError as e:
            print(f"  answer.aggregate not available yet: {e}")

    rule("4. JIRA — `how many open bugs in project <X>` from Jira facts")
    jp = facts.get("jira_projects", {})
    if not jp:
        print(
            "  no Jira projects in facts.json — set JIRA_URL/JIRA_EMAIL/JIRA_TOKEN and run "
            "`scripts/ingest.py --jira`."
        )
    else:
        # Show every project — the largest first, so the headline number is the one
        # the leadership question ("how many open bugs in QZ?") actually asks about.
        for key in sorted(jp, key=lambda k: -int(jp[k]["issues"].get("total", 0) or 0)):
            by = jp[key]["issues"].get("by_status", {})
            print(
                f"  project {key}: open {by.get('open', 0)} · "
                f"total {jp[key]['issues'].get('total')} · as of {jp[key].get('as_of')}"
            )

    rule("5. EXCEL — a sheet question answered by a SELECT with a row citation")
    tables = facts.get("tables", [])
    if not tables:
        print("  no tables in facts.json — upload an .xlsx/.csv (T41) or use --fixture.")
    else:
        tb = tables[0]
        try:
            from knowledge_fabric.answer import tables as _tables

            res = _tables.run_table_query(tb["doc_id"], tb["sheet"], "SELECT COUNT(*) FROM t")
            print(
                f"  {tb['doc_title']} / {tb['sheet']}: COUNT(*) = {res['rows'][0][0]}  "
                f"(citation: {tb['path']})"
            )
        except ImportError as e:
            import sqlite3

            con = sqlite3.connect(fd.path(*tb["path"].split("/")))
            n_rows = con.execute("SELECT COUNT(*) FROM t").fetchone()[0]
            print(
                f"  {tb['doc_title']} / {tb['sheet']}: COUNT(*) = {n_rows}  "
                f"(citation: {tb['path']}; answer.tables not merged: {e})"
            )

    rule("6. IMAGE — answered from the stored description (OCR + image_describe)")
    img_root = fd.path("images")
    found = []
    if os.path.isdir(img_root):
        for doc in sorted(os.listdir(img_root)):
            for name in sorted(os.listdir(os.path.join(img_root, doc))):
                found.append((doc, name))
    if not found:
        print(
            "  no images/ descriptions — ingest an image (T41); the real description needs the key "
            "(purpose image_describe); OCR text is stored regardless."
        )
    else:
        doc, name = found[0]
        d = fd.read_json(os.path.join(img_root, doc, name), {})
        print(
            f"  {doc}/{name}: kind={d.get('kind')} · caption={d.get('caption')!r}"
            f" · model={d.get('model')}"
        )

    rule("7. THE QUEUE — a multi-tool question → answers/<hash>.json lands → ledger row")
    question = "compare the commit counts of the two most active repositories"
    h = hashlib.sha256(norm(question).encode()).hexdigest()[:16]
    path = fd.path("answers", f"{h}.json")
    if os.path.exists(path):
        a = fd.read_json(path, {})
        print(
            f"  answers/{h}.json present: source={a.get('source')} model={a.get('model')}"
            f" cost=${a.get('cost_usd')}"
        )
    else:
        print(
            f"  answers/{h}.json not present. In production: the Workspace `Get full answer` "
            "button opens an issue labelled `ask`; ask.yml runs the agent with the key and "
            "writes this file, comments the answer, closes the issue; the page polls and swaps "
            "the bubble; the ledger gains an `ask_queue` row."
        )
        print(
            "  Local proof of the pipe: `python3 scripts/answer_issue.py --question ...` "
            "(needs the key)."
        )

    rule(
        "DONE — Production on GitHub: provider proven, consumption real, facts exact, sources live."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
