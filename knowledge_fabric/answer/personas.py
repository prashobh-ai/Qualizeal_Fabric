"""Designation → persona conditioning (T27).

A person's **role** in the Knowledge Fabric is their organisational *designation*
— developer, tester, QE, architect, manager, delivery head, director, CTO — and
it is captured by the admin when the user is added, not chosen on the ask window.
The same question, asked by different designations, should come back pitched for
that reader: a developer wants the implementation angle in full, a tester the
test/quality angle, a delivery lead a brief status view, a CxO a one-glance
headline.

This module is the single source of truth for that conditioning. It maps the
many titles a company uses onto a small set of **personas**, each with a
deterministic, model-free profile: how deep the answer runs and which grounded
evidence to emphasise. The answer service applies it (a gentle retrieval bump
plus a depth cap on composition), and the browser engine mirrors it verbatim, so
the facts stay grounded and cited for every persona — only the framing, depth,
and emphasis change.
"""

from __future__ import annotations

# --------------------------------------------------------------------------
# Persona profiles — the conditioning axis.
#   depth:    headline (1 sentence) | brief (2) | full (3)
#   emphasis: which retrieved evidence to gently prefer —
#             code | test | authority | none
#   lens:     the adjunct frame the surfaces render
# --------------------------------------------------------------------------
PROFILE: dict[str, dict] = {
    "developer": {
        "depth": "full",
        "emphasis": "code",
        "lens": "builder",
        "note": "Developer view — implementation and code emphasised, in full.",
    },
    "quality": {
        "depth": "full",
        "emphasis": "test",
        "lens": "quality",
        "note": "Quality view — tests, coverage and how it is verified, in full.",
    },
    "delivery": {
        "depth": "brief",
        "emphasis": "authority",
        "lens": "delivery",
        "note": "Delivery view — the status in brief, from the authoritative source.",
    },
    "executive": {
        "depth": "headline",
        "emphasis": "authority",
        "lens": "executive",
        "note": "Executive view — the headline, grounded in the authoritative source.",
    },
    "curation": {
        "depth": "full",
        "emphasis": "none",
        "lens": "curation",
        "note": "Curator view — how well grounded, and where the gaps are.",
    },
    "operations": {
        "depth": "full",
        "emphasis": "none",
        "lens": "operations",
        "note": "Admin view — the level, model and cost that produced this.",
    },
    "general": {
        "depth": "full",
        "emphasis": "none",
        "lens": "answer",
        "note": "",
    },
}

DEPTH_CAP = {"headline": 1, "brief": 2, "full": 3}

# --------------------------------------------------------------------------
# Designation → persona. Titles are matched case-insensitively by keyword, most
# specific first, so "QA lead" resolves to quality and "delivery head" to
# delivery. Unknown or empty designations fall through to "general".
# --------------------------------------------------------------------------
_KEYWORDS: list[tuple[str, str]] = [
    # executive / leadership
    ("ceo", "executive"),
    ("cto", "executive"),
    ("coo", "executive"),
    ("cio", "executive"),
    ("cfo", "executive"),
    ("chief", "executive"),
    ("director", "executive"),
    ("vp", "executive"),
    ("vice president", "executive"),
    ("head of", "executive"),
    ("founder", "executive"),
    # delivery / management
    ("delivery", "delivery"),
    ("manager", "delivery"),
    ("scrum", "delivery"),
    ("project lead", "delivery"),
    ("program", "delivery"),
    ("product owner", "delivery"),
    ("lead", "delivery"),  # a plain "lead" leans management; specific IC leads matched above
    # quality / test
    ("tester", "quality"),
    ("test engineer", "quality"),
    ("qe", "quality"),
    ("qa", "quality"),
    ("sdet", "quality"),
    ("quality", "quality"),
    ("automation", "quality"),
    # engineering / build
    ("developer", "developer"),
    ("engineer", "developer"),
    ("architect", "developer"),
    ("devops", "developer"),
    ("sre", "developer"),
    ("programmer", "developer"),
    ("sde", "developer"),
    # platform functions
    ("curator", "curation"),
    ("knowledge manager", "curation"),
    ("steward", "curation"),
    ("admin", "operations"),
    ("operator", "operations"),
    ("platform", "operations"),
]


def persona_for(designation: str) -> str:
    """The persona that governs a designation's answer conditioning.

    Keyword match, most-specific first; unknown/empty → ``general``. An IC test
    lead ("QA lead") must resolve to quality before the generic "lead"→delivery
    rule, so quality/engineering keywords are checked ahead of a bare "lead"."""
    d = (designation or "").strip().lower()
    if not d:
        return "general"
    # specific IC signals win over a generic "lead"
    for kw, persona in _KEYWORDS:
        if kw == "lead":
            continue
        if kw in d:
            return persona
    if "lead" in d:
        return "delivery"
    return "general"


def profile_for(designation: str) -> dict:
    """The full conditioning profile for a designation."""
    return PROFILE[persona_for(designation)]


def depth_cap(designation: str) -> int:
    """How many sentences the composed answer may carry for this designation."""
    return DEPTH_CAP[profile_for(designation)["depth"]]
