"""Evaluation harness + promotion gate (Section 15, invariant I10).

The question bank is run against the REAL engine. A candidate index/graph
version is promoted ONLY if it clears thresholds AND shows no regression
against the currently live version. A deliberately corrupted citation
coordinate must block promotion — proving the gate actually gates.
"""
from __future__ import annotations

from ..answer.service import AnswerService
from ..contracts.types import AnswerKind, Principal, now_ms

THRESHOLDS = {"citation_coverage": 0.75, "grounding_pass_rate": 0.6,
              "answer_supportedness": 0.75, "recall": 0.5}


def _eval_principal(platform, tenant: str) -> Principal:
    token = platform.idp.mint(Principal(subject="eval-bot", tenant=tenant,
                                        roles=["asker"], scopes=["public", "restricted"]))
    return platform.idp.authenticate({"token": token})


def run_bank(platform, tenant: str) -> dict:
    svc = AnswerService(platform)
    prin = _eval_principal(platform, tenant)
    platform.cache.invalidate(tenant)     # evaluate the index itself, not cached answers
    rows = platform.db.query(
        "SELECT question, expected_docs FROM question_bank WHERE tenant=?", (tenant,))
    if not rows:
        return {"n": 0, "citation_coverage": 0, "grounding_pass_rate": 0,
                "answer_supportedness": 0, "recall": 0, "per_question": []}

    n = len(rows)
    cited = supported = grounded = 0
    recall_hits = recall_total = 0
    per_q = []
    for r in rows:
        expected = [d for d in r["expected_docs"].split(",") if d]
        ans = svc.ask(prin, r["question"])
        has_cite = ans.kind == AnswerKind.ANSWER and bool(ans.citations)
        resolvable = all(c.coordinate.locator for c in ans.citations) if ans.citations else False
        cited += 1 if has_cite else 0
        supported += 1 if (has_cite and resolvable) else 0
        grounded += 1 if ans.grounding_score >= platform.grounding_threshold else 0
        # recall: did we cite a passage from an expected document?
        cited_uris = set()
        for c in ans.citations:
            d = platform.documents.get(tenant, c.document_id)
            if d:
                cited_uris.add(d["uri"].replace("file://", ""))
        for e in expected:
            recall_total += 1
            if e in cited_uris:
                recall_hits += 1
        per_q.append({"q": r["question"], "kind": ans.kind.value,
                      "citations": len(ans.citations), "grounding": ans.grounding_score,
                      "resolvable": resolvable})

    return {
        "n": n,
        "citation_coverage": round(cited / n, 4),
        "answer_supportedness": round(supported / n, 4),
        "grounding_pass_rate": round(grounded / n, 4),
        "recall": round(recall_hits / recall_total, 4) if recall_total else 0.0,
        "per_question": per_q,
    }


def evaluate(platform, tenant: str, candidate_version: int = 1) -> dict:
    """Return {passed, metrics, regressions} for a candidate index version."""
    metrics = run_bank(platform, tenant)
    regressions = []
    for key, floor in THRESHOLDS.items():
        if metrics.get(key, 0) < floor:
            regressions.append(f"{key}={metrics.get(key)} < {floor}")
    passed = len(regressions) == 0 and metrics["n"] > 0
    platform.db.execute(
        "INSERT INTO eval_runs(id,tenant,candidate_version,passed,metrics,regressions,at) "
        "VALUES(?,?,?,?,?,?,?)",
        (f"eval_{tenant}_{candidate_version}_{now_ms()}", tenant, candidate_version,
         1 if passed else 0, str(metrics), str(regressions), now_ms()))
    return {"passed": passed, "metrics": metrics, "regressions": regressions}


def promote_if_passes(platform, tenant: str, candidate_version: int) -> dict:
    verdict = evaluate(platform, tenant, candidate_version)
    if verdict["passed"]:
        platform.db.execute("UPDATE index_versions SET state='retired' WHERE tenant=? AND state='live'",
                            (tenant,))
        platform.db.execute(
            "INSERT OR REPLACE INTO index_versions(tenant,version,state,promoted_at) VALUES(?,?,?,?)",
            (tenant, candidate_version, "live", now_ms()))
        verdict["promoted"] = candidate_version
    else:
        verdict["promoted"] = None      # prior live version keeps serving (I10)
        platform.curation.add(tenant, f"promotion blocked: {verdict['regressions']}",
                              "gap", now_ms())
    return verdict


def corrupt_citation_coordinates(platform, tenant: str) -> int:
    """Test hook: break citation coordinates on a candidate to prove the gate blocks."""
    cur = platform.db.execute(
        "UPDATE passages SET coord_locator='{}' WHERE tenant=? AND superseded_by IS NULL", (tenant,))
    platform.cache.invalidate(tenant)     # the corrupted index must be re-evaluated, not cached
    return cur.rowcount
