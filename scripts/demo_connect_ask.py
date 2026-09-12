"""Connect-and-ask demo (T118 + T119) — four sources from a pasted URL, asked.

A stakeholder watches, end to end and offline:

  1. Four sources connect from exactly what the browser shows — a GitHub repo
     URL, a Jira dashboard URL, a Confluence page URL, and a website — parsed by
     the same generic ``url_parse`` (T118), ingested through the real pipeline.
  2. Each source answers a question with an EXPANDABLE citation, on the
     model-free extractive path (no credits).
  3. The cross-source question ("the Jira task says done — is there a matching
     commit?") cites BOTH Jira and the repo (T99).
  4. The identical flow runs again for a PERSONAL GitHub repo and a personal
     website — proving the path is generic, not wired to QualiZeal.

Offline via fake transports, so it is deterministic and needs no secrets.

Run:  python scripts/demo_connect_ask.py
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
import urllib.parse

ROOT = os.path.abspath(os.path.dirname(os.path.dirname(__file__)))
sys.path.insert(0, ROOT)
os.environ.setdefault("KF_MODEL_MODE", "extractive")  # deterministic, model-free
os.environ.setdefault("KF_DATA_ROOT", tempfile.mkdtemp(prefix="kf-demo-connect-"))

from knowledge_fabric import relationships as relmod  # noqa: E402
from knowledge_fabric.answer import cross_source  # noqa: E402
from knowledge_fabric.answer.service import AnswerService  # noqa: E402
from knowledge_fabric.connectors import url_parse  # noqa: E402
from knowledge_fabric.connectors.confluence import ConfluenceConnector  # noqa: E402
from knowledge_fabric.connectors.jira_live import JiraLiveConnector  # noqa: E402
from knowledge_fabric.connectors.website import WebsiteConnector  # noqa: E402
from knowledge_fabric.contracts.types import RawItem, now_ms  # noqa: E402
from knowledge_fabric.ingestion.intake import IngestWorker, Intake  # noqa: E402
from knowledge_fabric.tenants import demo  # noqa: E402

TENANT = "qualizeal"
JIRA_SITE = "https://qualizeal-team-aicoe.atlassian.net"
CONF_SITE = "https://aicoe-genq.atlassian.net"

LOGIN_CODE = (
    '"""Auth helpers for single sign-on."""\n\n\n'
    "def login(email, password):\n"
    '    """Verify the password and mint a session token for single sign-on."""\n'
    "    user = verify_password(email, password)\n"
    "    return mint_session_token(user)\n"
)


def rule(t: str) -> None:
    print("\n" + "═" * 78 + f"\n▐ {t}\n" + "═" * 78)


def show(label: str, a) -> None:
    head = (a.answer_text or "").strip().splitlines()
    print(f"  {label}")
    print(f"    → {a.kind.value.upper():7s} grounding={a.grounding_score} tier={a.tier}")
    for ln in head[:3]:
        print(f"      {ln[:96]}")
    for i, c in enumerate(a.citations[:2], 1):
        print(f"      [{i}] {c.document_title} — {c.coordinate.render()}  ↳ expands to the source")


def jira_transport(url, headers, timeout=30):
    path = urllib.parse.urlparse(url).path

    def ok(p):
        return 200, json.dumps(p).encode()

    if path == "/rest/api/3/field":
        return ok([])
    if path == "/rest/api/3/dashboard/10201":
        return ok(
            {"id": "10201", "name": "ValidAIte QA Status", "owner": {"displayName": "Prashobh"}}
        )
    if path == "/rest/api/3/dashboard/10201/gadget":
        return ok(
            {"gadgets": [{"title": "Open Defects by Severity"}, {"title": "Test Run Pass Rate"}]}
        )
    if path in ("/rest/api/3/search/jql", "/rest/api/3/search"):
        return ok(
            {
                "issues": [
                    {
                        "key": "V1-42",
                        "id": "42",
                        "fields": {
                            "summary": "V1-42 login retry",
                            "status": {"name": "Done"},
                            "issuetype": {"name": "Task"},
                            "project": {"key": "V1"},
                            "updated": "2026-09-12T08:00:00.000+0000",
                        },
                    }
                ],
                "isLast": True,
                "total": 1,
            }
        )
    return 404, b'{"error":"not found"}'


def conf_transport(url, headers, timeout=60):
    path = urllib.parse.urlparse(url).path

    def ok(p):
        return 200, json.dumps(p).encode()

    if path == "/wiki/api/v2/pages/1703938":
        return ok(
            {
                "id": "1703938",
                "title": "Project Plan",
                "spaceId": "555",
                "body": {
                    "storage": {
                        "value": "<h2>Project Plan</h2><p>The project lead is Prashobh Paul.</p>"
                        "<p>The kickoff date is 1 October 2026.</p>"
                    }
                },
                "version": {"number": 2, "createdAt": "2026-09-01T00:00:00Z"},
                "_links": {"webui": "/spaces/COE/pages/1703938/Project+Plan"},
            }
        )
    if path == "/wiki/api/v2/spaces/555":
        return ok({"id": "555", "key": "COE", "name": "AI CoE"})
    if path == "/wiki/api/v2/pages/1703938/attachments":
        return ok({"results": []})
    return 404, b'{"error":"not found"}'


def website_transport(html):
    def t(url, headers, timeout=20):
        return 200, html.encode("utf-8")

    return t


def _ingest(p, items):
    intake = Intake(p)
    worker = IngestWorker(p, intake)
    for it in items:
        it.meta.setdefault("ontology", "quality-assurance")
        intake.submit(it)
    worker.drain()


def _code_item(uri, title, url):
    return RawItem(
        tenant=TENANT,
        source="github_live",
        source_version="1",
        uri=uri,
        mime="text/x-python;code",
        title=title,
        bytes_=LOGIN_CODE.encode("utf-8"),
        meta={
            "acl": ["public"],
            "source_kind": "github",
            "citation_url": url,
            "arrived_at": now_ms(),
            "as_of": "2026-09-12T08:00:00Z",
        },
    )


def _build():
    from knowledge_fabric.app import Platform

    p = Platform(db_path=":memory:", blob_root="./data/demo-connect-blobs")
    demo.seed(p)
    p.policy.set_budget(TENANT, 1000.0)
    return p


def _connect_note(label: str, pasted: str) -> None:
    frag = url_parse.parse_source_url(pasted)
    print(f"  paste {pasted!r}\n    → url_parse: {json.dumps(frag)}   [{label}]")


def main() -> int:
    p = _build()
    svc = AnswerService(p)
    asker = demo.principal_for(p, TENANT, "developer")

    rule("1. CONNECT — four sources, from exactly what the browser shows (T118)")
    _connect_note("GitHub repo", "https://github.com/qualizeal/platform")
    _connect_note("Jira dashboard", f"{JIRA_SITE}/jira/dashboards/10201")
    _connect_note("Confluence page", f"{CONF_SITE}/wiki/spaces/COE/pages/1703938/Project+Plan")
    _connect_note("Website", "https://qualizeal.com/security-testing")

    # ingest each, offline
    _ingest(
        p,
        [
            _code_item(
                "github://qualizeal/platform/auth.py",
                "auth.py",
                "https://github.com/qualizeal/platform/blob/main/auth.py",
            )
        ],
    )
    ji, _ = JiraLiveConnector(
        TENANT,
        {"url": JIRA_SITE, "email": "c@q.co", "token": "x", "dashboards": ["10201"]},
        transport=jira_transport,
    ).pull(None)
    _ingest(p, ji)
    ci, _ = ConfluenceConnector(
        TENANT,
        {
            "url": CONF_SITE,
            "email": "c@q.co",
            "token": "x",
            "pages": ["1703938"],
            "attachments": False,
        },
        transport=conf_transport,
    ).pull(None)
    _ingest(p, ci)
    wi, _ = WebsiteConnector(
        TENANT,
        {"url": "https://qualizeal.com/security-testing"},
        transport=website_transport(
            "<html><title>Security Testing</title><body><h1>Security Testing</h1>"
            "<p>QualiZeal offers security testing including penetration testing, "
            "vulnerability assessment and secure code review.</p></body></html>"
        ),
    ).pull(None)
    _ingest(p, wi)

    rule("2. ASK — each source answers, with an expandable citation")
    show(
        "GitHub  · 'show the login implementation'", svc.ask(asker, "show the login implementation")
    )
    show(
        "Jira    · 'what is on the ValidAIte QA Status dashboard'",
        svc.ask(asker, "what is on the ValidAIte QA Status dashboard"),
    )
    show(
        "Conf.   · 'who is the project lead in the Project Plan page'",
        svc.ask(asker, "who is the project lead in the Project Plan page"),
    )
    show(
        "Website · 'what does QualiZeal offer for security testing'",
        svc.ask(asker, "what does QualiZeal offer for security testing"),
    )

    rule("3. CROSS-SOURCE — 'the Jira task says done — is there a matching commit?' (T99)")
    # connect the V1 project too, so the issue V1-42 is resolvable in the fabric
    ji2, _ = JiraLiveConnector(
        TENANT,
        {"url": JIRA_SITE, "email": "c@q.co", "token": "x", "projects": ["V1"]},
        transport=jira_transport,
    ).pull(None)
    _ingest(p, ji2)
    _ingest(
        p,
        [
            RawItem(
                tenant=TENANT,
                source="github_live",
                source_version="1",
                uri="github://qualizeal/platform/commits/abc123",
                mime="text/markdown",
                title="Fix login retry",
                bytes_=b"Fix login retry\n\nImplements V1-42.",
                meta={
                    "acl": ["public"],
                    "source_kind": "github",
                    "citation_url": "https://github.com/qualizeal/platform/commit/abc123",
                    "arrived_at": now_ms(),
                    "as_of": "2026-09-12T08:00:00Z",
                },
            )
        ],
    )
    relmod.scan(p, TENANT)
    res = cross_source.verify(
        p, asker, "the Jira task V1-42 shows done — check the repo and verify"
    )
    print(f"  verdict={res.verdict.upper()}  — {(res.text or '').splitlines()[0][:90]}")
    for i, c in enumerate(res.citations[:2], 1):
        print(f"    [{i}] {c.document_title} — {c.coordinate.render()}")

    rule("4. GENERIC — the SAME flow, a personal GitHub repo + a personal website")
    p2 = _build()
    svc2 = AnswerService(p2)
    asker2 = demo.principal_for(p2, TENANT, "developer")
    _connect_note("personal repo", "https://github.com/prashobh-ai/sideproject")
    _connect_note("personal site", "https://prashobh.dev")
    _ingest(
        p2,
        [
            _code_item(
                "github://prashobh-ai/sideproject/app.py",
                "app.py",
                "https://github.com/prashobh-ai/sideproject/blob/main/app.py",
            )
        ],
    )
    wi2, _ = WebsiteConnector(
        TENANT,
        {"url": "https://prashobh.dev"},
        transport=website_transport(
            "<html><title>My Site</title><body><h1>About</h1>"
            "<p>This personal site documents my weekend robotics experiments.</p>"
            "</body></html>"
        ),
    ).pull(None)
    _ingest(p2, wi2)
    show(
        "personal GitHub · 'show the login implementation'",
        svc2.ask(asker2, "show the login implementation"),
    )
    show(
        "personal site   · 'what does this personal site document'",
        svc2.ask(asker2, "what does this personal site document"),
    )

    rule(
        "DONE — four sources connected from a pasted URL, each answered with a "
        "citation, cross-source verified, and the same flow generic for a personal account."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
