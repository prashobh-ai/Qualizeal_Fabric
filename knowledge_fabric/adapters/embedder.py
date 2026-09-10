"""Deterministic, self-hosted, versioned embedder (Embedder contract, I9).

No network, no GPU, no model weights: a hashing embedder projects token
unigrams and bigrams into a fixed-dimensional space and L2-normalises. It is
fully deterministic and stable across runs and machines, which is exactly
what invariant I9 requires ("embeddings are never re-computed at a provider
swap"). On a GPU box this adapter is swapped for a bge-m3-class server behind
the same three methods — nothing else changes.
"""

from __future__ import annotations

import hashlib
import math
import re

_TOKEN = re.compile(r"[a-z0-9]+")
_MODEL_ID = "kf-hash-embed-v1"
_DIM = 256


def _tokens(text: str) -> list[str]:
    return _TOKEN.findall(text.lower())


class HashingEmbedder:
    def __init__(self, dim: int = _DIM):
        self._dim = dim

    def model_id(self) -> str:
        return _MODEL_ID

    def dim(self) -> int:
        return self._dim

    def _feature(self, feat: str, vec: list[float]) -> None:
        h = hashlib.blake2b(feat.encode(), digest_size=8).digest()
        idx = int.from_bytes(h[:4], "big") % self._dim
        sign = 1.0 if h[4] & 1 else -1.0
        vec[idx] += sign

    def embed(self, texts: list[str]) -> list[list[float]]:
        out = []
        for t in texts:
            vec = [0.0] * self._dim
            toks = _tokens(t)
            for tok in toks:
                self._feature(tok, vec)
            for a, b in zip(toks, toks[1:], strict=False):
                self._feature(a + "_" + b, vec)
            norm = math.sqrt(sum(x * x for x in vec)) or 1.0
            out.append([x / norm for x in vec])
        return out


def cosine(a: list[float], b: list[float]) -> float:
    return sum(x * y for x, y in zip(a, b, strict=False))
