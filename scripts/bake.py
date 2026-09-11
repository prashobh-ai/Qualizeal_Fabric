"""Coverage bake (T44) — pre-answer what the corpus invites, into ``answers/``.

    python scripts/bake.py --tenant qualizeal [--limit N] [--force]

Loads the fabric (the vendored corpus on a fresh runner, or ``KF_DB``), runs
``knowledge_fabric.baking.bake`` — template questions per document kind plus
one ``question_gen`` model call per document when the model is available —
and bakes every cited answer to ``<fabric root>/answers/<hash>.json``. Skips
documents whose content hash is unchanged since the last bake. Prints the
summary line and appends it (and the ledger line) to ``$GITHUB_STEP_SUMMARY``.
"""

from __future__ import annotations

import argparse
import os
import sys

ROOT = os.path.abspath(os.path.dirname(os.path.dirname(__file__)))
sys.path.insert(0, ROOT)
os.environ.setdefault("KF_LEDGER_PURPOSE", "answer_bake")
os.environ.setdefault("KF_MODEL_MODE", "extractive")

from knowledge_fabric import baking  # noqa: E402
from knowledge_fabric import fabric_data as fd  # noqa: E402
from knowledge_fabric.telemetry import api_ledger  # noqa: E402


def run(tenant: str, *, limit=None, force=False, platform=None, out=print) -> int:
    if platform is None:
        from knowledge_fabric.app import Platform
        from knowledge_fabric.tenants import demo
        from scripts import ingest as _ingest

        platform = Platform(
            db_path=os.environ.get("KF_DB") or ":memory:",
            blob_root=os.path.join(fd.fabric_root(), "blobs"),
        )
        demo.seed(platform, [tenant])
        if not platform.documents.list(tenant):
            _ingest.load_corpus(platform, tenant, out=out)
    s = baking.bake(platform, tenant, limit=limit, force=force, out=out)
    line = baking.summary_line(s)
    ledger = api_ledger.step_summary_line()
    out(line)
    out(ledger)
    api_ledger.append_step_summary(line)
    api_ledger.append_step_summary(ledger)
    return 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="bake", description=__doc__.splitlines()[0])
    ap.add_argument("--tenant", default="qualizeal")
    ap.add_argument("--limit", type=int, default=None, help="bake at most N changed documents")
    ap.add_argument("--force", action="store_true", help="re-bake unchanged documents too")
    args = ap.parse_args(argv)
    return run(args.tenant, limit=args.limit, force=args.force)


if __name__ == "__main__":
    sys.exit(main())
