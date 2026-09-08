"""Lexical (BM25) index over tenant passages (Postgres tsvector/BM25 in cloud).

BM25 is computed over the tenant's live passages at query time. The ACL
filter is applied while building the candidate set (I6); tenant scoping is in
the SQL (I5). Demo-scale simple; the contract is identical to the cloud
adapter that pushes this into the database engine.
"""
from __future__ import annotations

import json
import math
import re
from collections import Counter

_TOKEN = re.compile(r"[a-z0-9]+")


def _tok(s: str) -> list[str]:
    return _TOKEN.findall(s.lower())


class SqlLexicalIndex:
    def __init__(self, db: Database):  # type: ignore[name-defined]
        self.db = db

    def index(self, tenant: str, passages) -> None:
        # Passages already live in the store; BM25 reads them at query time.
        return

    def delete(self, tenant: str, ids: list[str]) -> None:
        return

    def search(self, tenant: str, query: str, k: int, acl: list[str]) -> list[tuple[str, float]]:
        rows = self.db.query(
            "SELECT id, text, acl FROM passages WHERE tenant=? AND superseded_by IS NULL", (tenant,))
        docs = []
        for r in rows:
            if not (set(json.loads(r["acl"])) & set(acl)):   # pre-rank permission filter
                continue
            docs.append((r["id"], _tok(r["text"])))
        if not docs:
            return []
        N = len(docs)
        df = Counter()
        for _, toks in docs:
            for t in set(toks):
                df[t] += 1
        avgdl = sum(len(toks) for _, toks in docs) / N
        q = _tok(query)
        k1, b = 1.5, 0.75
        scored = []
        for pid, toks in docs:
            tf = Counter(toks)
            dl = len(toks)
            score = 0.0
            for term in q:
                if term not in tf:
                    continue
                idf = math.log(1 + (N - df[term] + 0.5) / (df[term] + 0.5))
                denom = tf[term] + k1 * (1 - b + b * dl / avgdl)
                score += idf * (tf[term] * (k1 + 1)) / denom
            if score > 0:
                scored.append((pid, score))
        scored.sort(key=lambda x: x[1], reverse=True)
        return scored[:k]
