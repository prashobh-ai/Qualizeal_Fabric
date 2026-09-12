"""T99 — cross-source verification: corroborate a claim across two sources.

The architecture Rasool asked for, usable now for the Jira-vs-repo case and
generalised so any source pair works next phase without rework. Every source
implements one small interface:

* ``state_of(entity)`` — the source's current view of the entity (a Jira issue's
  status; whether the repo shows code referencing it);
* ``evidence_for(entity, state)`` — the citations that back that view.

:func:`verify` resolves the entity in each named source, pulls each state, and
reports **agreement or the specific discrepancy** with citations from *both* —
never asserting beyond the evidence, and saying what it could not find. It uses
the ``relationships`` graph (commits/PRs that mention an issue key, discovered at
ingestion) so the repo lookup is a graph query, not a re-scan; a live Jira
callable is preferred for the issue's current status, falling back to the
ingested issue document.

Keyless by design: no model is required — the two-source answer is composed
directly, so it works on the open-source/extractive path the demo runs on.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field

from ..contracts.types import Citation, CoordinateKind
from ..relationships import ISSUE_KEY
from . import aggregate
from .aggregate import cite

# "verify this against that", "cross-check", "corroborate", "does the code match"
_VERIFY = re.compile(
    r"\b(verif(?:y|ies|ied|ication)|cross[- ]?check|corroborate|reconcile|"
    r"double[- ]?check|confirm(?:ed|s)?|really (?:done|complete)|actually (?:done|complete))\b",
    re.I,
)


def issue_keys_in(text: str) -> list[str]:
    """Every Jira-style issue key in the text, order-preserving and de-duplicated."""
    out: list[str] = []
    for k in ISSUE_KEY.findall(text or ""):
        if k not in out:
            out.append(k)
    return out


def wants_verification(question: str) -> bool:
    """A cross-source verify intent: a verify/cross-check verb, and either an
    issue key or a nod to the repo/code as a second source."""
    q = question or ""
    if not _VERIFY.search(q):
        return False
    return bool(
        issue_keys_in(q)
        or re.search(r"\b(repo|repository|code|commit|commits|pull request|prs?|github)\b", q, re.I)
    )


# Jira statuses that mean the work is finished — a "done" claim to corroborate.
DONE_LIKE = {
    "done",
    "closed",
    "resolved",
    "released",
    "complete",
    "completed",
    "shipped",
    "merged",
    "deployed",
}
CODE_KINDS = ["commit", "pull_request"]


@dataclass
class CrossSourceResult:
    text: str = ""
    citations: list[Citation] = field(default_factory=list)
    verdict: str = ""  # agree | discrepancy | partial | insufficient
    explain: str = ""
    entity: str = ""
    per_source: dict = field(default_factory=dict)


# --------------------------------------------------------------------------
# source adapters
# --------------------------------------------------------------------------
class JiraSource:
    kind = "jira"

    def __init__(self, platform, principal, live_jql=None):
        self.p = platform
        self.principal = principal
        self.tenant = principal.tenant
        self.live_jql = live_jql

    def _ingested(self, key: str) -> dict | None:
        for d in self.p.documents.list(self.tenant):
            uri = d.get("uri") or ""
            if uri.startswith("jira://") and uri.rsplit("/", 1)[-1] == key:
                return d
        return None

    def state_of(self, key: str) -> dict:
        # prefer the live source for the current status
        if self.live_jql is not None:
            try:
                raw = self.live_jql(f"key = {key}", ["status", "summary", "updated"])
                issues = raw.get("issues", []) if isinstance(raw, dict) else list(raw or [])
                if issues:
                    it = issues[0]
                    status = aggregate._live_issue_field(it, "status") or (it.get("status") or "")
                    f = it.get("fields") if isinstance(it.get("fields"), dict) else it
                    return {
                        "found": True,
                        "status": str(status),
                        "summary": str(f.get("summary") or it.get("summary") or ""),
                        "as_of": aggregate.as_of_text(None) + " (live JQL)",
                        "url": self._url(key),
                        "live": True,
                    }
            except Exception:  # live failed — fall back to the ingested issue
                pass
        doc = self._ingested(key)
        if doc:
            try:
                meta = json.loads(doc.get("meta") or "{}")
            except (TypeError, ValueError):
                meta = {}
            jira = meta.get("jira") or {}
            return {
                "found": True,
                "status": str(jira.get("status") or ""),
                "summary": (doc.get("title") or "").split("·", 1)[-1].strip(),
                "as_of": aggregate.as_of_text({"as_of": meta.get("as_of")}),
                "url": str(meta.get("citation_url") or self._url(key)),
                "live": False,
            }
        return {"found": False, "status": "", "url": self._url(key)}

    def _url(self, key: str) -> str:
        return aggregate._jira_url(key.split("-")[0], {})

    def evidence_for(self, key: str, state: dict) -> list[Citation]:
        if not state.get("found"):
            return []
        return [
            cite(f"Jira {key}", state.get("url", ""), f"jira:{key}", {"key": key, "source": "jira"})
        ]


class RepoSource:
    kind = "repo"

    def __init__(self, platform, principal):
        self.p = platform
        self.tenant = principal.tenant

    def state_of(self, key: str) -> dict:
        rows = self.p.relationships.mentions_of(self.tenant, "issue", key, subject_kinds=CODE_KINDS)
        return {"found": bool(rows), "references": rows}

    def evidence_for(self, key: str, state: dict) -> list[Citation]:
        out = []
        for r in state.get("references", [])[:5]:
            title = r.get("evidence_title") or r.get("subject_id") or "code reference"
            out.append(
                cite(
                    f"{r.get('subject_kind', 'code')} {r.get('subject_id', '')}".strip(),
                    r.get("evidence_url", ""),
                    r.get("evidence_id", "") or f"{r.get('subject_kind')}:{r.get('subject_id')}",
                    {"ref": r.get("subject_id"), "kind": r.get("subject_kind"), "source": "repo"},
                    CoordinateKind.SYMBOL_LINE,
                    title,
                )
            )
        return out


# --------------------------------------------------------------------------
# the corroboration
# --------------------------------------------------------------------------
def _ref_list(rows: list[dict]) -> str:
    return ", ".join(
        f"{r.get('subject_kind', 'ref').replace('_', ' ')} {r.get('subject_id', '')}".strip()
        for r in rows[:5]
    )


def verify(platform, principal, claim: str, *, live_jql=None) -> CrossSourceResult | None:
    """Corroborate a Jira issue's state against the repository. Returns a
    two-source result (agreement or the specific gap) or ``None`` when no Jira
    issue key is present to resolve."""
    keys = issue_keys_in(claim)
    if not keys:
        return None
    key = keys[0]
    jira = JiraSource(platform, principal, live_jql=live_jql)
    repo = RepoSource(platform, principal)
    js = jira.state_of(key)
    rs = repo.state_of(key)
    cites: list[Citation] = []

    if not js["found"]:
        # cannot resolve the issue — say so, and report any code that names it
        cites += repo.evidence_for(key, rs)
        if rs["found"]:
            text = (
                f"I could not find Jira issue {key} in the fabric, so I cannot confirm its status. "
                f"The repository does reference {key}: {_ref_list(rs['references'])} [1]."
            )
        else:
            text = (
                f"I could not find Jira issue {key} in the fabric, and no commit or pull request "
                f"references it — there is nothing to corroborate."
            )
        return CrossSourceResult(
            text,
            cites,
            "insufficient",
            f"No ingested/live Jira issue {key}.",
            key,
            {"jira": js, "repo": rs},
        )

    cites += jira.evidence_for(key, js)  # [1]
    repo_cites = repo.evidence_for(key, rs)
    done = js["status"].strip().lower() in DONE_LIKE
    n_refs = len(rs["references"])

    if done and rs["found"]:
        cites += repo_cites
        tail = " [2]" + (
            "".join(f"[{i}]" for i in range(3, 2 + min(n_refs, 5))) if n_refs > 1 else ""
        )
        text = (
            f"Agreement. Jira {key} is marked {js['status']} ({js['as_of']}) [1], and the "
            f"repository corroborates it — {n_refs} reference(s): {_ref_list(rs['references'])}"
            f"{tail}."
        )
        verdict = "agree"
    elif done and not rs["found"]:
        text = (
            f"Discrepancy. Jira {key} is marked {js['status']} ({js['as_of']}) [1], but the "
            f"repository shows no commit or pull request referencing {key} — the code change is "
            f"not evident. Either the work is tracked elsewhere or the code has not landed."
        )
        verdict = "discrepancy"
    elif not done and rs["found"]:
        cites += repo_cites
        status = js["status"] or "in an open state"
        text = (
            f"Jira {key} is {status} — not done — as of {js['as_of']} [1], yet the repository "
            f"already references it: {_ref_list(rs['references'])} [2]. The code appears to be "
            f"in progress ahead of the ticket."
        )
        verdict = "partial"
    else:
        status = js["status"] or "in an open state"
        text = (
            f"Jira {key} is {status} ({js['as_of']}) [1], and no commit or pull request references "
            f"{key} yet — the two sources agree the work is not complete."
        )
        verdict = "agree"
    return CrossSourceResult(
        text,
        cites,
        verdict,
        f"Compared Jira status of {key} against code references in the relationships graph.",
        key,
        {"jira": js, "repo": rs},
    )
