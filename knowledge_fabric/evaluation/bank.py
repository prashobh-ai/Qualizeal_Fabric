"""Question-bank generation over a loaded corpus (L0.3).

The bank drives the Ask console's suggested questions and the showcase
`bank.json`. It is generated FROM the fabric's real documents, never
hand-written against synthetic fixtures, so every suggestion references a
document that is actually in the fabric.

Method (cheap, no model needed to *generate* — the answer service scores):
  * form candidate questions from each document's title subject and from
    salient graph concepts;
  * run each candidate through the governed answer path with an eval
    principal;
  * keep a candidate only when the answer is confident (>= gate) and cites
    at least two documents (co-occurrence gate) — this is exactly the
    P1.6 suggestion filter, so a generated bank entry is always a valid
    suggestion.

`expected_docs` is stored as the cited documents' source URIs, so the
ACL-filtered `/api/suggestions` endpoint shows a question only to a
principal who can retrieve every one of its supporting documents.
"""

from __future__ import annotations

import re

from ..contracts.types import AnswerKind

_CODE_PREFIX = re.compile(r"^\s*(?:\d+[\s._-]*)+")  # "03 Product ValidAIte" -> "Product ValidAIte"
_GENERIC = {
    "product",
    "products",
    "service",
    "services",
    "overview",
    "document",
    "policy",
    "guide",
    "company",
    "the",
    "a",
    "an",
    "of",
    "and",
    "for",
}
CONFIDENCE_GATE = 0.56
MIN_CITED_DOCS = 2


def _subject(title: str) -> str:
    """The salient subject of a document title (drop a leading code and generic words)."""
    t = _CODE_PREFIX.sub("", title or "").strip()
    words = [w for w in re.split(r"[\s/_-]+", t) if w and w.lower() not in _GENERIC]
    return " ".join(words).strip()


def _candidates(platform, tenant: str) -> list[str]:
    """Candidate questions from document titles (definition + coverage form)."""
    seen, out = set(), []
    for d in platform.documents.list(tenant):
        subj = _subject(d.get("title", ""))
        if not subj or subj.lower() in seen:
            continue
        seen.add(subj.lower())
        out.append(f"what is {subj}?")
        out.append(f"what does {subj} cover?")
    return out


def _doc_uri(platform, tenant: str, document_id: str) -> str:
    d = platform.documents.get(tenant, document_id)
    if not d:
        return ""
    return (d.get("uri") or "").replace("file://", "").replace("upload://", "")


def generate(platform, tenant: str, principal=None) -> int:
    """Regenerate the tenant's question bank from its documents. Returns the
    number of bank entries written. Replaces any prior bank for the tenant."""
    from ..answer.service import AnswerService
    from ..contracts.types import Principal

    svc = AnswerService(platform)
    if principal is None:
        token = platform.idp.mint(
            Principal(
                subject="bank-bot", tenant=tenant, roles=["asker"], scopes=["public", "restricted"]
            )
        )
        principal = platform.idp.authenticate({"token": token})

    platform.db.execute("DELETE FROM question_bank WHERE tenant=?", (tenant,))
    kept = 0
    for q in _candidates(platform, tenant):
        ans = svc.ask(principal, q)
        if ans.kind != AnswerKind.ANSWER or ans.confidence < CONFIDENCE_GATE:
            continue
        uris = sorted({_doc_uri(platform, tenant, c.document_id) for c in ans.citations})
        uris = [u for u in uris if u]
        if len(uris) < MIN_CITED_DOCS:
            continue
        family = "definition" if q.startswith("what is") else "coverage"
        platform.db.execute(
            (
                "INSERT OR REPLACE INTO question_bank(id,tenant,question,expected_docs,family) "
                "VALUES(?,?,?,?,?)"
            ),
            (f"{tenant}-gen{kept}", tenant, q, ",".join(uris), family),
        )
        kept += 1
    return kept
