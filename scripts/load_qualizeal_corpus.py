"""Load the real QualiZeal product/service corpus (.docx) into a 'qualizeal'
tenant — proves the platform on QualiZeal's own knowledge, in QualiZeal format.

Usage:
    python scripts/load_qualizeal_corpus.py <folder-with-docx> [--db ./data/kf.db]

Points at any folder of .docx (e.g. the docs_source/ from the reference
Knowledge-Fabric repo). Ingestion is idempotent-by-hash, so re-running is a
no-op for unchanged files.
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from pathlib import Path
from knowledge_fabric.app import Platform
from knowledge_fabric.ingestion.intake import Intake, IngestWorker

TENANT = "qualizeal"
QBANK = [
    ("what does ValidAIte do?", "product"),
    ("what services does QualiZeal offer for AI/ML model testing?", "service"),
    ("what is QMentisAI?", "product"),
    ("what is QualiZeal's mission?", "company"),
]


def main(argv=None):
    argv = argv or sys.argv[1:]
    if not argv:
        print(__doc__); return 1
    folder = Path(argv[0])
    db = argv[argv.index("--db") + 1] if "--db" in argv else os.environ.get("KF_DB", "./data/kf.db")
    p = Platform(db_path=db)
    p.policy.set_budget(TENANT, 20.0)
    intake, worker = Intake(p), IngestWorker(p, None); worker.intake = intake

    docx = sorted(folder.glob("**/*.docx"))
    if not docx:
        print(f"[!] no .docx found under {folder}"); return 1
    print(f"[*] ingesting {len(docx)} QualiZeal documents into tenant '{TENANT}'")
    for path in docx:
        intake.canonical(TENANT, "files", f"file://{path.name}", path.stem,
                         path.read_bytes(), mime="application/vnd.openxmlformats-officedocument.wordprocessingml.document")
        intake.upload(TENANT, path.name, path.read_bytes())
    res = worker.drain()
    for qid, (q, fam) in enumerate(QBANK):
        p.db.execute("INSERT OR REPLACE INTO question_bank(id,tenant,question,expected_docs,family) VALUES(?,?,?,?,?)",
                     (f"{TENANT}-q{qid}", TENANT, q, "", fam))
    ok = [r for r in res if r["status"] in ("ok", "updated")]
    print(f"[✓] ingested {len(ok)} docs · {p.passages.count(TENANT)} passages · "
          f"{sum(r.get('entities',0) for r in ok)} entities")
    print("    try:  python -m knowledge_fabric.cli ask qualizeal asker.public \"what is ValidAIte?\"")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
