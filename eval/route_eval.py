"""Routing evaluation (L0.4 stub; F7.5 expands it).

Runs the question bank through the full answer path and reports the level
distribution, so a demo can prove "most questions in the lower two levels".
Writes `eval/route_eval.md`.

    python eval/route_eval.py                 # human report + markdown
    python eval/route_eval.py --json          # machine-readable

Standard library only. Uses the synthetic corpus fixture when no real
corpus is loaded; F7.5 will point it at the loaded `qualizeal` fabric.
"""
from __future__ import annotations

import argparse
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("KF_MODEL_MODE", "mock")

from knowledge_fabric.answer.service import AnswerService     # noqa: E402
from knowledge_fabric.app import Platform                     # noqa: E402
from knowledge_fabric.tenants import demo                     # noqa: E402

LEVEL_WORDS = {0: "Look it up", 1: "Quote it", 2: "Summarise it", 3: "Reason about it", 4: "Escalation"}


def run(tenant: str = "route-fabric") -> dict:
    from tests.fixtures import synthetic_corpus
    p = Platform(db_path=":memory:", blob_root="./data/route-blobs")
    demo.seed(p)
    synthetic_corpus.load_into(p, tenant)
    svc = AnswerService(p)
    prin = demo.principal_for(p, tenant, "asker.restricted")
    rows = p.db.query("SELECT question FROM question_bank WHERE tenant=?", (tenant,))
    dist: dict[int, int] = {}
    per_q = []
    for r in rows:
        a = svc.ask(prin, r["question"])
        lvl = a.level
        dist[lvl] = dist.get(lvl, 0) + 1
        per_q.append({"question": r["question"], "level": lvl,
                      "level_word": LEVEL_WORDS.get(lvl, str(lvl)),
                      "reasons": [x.get("code") for x in (a.why or {}).get("reasons", [])]})
    n = len(per_q) or 1
    lower_two = sum(v for k, v in dist.items() if k <= 1)
    return {
        "n": len(per_q),
        "distribution": {LEVEL_WORDS.get(k, str(k)): v for k, v in sorted(dist.items())},
        "lower_two_share": round(lower_two / n, 3),
        "per_question": per_q,
    }


def render(rep: dict) -> str:
    lines = ["# Routing evaluation", "",
             f"Questions: {rep['n']}", "",
             "## Level distribution", ""]
    for word, count in rep["distribution"].items():
        lines.append(f"- {word}: {count}")
    lines += ["", f"Lower-two (Look it up + Quote it) share: "
                  f"{rep['lower_two_share'] * 100:.0f}%", "",
              "## Per question", ""]
    for q in rep["per_question"]:
        lines.append(f"- [{q['level_word']}] {q['question']}  "
                     f"({', '.join(c for c in q['reasons'] if c)})")
    return "\n".join(lines) + "\n"


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="route_eval")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--out", default=os.path.join(os.path.dirname(__file__), "route_eval.md"))
    args = ap.parse_args(argv)
    rep = run()
    if args.json:
        print(json.dumps(rep, indent=2, sort_keys=True))
    else:
        text = render(rep)
        print(text)
        with open(args.out, "w", encoding="utf-8") as fh:
            fh.write(text)
    return 0


if __name__ == "__main__":
    sys.exit(main())
