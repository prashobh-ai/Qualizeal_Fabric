"""Source / asset discovery search (T24).

Employees ask capability questions — "has anyone made SSO/auth code I can
reuse?", "is there an automation script for this suite?", "where is the leave
policy?" — that must return a ranked LIST of real organisation assets (files,
functions, documents), not a single synthesised answer, and must never
blind-gap regardless of the asker's role.

A ``SourceSearcher`` answers such a query. Two implementations, behind one
interface:

* ``IndexedSourceSearcher`` runs over the ingested fabric (BM25 lexical + a code
  identifier tier), grouped to one hit per document. It works everywhere,
  including the static showcase, where its results are baked.
* ``GitHubCodeSearcher`` searches GitHub live (a real backend with a token) and
  is dormant without one. Its hits are line-anchored to source on GitHub.

The hits a searcher returns are exactly the augmentation context an LLM would be
handed at the generation step of RAG — "search on sources itself, later fed to
the model". ``discover`` fans out across the configured searchers, dedupes and
ranks, so a discovery answer draws on the fabric and, when configured, live
sources together.
"""

from __future__ import annotations

import json
import os
import re
import urllib.request
from dataclasses import dataclass, field

_TOKEN = re.compile(r"[a-z0-9]+")
# Capability / asset-discovery intent: the question asks whether something
# EXISTS to find or reuse, rather than asking for a fact.
_DISCOVERY = re.compile(
    r"\b(has|have)\s+(anyone|we|someone|any\s?one)\b"
    r"|\b(is|are)\s+there\b|\bdo\s+we\s+have\b"
    r"|\bcan\s+i\s+(find|reuse|use|get)\b|\bwhere\s+(can|do)\s+i\s+find\b"
    r"|\bfind\s+(me\s+)?(a|an|the|some|any)\b|\blook(ing)?\s+for\b|\bsearch\s+for\b"
    r"|\breus(e|able)\b|\bexample[s]?\s+of\b|\bexisting\b|\bany\s+(code|script|module|library|example)\b",
    re.I,
)


def is_discovery(question: str) -> bool:
    return bool(_DISCOVERY.search(question or ""))


def _tok(s: str) -> list[str]:
    return _TOKEN.findall((s or "").lower())


@dataclass
class SearchHit:
    """One asset a searcher found — a file, a function, or a document passage."""

    title: str
    kind: str  # code | test | policy | learning | doc
    snippet: str
    score: float
    source: str  # which searcher produced it (indexed | github)
    url: str = ""  # a link to the exact place, when there is one
    path: str = ""
    symbol: str = ""
    document_id: str = ""
    passage_id: str = ""
    coordinate: object = None


def _kind_of(document, coordinate) -> str:
    loc = getattr(coordinate, "locator", {}) or {}
    path = str(loc.get("path", "")).lower()
    if (
        coordinate is not None
        and getattr(coordinate, "kind", None)
        and coordinate.kind.value == "symbol_line"
    ):
        return (
            "test"
            if path.startswith("tests/") or "/test" in path or path.startswith("test_")
            else "code"
        )
    uri = (document or {}).get("uri", "")
    title = ((document or {}).get("title") or "").lower()
    if "handbook" in uri or "policy" in title or "standard" in title:
        return "policy"
    if "onboarding" in title or "learning" in title or "playbook" in title or "guide" in title:
        return "learning"
    return "doc"


class SourceSearcher:
    """The one interface every source search implements."""

    name = "source"

    def available(self) -> bool:  # pragma: no cover - trivial
        return True

    def search(self, tenant, query, accessible, k=6) -> list[SearchHit]:  # pragma: no cover
        raise NotImplementedError


class IndexedSourceSearcher(SourceSearcher):
    """Search the ingested fabric: BM25 over passage text, plus a code
    identifier tier (symbol / path), grouped to the best hit per document."""

    name = "indexed"

    def __init__(self, platform):
        self.p = platform

    def _hit(self, tenant, pas, score, source="indexed"):
        d = self.p.documents.get(tenant, pas.document_id)
        loc = getattr(pas.coordinate, "locator", {}) or {}
        summary = loc.get("summary_line") or (pas.abstract or pas.text[:160])
        return SearchHit(
            title=(d or {}).get("title", loc.get("path", "document")),
            kind=_kind_of(d, pas.coordinate),
            snippet=summary,
            score=float(score),
            source=source,
            url=loc.get("url", ""),
            path=loc.get("path", ""),
            symbol=loc.get("symbol", ""),
            document_id=pas.document_id,
            passage_id=pas.id,
            coordinate=pas.coordinate,
        )

    def search(self, tenant, query, accessible, k=6) -> list[SearchHit]:
        acc = list(accessible)
        by_pid: dict[str, SearchHit] = {}
        for pid, score in self.p.lindex.search(tenant, query, 40, acc):
            pas = self.p.passages.get(tenant, pid)
            if pas:
                by_pid[pid] = self._hit(tenant, pas, score)
        qtok = [t for t in _tok(query) if len(t) >= 3]
        if qtok:
            for pas in self.p.passages.for_tenant(tenant):
                if pas.coordinate.kind.value != "symbol_line":
                    continue
                if not (set(self.p.passages.acl_of(tenant, pas.id)) & set(acc)):
                    continue
                loc = pas.coordinate.locator or {}
                sym = str(loc.get("symbol", "")).lower()
                path = str(loc.get("path", "")).lower()
                s = sum((3 if t in sym else 0) + (1 if t in path else 0) for t in qtok)
                if s > 0:
                    prev = by_pid.get(pas.id)
                    if not prev or s > prev.score:
                        by_pid[pas.id] = self._hit(tenant, pas, float(s))
        # one hit per document — distinct files/docs, not many passages of one
        best: dict[str, SearchHit] = {}
        for h in by_pid.values():
            cur = best.get(h.document_id)
            if not cur or h.score > cur.score:
                best[h.document_id] = h
        return sorted(best.values(), key=lambda h: h.score, reverse=True)[:k]


class GitHubCodeSearcher(SourceSearcher):
    """Live GitHub code search (a real backend with a token). Dormant without
    ``GITHUB_TOKEN`` — ``available()`` is False — so the static build never calls
    the network; its results are baked. Same ``SearchHit`` contract, so a
    discovery answer treats live and indexed hits identically."""

    name = "github"

    def __init__(self, config=None):
        cfg = config or {}
        tok = cfg.get("token")  # an explicit "" disables; absent falls back to env
        self.token = tok if tok is not None else os.environ.get("GITHUB_TOKEN", "")
        self.org = cfg.get("org") or os.environ.get("GITHUB_ORG", "")
        self.repos = cfg.get("repos") or [
            r for r in os.environ.get("GITHUB_EXTRA_REPOS", "").split(",") if r
        ]
        self.base = cfg.get("base_url", "https://api.github.com")

    def available(self) -> bool:
        return bool(self.token)

    def _qualifier(self) -> str:
        if self.repos:
            return " ".join(f"repo:{r}" for r in self.repos)
        return f"org:{self.org}" if self.org else ""

    def search(self, tenant, query, accessible, k=6) -> list[SearchHit]:
        if not self.available():
            return []
        q = (query + " " + self._qualifier()).strip()
        url = f"{self.base}/search/code?q={urllib.request.quote(q)}&per_page={int(k)}"
        req = urllib.request.Request(
            url,
            headers={
                "Authorization": f"Bearer {self.token}",
                "Accept": "application/vnd.github.text-match+json",
                "User-Agent": "qualizeal-fabric",
            },
        )
        try:
            with urllib.request.urlopen(req, timeout=10) as resp:
                data = json.loads(resp.read())
        except Exception:
            return []
        hits = []
        for item in data.get("items", [])[:k]:
            path = item.get("path", "")
            frag = ""
            for m in item.get("text_matches", []):
                frag = frag or m.get("fragment", "")
            hits.append(
                SearchHit(
                    title=path.rsplit("/", 1)[-1],
                    kind="code",
                    snippet=(frag or path)[:200],
                    score=1.0,
                    source="github",
                    url=item.get("html_url", ""),
                    path=path,
                )
            )
        return hits


@dataclass
class Discovery:
    """The result of a discovery: a ranked, deduped list of assets."""

    query: str
    hits: list[SearchHit] = field(default_factory=list)
    searched: list[str] = field(default_factory=list)


def build_searchers(platform) -> list[SourceSearcher]:
    """The default fan-out: always the fabric, plus live GitHub only when a real
    backend opts in (``KF_LIVE_SOURCE_SEARCH`` set) AND a token is configured.
    The static build never opts in, so its discovery is deterministic and
    offline; a deployed backend flips the flag to search sources live."""
    searchers: list[SourceSearcher] = [IndexedSourceSearcher(platform)]
    if os.environ.get("KF_LIVE_SOURCE_SEARCH"):
        gh = GitHubCodeSearcher()
        if gh.available():
            searchers.append(gh)
    return searchers


def discover(platform, tenant, query, accessible, k=6, searchers=None) -> Discovery:
    """Fan out across searchers, merge, dedupe by (path or passage) and rank."""
    searchers = searchers if searchers is not None else build_searchers(platform)
    merged: dict = {}
    used = []
    for s in searchers:
        try:
            hits = s.search(tenant, query, accessible, k=k)
        except Exception:
            hits = []
        if hits:
            used.append(s.name)
        for h in hits:
            key = h.url or h.passage_id or (h.path + h.title)
            cur = merged.get(key)
            if not cur or h.score > cur.score:
                merged[key] = h
    ranked = sorted(merged.values(), key=lambda h: h.score, reverse=True)[:k]
    return Discovery(query=query, hits=ranked, searched=used)
