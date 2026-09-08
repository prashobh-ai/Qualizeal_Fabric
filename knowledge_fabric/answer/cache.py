"""Five cache layers with a savings-by-technique ledger (WS3 · PROVE).

Per the roadmap: "Five cache layers, incl. the model's own prompt memory ·
Savings by technique, not just a total." Each layer records what it *avoided*
so the dashboard can attribute cost saved to a technique rather than a lump
sum. Local in-process caches; the cloud adapter swaps a shared cache (Redis-
class) behind the same methods.

Layers:
  1. answer_cache      — full grounded answer for an identical (tenant, q, acl)
  2. retrieval_cache   — fused candidate ids for a query
  3. embedding_cache   — query embedding vector
  4. graph_cache       — graph-expansion node keys for a query
  5. prompt_memory     — provider prompt-cache discount on the repeated system
                         preamble (the model's own prompt memory)
"""
from __future__ import annotations

import hashlib
import re
import time

_WS = re.compile(r"\s+")


def norm(q: str) -> str:
    return _WS.sub(" ", q.strip().lower())


def _key(*parts) -> str:
    return hashlib.blake2b("|".join(parts).encode(), digest_size=16).hexdigest()


class Cache:
    PROMPT_DISCOUNT = 0.9   # provider prompt-cache: cached input tokens ~90% cheaper

    def __init__(self):
        self.answer: dict = {}
        self.retrieval: dict = {}
        self.embedding: dict = {}
        self.graph: dict = {}
        self._prompt_seen: set = set()
        self._tenant_keys: dict = {}   # tenant -> {(store, key)} for invalidation
        self.stats = {"answer": 0, "retrieval": 0, "embedding": 0, "graph": 0, "prompt_memory": 0}

    def _track(self, tenant, store, key):
        self._tenant_keys.setdefault(tenant, set()).add((store, key))

    def invalidate(self, tenant: str) -> None:
        """Drop a tenant's answer/retrieval/graph caches — call on index change
        (a new candidate index or a promotion must not serve stale answers)."""
        for store, key in self._tenant_keys.pop(tenant, set()):
            getattr(self, store).pop(key, None)

    def acl_key(self, tenant: str, acl: list[str]) -> str:
        return _key(tenant, ",".join(sorted(acl)))

    # ---- answer cache ------------------------------------------------
    def get_answer(self, tenant, q, acl):
        k = _key(tenant, norm(q), self.acl_key(tenant, acl))
        hit = self.answer.get(k)
        if hit:
            self.stats["answer"] += 1
        return hit

    def put_answer(self, tenant, q, acl, payload: dict, cost: float):
        k = _key(tenant, norm(q), self.acl_key(tenant, acl))
        self.answer[k] = {"payload": payload, "cost": cost, "at": time.time()}
        self._track(tenant, "answer", k)

    # ---- retrieval cache --------------------------------------------
    def get_retrieval(self, tenant, q, acl):
        k = _key(tenant, norm(q), self.acl_key(tenant, acl))
        hit = self.retrieval.get(k)
        if hit is not None:
            self.stats["retrieval"] += 1
        return hit

    def put_retrieval(self, tenant, q, acl, fused):
        k = _key(tenant, norm(q), self.acl_key(tenant, acl))
        self.retrieval[k] = fused
        self._track(tenant, "retrieval", k)

    # ---- embedding cache --------------------------------------------
    def get_embedding(self, text):
        k = _key(norm(text))
        hit = self.embedding.get(k)
        if hit is not None:
            self.stats["embedding"] += 1
        return hit

    def put_embedding(self, text, vec):
        self.embedding[_key(norm(text))] = vec

    # ---- graph-expansion cache --------------------------------------
    def get_graph(self, tenant, q, acl):
        k = _key(tenant, norm(q), self.acl_key(tenant, acl))
        hit = self.graph.get(k)
        if hit is not None:
            self.stats["graph"] += 1
        return hit

    def put_graph(self, tenant, q, acl, keys):
        k = _key(tenant, norm(q), self.acl_key(tenant, acl))
        self.graph[k] = keys
        self._track(tenant, "graph", k)

    # ---- prompt memory (provider prompt cache) ----------------------
    def prompt_discount(self, system_preamble: str, input_token_cost: float) -> float:
        """Cost saved by the provider caching the repeated system preamble."""
        k = _key(system_preamble)
        if k in self._prompt_seen:
            self.stats["prompt_memory"] += 1
            return input_token_cost * self.PROMPT_DISCOUNT
        self._prompt_seen.add(k)
        return 0.0
