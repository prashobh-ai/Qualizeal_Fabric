"""Generic pasted-URL parsing for the add-source flow (T118).

The admin allow-list field is URL-aware: a reviewer pastes exactly what the
browser shows — a GitHub org/user or repo URL, a Jira dashboard/board/project
URL, a Confluence page/space URL, or a website — and the same code path turns
it into the connector's own config keys. Nothing is hardcoded to QualiZeal, so
the identical parse serves a personal GitHub, a personal Jira and a personal
site.

``parse_source_url(raw)`` classifies one entry into
``{"source": <key>, ...fragments}``; ``config_from_allow(source, entries)``
folds a card's allow-list into that connector's config fragment (repos/org,
dashboards/boards/projects, pages/spaces, urls), so ``effective_config`` can
merge it. Plain identifiers (``owner/repo``, a project ``KEY``, a space key, a
board id) still map to the native key, so an allow-list written before T118
keeps working.
"""

from __future__ import annotations

import re
from urllib.parse import urlsplit

__all__ = ["parse_source_url", "config_from_allow", "PLACEHOLDERS"]

#: the add-source card placeholders (Admin UI reads these per source key).
PLACEHOLDERS: dict[str, str] = {
    "github": "Paste a GitHub user or org URL, or owner/repo",
    "jira": "Paste a Jira dashboard, board, or project URL",
    "confluence": "Paste a Confluence page or space URL",
    "website": "Paste a website URL",
    "files": "Allowed file extensions, e.g. .pdf .docx",
}

_GH_HOSTS = ("github.com", "www.github.com")
_OWNER_REPO = re.compile(r"^[A-Za-z0-9](?:[A-Za-z0-9._-]*[A-Za-z0-9])?$")


def _segments(path: str) -> list[str]:
    return [s for s in (path or "").split("/") if s]


def _site(u) -> str:
    """The scheme+host origin of a parsed URL (Jira/Confluence base)."""
    return f"{u.scheme}://{u.netloc}" if u.scheme and u.netloc else ""


def _first_num(segs: list[str]) -> str | None:
    for s in segs:
        if s.isdigit():
            return s
    return None


def _parse_github(u, segs: list[str]) -> dict:
    # github.com/<owner>            → the whole account (org or user)
    # github.com/<owner>/<repo>     → a single repository
    if len(segs) == 1:
        return {"source": "github", "org": segs[0]}
    if len(segs) >= 2:
        repo = segs[1][:-4] if segs[1].endswith(".git") else segs[1]
        return {"source": "github", "repos": [f"{segs[0]}/{repo}"]}
    return {"source": "github"}


def _parse_atlassian(u, segs: list[str], low: str) -> dict:
    """One atlassian.net host serves both Jira and Confluence; ``/wiki/`` marks
    Confluence, everything else (dashboards, boards, browse, projects) is Jira."""
    site = _site(u)
    if "/wiki/" in low or (segs and segs[0] == "wiki"):
        # .../wiki/spaces/<KEY>/pages/<id>/...   → that one page
        # .../wiki/spaces/<KEY>                  → the space
        frag: dict = {"source": "confluence", "url": site}
        if "pages" in segs:
            pid = segs[segs.index("pages") + 1] if segs.index("pages") + 1 < len(segs) else ""
            if pid.isdigit():
                frag["pages"] = [pid]
                return frag
        if "spaces" in segs:
            key = segs[segs.index("spaces") + 1] if segs.index("spaces") + 1 < len(segs) else ""
            if key:
                frag["spaces"] = [key.upper()]
        return frag
    # Jira
    frag = {"source": "jira", "url": site}
    if "dashboards" in segs or "Dashboard.jspa" in low:
        did = _first_num(segs[segs.index("dashboards") + 1 :]) if "dashboards" in segs else None
        if did:
            frag["dashboards"] = [did]
            return frag
    if "boards" in segs:
        bid = _first_num(segs[segs.index("boards") + 1 :])
        if bid:
            frag["boards"] = [bid]
            return frag
    if "browse" in segs:  # .../browse/KEY-123
        key = segs[segs.index("browse") + 1] if segs.index("browse") + 1 < len(segs) else ""
        key = key.split("-")[0]
        if key:
            frag["projects"] = [key.upper()]
            return frag
    if "projects" in segs:  # .../jira/software/projects/KEY/...
        key = segs[segs.index("projects") + 1] if segs.index("projects") + 1 < len(segs) else ""
        if key:
            frag["projects"] = [key.upper()]
    return frag


def parse_source_url(raw: str) -> dict | None:
    """Classify one pasted entry. Returns ``{"source", ...fragment}`` or ``None``
    when it is empty. A bare identifier is left for :func:`config_from_allow` to
    place in a card's own source (it cannot be classified on its own)."""
    s = (raw or "").strip()
    if not s:
        return None
    has_scheme = "://" in s
    u = urlsplit(s if has_scheme else "https://" + s)
    host = (u.netloc or "").lower().split("@")[-1].split(":")[0]
    segs = _segments(u.path)
    low = s.lower()

    if host in _GH_HOSTS:
        return _parse_github(u, segs)
    if host.endswith("atlassian.net") or "/wiki/" in low or "/jira/" in low:
        return _parse_atlassian(u, segs, low)
    if has_scheme and host:  # any other real URL → a website crawl seed
        return {"source": "website", "urls": [s]}
    # bare "owner/repo" with no host is a GitHub single repo (generic)
    if not has_scheme and s.count("/") == 1 and all(_OWNER_REPO.match(p) for p in s.split("/")):
        return {"source": "github", "repos": [s]}
    return None


# --------------------------------------------------------------------------
# allow-list → a connector config fragment
# --------------------------------------------------------------------------
def _native_allow(source: str, entry: str, frag: dict) -> None:
    """A bare identifier (not a URL) goes to the card's own source key, so an
    allow-list written before T118 (repo names, project keys, …) still works."""
    if source == "github":
        (frag.setdefault("repos", []) if "/" in entry else frag.setdefault("_orgs", [])).append(
            entry
        )
    elif source == "jira":
        frag.setdefault("projects", []).append(entry.upper())
    elif source == "confluence":
        frag.setdefault("spaces", []).append(entry.upper())
    elif source == "website":
        frag.setdefault("urls", []).append(entry)
    elif source == "files":
        frag.setdefault("allow_ext", []).append(entry)


def _merge_parsed(source: str, parsed: dict, frag: dict) -> None:
    """Fold a classified entry into the fragment when it matches this card's
    source (a URL pasted into the wrong card is ignored, not misfiled)."""
    if parsed.get("source") != source:
        return
    for key in ("repos", "dashboards", "boards", "projects", "pages", "spaces", "urls"):
        if parsed.get(key):
            frag.setdefault(key, []).extend(parsed[key])
    if parsed.get("org"):
        frag.setdefault("_orgs", []).append(parsed["org"])
    if parsed.get("url"):
        frag.setdefault("url", parsed["url"])  # first site wins


def config_from_allow(source: str, entries: list[str]) -> dict:
    """A card's allow-list → the connector's config fragment. Deduplicates and
    drops the internal ``_orgs`` accumulator into ``org`` (last pasted wins)."""
    frag: dict = {}
    for raw in entries or []:
        entry = (raw or "").strip()
        if not entry:
            continue
        parsed = parse_source_url(entry)
        if parsed and parsed.get("source") == source:
            _merge_parsed(source, parsed, frag)
        elif parsed and parsed.get("source") != source and "://" in entry:
            continue  # a real URL for another source pasted into this card — ignore
        else:
            _native_allow(source, entry, frag)
    # de-dup list values, preserving order
    for key, val in list(frag.items()):
        if isinstance(val, list):
            seen: list[str] = []
            for v in val:
                if v not in seen:
                    seen.append(v)
            frag[key] = seen
    orgs = frag.pop("_orgs", [])
    if orgs:
        frag["org"] = orgs[-1]
    return frag
