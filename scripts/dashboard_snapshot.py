"""Render a self-contained snapshot of the telemetry dashboard.

Seeds a tenant, drives a realistic spread of questions (multiple users, roles,
languages, repeats for cache hits), then embeds the real /api/analytics and
/admin/sources payloads into the dashboard HTML behind a fetch shim so the
page renders exactly as served — but with no server needed.
"""
from __future__ import annotations

import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("KF_MODEL_MODE", "mock")

from knowledge_fabric.app import Platform
from knowledge_fabric.answer.service import AnswerService
from knowledge_fabric.ingestion.sync import SyncManager
from knowledge_fabric.surfaces.dashboard import DASHBOARD_HTML
from knowledge_fabric.tenants import demo

TENANT = "q-quality"
SCRIPT = [
    ("asker.public", "what must a release achieve before promotion?"),
    ("asker.public", "why does a component with an open defect block dependent releases?"),
    ("asker.public", "which requirement has a traceability gap?"),
    ("asker.public", "what blocks the release according to the standup?"),
    ("asker.public", "what must a release achieve before promotion?"),        # cache hit
    ("curator", "how fast must critical defects be triaged?"),
    ("curator", "compare acceptance criteria across the strategy and the runbook"),
    ("asker.public", "how fast must critical defects be triaged?"),
    ("asker.public", "quel est le critère d acceptation pour la couverture?"),  # FR
    ("asker.public", "¿cuál es el criterio de aceptación para la cobertura?"),  # ES
    ("qa-agent", "what is required before a release is promoted?"),
    ("asker.public", "what is the capital of France?"),                        # gap
    ("asker.public", "list the requirement with a traceability gap"),
    ("curator", "why does an open defect block its dependent releases?"),
]


def main():
    p = Platform(db_path=":memory:", blob_root="./data/snap-blobs")
    demo.seed(p, [TENANT])
    svc = AnswerService(p)
    for user, q in SCRIPT:
        try:
            svc.ask(demo.principal_for(p, TENANT, user), q)
        except Exception as e:
            print("skip", user, e)
    analytics = p.telemetry.analytics(TENANT, "7d")
    sources = {"sources": SyncManager(p).source_health(TENANT)}

    shim = f"""<script>
    const _A={json.dumps(analytics)}; const _S={json.dumps(sources)};
    window.fetch=async(url)=>{{
      if(url.indexOf('/login')===0) return {{json:async()=>({{token:'snapshot'}})}};
      if(url.indexOf('/api/analytics')===0) return {{json:async()=>_A}};
      if(url.indexOf('/admin/sources')===0) return {{json:async()=>_S}};
      return {{json:async()=>({{}})}};
    }};
    </script>"""
    html = DASHBOARD_HTML.replace("<body>", "<body>\n" + shim, 1)
    out = os.environ.get("KF_SNAPSHOT", "/tmp/claude-0/kf_dashboard_snapshot.html")
    os.makedirs(os.path.dirname(out), exist_ok=True)
    with open(out, "w") as fh:
        fh.write(html)
    print("answers:", analytics["answers"], "| levels:", analytics["routing_by_level"],
          "| savings:", analytics["savings_by_technique"])
    print("snapshot:", out)


if __name__ == "__main__":
    main()
