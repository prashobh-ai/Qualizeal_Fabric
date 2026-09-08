"""Knowledge Fabric CLI — the third intake door plus operator commands.

    python -m knowledge_fabric.cli seed
    python -m knowledge_fabric.cli ingest <tenant> <path> [--acl public,restricted]
    python -m knowledge_fabric.cli ask <tenant> <subject> "<question>"
    python -m knowledge_fabric.cli metrics <tenant>
    python -m knowledge_fabric.cli gaps <tenant>
    python -m knowledge_fabric.cli eval <tenant>
    python -m knowledge_fabric.cli serve
"""
from __future__ import annotations

import json
import os
import sys

from .app import Platform
from .answer.service import AnswerService
from .ingestion.intake import Intake, IngestWorker
from .tenants import demo


def _platform() -> Platform:
    return Platform(db_path=os.environ.get("KF_DB", "./data/kf.db"))


def main(argv=None):
    argv = argv or sys.argv[1:]
    if not argv:
        print(__doc__)
        return 0
    cmd, rest = argv[0], argv[1:]
    p = _platform()

    if cmd == "seed":
        print(json.dumps(demo.seed(p), indent=2))
    elif cmd == "ingest":
        tenant, path = rest[0], rest[1]
        acl = ["public"]
        if "--acl" in rest:
            acl = rest[rest.index("--acl") + 1].split(",")
        intake, worker = Intake(p), IngestWorker(p, None)
        worker.intake = intake
        with open(path, "rb") as fh:
            data = fh.read()
        intake.upload(tenant, os.path.basename(path), data, acl=acl)
        print(json.dumps(worker.drain(), indent=2))
    elif cmd == "ask":
        tenant, subject, question = rest[0], rest[1], rest[2]
        prin = demo.principal_for(p, tenant, subject)
        ans = AnswerService(p).ask(prin, question)
        print(json.dumps(ans.to_dict(), indent=2))
    elif cmd == "metrics":
        print(json.dumps(p.telemetry.metrics(rest[0]), indent=2))
    elif cmd == "gaps":
        print(json.dumps(p.curation.list(rest[0]), indent=2))
    elif cmd == "eval":
        from .evaluation import gate
        print(json.dumps(gate.evaluate(p, rest[0]), indent=2, default=str))
    elif cmd == "serve":
        from .surfaces.http_api import serve
        serve(port=int(os.environ.get("KF_PORT", "8080")))
    else:
        print(f"unknown command: {cmd}\n{__doc__}")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
