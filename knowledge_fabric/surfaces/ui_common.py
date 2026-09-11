"""Shared shell for the Stage-2 web consoles (Section G).

The three consoles — Ask (``/``), Curator (``/curator``) and Admin
(``/admin``) — share one brand shell: the QualiZeal navy palette used by
``surfaces/dashboard.py``, a header with navigation, a sign-in bar backed by
``POST /login`` (tenant + demo subject → bearer token kept in
``sessionStorage``), and a tiny JS runtime (``KF``) that every page uses for
authenticated ``fetch`` calls, HTML escaping, number formatting, toasts and
the 401/403 "role gate" message.

Everything is inline (CSS, JS, SVG): the pages work fully offline with zero
external dependencies. The demo tenant/user picker is generated from
``tenants/demo.py`` at import time so it can never drift from the seed data.

Helpers here are plain string builders; the consoles call ``shell`` once to
produce their ``*_HTML`` constant.
"""

from __future__ import annotations

import json
import os

from ..tenants import demo

__all__ = ["BRAND_CSS", "RUNTIME_JS", "shell", "card", "demo_directory"]

_ASSETS = os.path.join(os.path.dirname(__file__), "assets")


def _read_asset(name: str) -> str:
    """Read a served CSS/JS/HTML fragment from ``surfaces/assets/``.

    The big style/script/markup blocks live in real ``.css``/``.js``/``.html``
    files (not inline Python strings) so the source stays lint-clean and the
    fragments keep their own editors; the served bytes are unchanged.
    """
    with open(os.path.join(_ASSETS, name), encoding="utf-8") as fh:
        return fh.read()


#: navigation shown in every console header (label, path)
_ACTIVE = ' class="active"'
# Page switcher (L1.2): Workspace · Curator · Admin — three surfaces, shown by
# role client-side. Each entry: (label, path, roles-that-see-it or None=all).
NAV = (
    ("Workspace", "/", None),
    ("Curator", "/curator", ("curator", "admin")),
    ("Admin", "/admin", ("admin",)),
)


def demo_directory() -> dict:
    """Tenant → users picker data, derived from the seeded demo directory.

    Shape: ``{"tenants": [{"tenant", "display"}], "users": {tenant: [{"subject", "roles"}]},
    "questions": {tenant: [question...]}}``. Deterministic (seed order).
    """
    display = {t.tenant: t.display for t in demo.DEMO_TENANTS}
    tenants = [{"tenant": t, "display": display.get(t, t)} for t in demo.DEMO_USERS]
    users = {
        t: [
            {"subject": s, "roles": list(r), "designation": dg}
            for s, r, _sc, dg in map(demo.user_fields, rows)
        ]
        for t, rows in demo.DEMO_USERS.items()
    }
    # Suggested questions now come from the live question bank over the real
    # corpus (P1.6 / L0.3) via GET /api/suggestions; the static directory
    # carries no synthetic question list (L0.2).
    questions = {t: [] for t in demo.DEMO_USERS}
    return {"tenants": tenants, "users": users, "questions": questions}


# --------------------------------------------------------------------------
# brand stylesheet (dashboard palette + console components)
# --------------------------------------------------------------------------
BRAND_CSS = _read_asset("ui_common__brand_css.css")

# --------------------------------------------------------------------------
# JS runtime shared by every console
# --------------------------------------------------------------------------
RUNTIME_JS = _read_asset("ui_common__runtime_js.js")

#: served brand assets (same-origin, L1.1/L1.2)
_LOCKUP = "/static/assets/brand/logo/qualizeal-lockup.png"
_FAVICON = "/static/assets/brand/logo/favicon-32.png"

_SHELL = _read_asset("ui_common__shell.html")

_VERSION = "v0.3"

#: The real-physics galaxy (T51): vendored vis-network + the KFGalaxy view,
#: both same-origin under /static/vendor/. Pages that render a galaxy pass this
#: as ``shell(..., head=GALAXY_HEAD)``. Loaded in <head> so ``window.KFGalaxy``
#: exists before the page script that mounts it runs.
GALAXY_HEAD = (
    '<script src="/static/vendor/vis-network.min.js"></script>'
    '<script src="/static/vendor/galaxy.js"></script>'
)


def card(title: str, body: str, id_: str = "", extra: str = "", right: str = "") -> str:
    """One brand panel: ``<div class="card"><h3>title</h3>body</div>``."""
    attr = f' id="{id_}"' if id_ else ""
    right_html = f'<span class="right">{right}</span>' if right else ""
    return (
        f'<div class="card"{attr}{(" " + extra) if extra else ""}>'
        f"<h3>{title}{right_html}</h3>{body}</div>"
    )


def shell(
    title: str,
    subtitle: str,
    body: str,
    script: str,
    active: str,
    extra_css: str = "",
    head: str = "",
) -> str:
    """Assemble a complete console page from the shared shell.

    ``active`` names the highlighted navigation entry (``"Workspace"``,
    ``"Curator"``, ``"Admin"``). ``subtitle`` is accepted for call-site
    compatibility but is NOT rendered — the product chrome has no narrative
    header (L1.2 / D6). The browser title is
    ``QualiZeal Knowledge Fabric — <page>`` (L1.2).

    ``head`` is raw markup placed at the end of ``<head>`` — the galaxy pages
    use it to pull the vendored ``vis-network`` and ``galaxy.js`` same-origin
    from ``/static/vendor/`` (T51). It stays empty for every other page, so no
    surface pays for a script it does not render. The showcase build rewrites
    the ``/static/`` prefix to a relative path, so a ``<script src>`` here works
    live and baked alike.
    """
    parts = []
    for label, path, roles in NAV:
        active_attr = _ACTIVE if label == active else ""
        roles_attr = "" if roles is None else ' data-roles="{}"'.format(",".join(roles))
        parts.append(f'<a href="{path}" data-path="{path}"{active_attr}{roles_attr}>{label}</a>')
    nav = "".join(parts)
    directory = json.dumps(demo_directory(), sort_keys=True).replace("</", "<\\/")
    browser_title = f"QualiZeal Knowledge Fabric — {active}"
    return (
        _SHELL.replace("__TITLE__", browser_title)
        .replace("__CSS__", BRAND_CSS)
        .replace("__EXTRA_CSS__", extra_css)
        .replace("__LOCKUP__", _LOCKUP)
        .replace("__FAVICON__", _FAVICON)
        .replace("__VERSION__", _VERSION)
        .replace("__NAV__", nav)
        .replace("__HEAD__", head)
        .replace("__DIRECTORY__", directory)
        .replace("__RUNTIME__", RUNTIME_JS)
        .replace("__BODY__", body)
        .replace("__SCRIPT__", script)
    )
