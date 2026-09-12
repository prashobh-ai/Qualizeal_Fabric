"""Stored per-user defaults (T87).

The article's context point — a question means different things to different
users — is our persona + one/two-turn context (T26/T27). This adds the missing
piece: defaults a reader sets **once** and the fabric applies to every answer
without re-asking:

- **persona view** — when a title maps to more than one lens, which one to read
  in (developer / quality / delivery / executive / curation / operations /
  general);
- **preferred depth** — headline / brief / full;
- **preferred language** — the answer's output language;
- **Explain auto-expand** — whether the narrative opens with the answer.

Stored per user (``tenant`` + ``subject``), applied to every answer, and edited
in the Workspace user menu. Persisted to the fabric-data runtime file
``data/user_defaults.json`` (honours ``KF_DATA_ROOT``), so nothing here widens
what a reader can retrieve — only how the grounded answer is shaped.
"""

from __future__ import annotations

from .. import fabric_data
from . import personas

_RUNTIME_NAME = "user_defaults.json"

#: the persona a reader may choose to read in (T27 lenses)
PERSONA_OPTIONS = [
    "developer",
    "quality",
    "delivery",
    "executive",
    "curation",
    "operations",
    "general",
]
DEPTH_OPTIONS = ["headline", "brief", "full"]
LANG_OPTIONS = ["en", "fr", "es", "ja"]

#: a persona → a representative designation, so setting a persona view routes
#: every T27 code path (which keys off ``designation``) to that lens.
CANONICAL_DESIGNATION = {
    "developer": "Developer",
    "quality": "QA Engineer",
    "delivery": "Delivery Manager",
    "executive": "CTO",
    "curation": "Curator",
    "operations": "Admin",
    "general": "",
}


def options() -> dict:
    """The choices the user menu offers, so the surface never hard-codes them."""
    return {"persona": PERSONA_OPTIONS, "depth": DEPTH_OPTIONS, "language": LANG_OPTIONS}


def _all() -> dict:
    d = fabric_data.read_json(fabric_data.data_path(_RUNTIME_NAME), {}) or {}
    return d if isinstance(d, dict) else {}


def _key(tenant: str, subject: str) -> str:
    return f"{tenant}/{subject}"


def get(tenant: str, subject: str) -> dict:
    """This user's stored defaults, or an empty dict (no override set)."""
    d = _all().get(_key(tenant, subject))
    return dict(d) if isinstance(d, dict) else {}


def set(tenant: str, subject: str, patch: dict) -> dict:
    """Merge validated fields into this user's defaults and persist. Unknown or
    invalid values are dropped; an empty string clears a field. Returns the
    stored defaults."""
    cur = get(tenant, subject)
    if "persona" in patch:
        v = (patch.get("persona") or "").strip().lower()
        cur = _apply(cur, "persona", v if v in PERSONA_OPTIONS else None)
    if "depth" in patch:
        v = (patch.get("depth") or "").strip().lower()
        cur = _apply(cur, "depth", v if v in DEPTH_OPTIONS else None)
    if "language" in patch:
        v = (patch.get("language") or "").strip().lower()
        cur = _apply(cur, "language", v if v in LANG_OPTIONS else None)
    if "explain_auto" in patch:
        cur["explain_auto"] = bool(patch.get("explain_auto"))
    alld = _all()
    if cur:
        alld[_key(tenant, subject)] = cur
    else:
        alld.pop(_key(tenant, subject), None)
    fabric_data.write_json(fabric_data.data_path(_RUNTIME_NAME, mkdir=True), alld)
    return cur


def _apply(cur: dict, field: str, value) -> dict:
    if value:
        cur[field] = value
    else:
        cur.pop(field, None)
    return cur


def apply_to(principal, tenant: str, subject: str):
    """Return ``principal`` with this user's stored defaults applied: the persona
    view routed through an effective ``designation`` (so all T27 paths follow
    it), and the depth / language / Explain-auto preferences carried on the
    principal. No stored default → the principal is returned unchanged."""
    import dataclasses

    d = get(tenant, subject)
    if not d:
        return principal
    designation = principal.designation
    persona = d.get("persona")
    if persona in CANONICAL_DESIGNATION:
        # keep the reader's own title when it already maps to the chosen persona,
        # so the designation shown stays theirs; otherwise route via the canonical
        if personas.persona_for(designation) != persona:
            designation = CANONICAL_DESIGNATION[persona]
    return dataclasses.replace(
        principal,
        designation=designation,
        persona_pref=persona if persona in CANONICAL_DESIGNATION else None,
        depth_pref=d.get("depth"),
        lang_pref=d.get("language"),
        explain_auto=bool(d.get("explain_auto")),
    )
