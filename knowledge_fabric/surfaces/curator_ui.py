"""Curator console (Section G) — served at ``/curator``.

The curator's job is *relevance and trust*, not plumbing. The page therefore
shows only knowledge-base evaluation and per-document decisions:

* ``GET /curator/quality``   → data-quality KPI tiles + risk register;
* ``GET /curator/gaps``      → open gaps, contradictions and the low-confidence review queue;
* ``GET /curator/documents`` → the document table: score bar, suggestion badge
  (keep / review / delete) with the transparent reasons behind it, the signals
  (citations, age, duplicates, readability…), authoritative flag, and the
  actions Keep · Delete · Mark authoritative / Unmark · History;
* ``GET /curator/versions?document_id=`` → History drawer with the version
  list, a Rollback button per non-current version and the dataset versions;
* ``POST /curator/decision`` → keep | delete | authoritative | not_authoritative | rollback;
* ``POST /curator/upload``   → the single "Add document" form.

Deliberately absent: connector cards, permissions, bulk upload/delete,
budgets, users — those belong to the Admin console (``admin_ui.py``).
Zero external dependencies: inline CSS/JS/SVG only.
"""

from __future__ import annotations

from .ui_common import _read_asset, card, shell

__all__ = ["CURATOR_HTML"]

_CSS = r"""
.score{display:flex;align-items:center;gap:8px;min-width:120px}.score b{width:36px;text-align:right}
.reasons{font-size:12px;color:var(--mut)}
.reasons li{margin:0}
.signals{display:flex;gap:4px;flex-wrap:wrap;margin-top:4px}
.signals .pill{font-weight:500}
.actions{display:flex;gap:4px;flex-wrap:wrap}
.star{color:var(--warn);font-weight:700}
.risk-high{color:var(--bad)}.risk-medium{color:var(--warn)}.risk-low{color:var(--good)}
.filterbar{display:flex;gap:8px;align-items:center;flex-wrap:wrap;margin-bottom:10px}
.filterbar input{min-width:220px}
.queue li{margin:3px 0;font-size:13px}
"""

_QUALITY = card(
    "Data quality",
    '<div class="grid kpis" id="quality-tiles"><div class="empty">Sign in as a curator to load the '
    "knowledge-base evaluation.</div></div>",
    "quality-card",
    right='<span class="muted small" id="quality-meta"></span>',
)

_RISK = card("Risk register", '<div id="risk-register" class="empty">—</div>', "risk-card")

_QUEUES = card(
    "Curation queues",
    '<div class="grid" style="grid-template-columns:repeat(auto-fit,minmax(220px,1fr))">'
    '<div><b>Gaps</b> <span class="pill bad" id="gaps-count">0</span><ul class="queue" '
    'id="gaps-list"></ul></div>'
    '<div><b>Contradictions</b> <span class="pill warn" id="contradictions-count">0</span><ul '
    'class="queue" id="contradictions-list"></ul></div>'
    '<div><b>Low-confidence review</b> <span class="pill info" id="review-count">0</span><ul '
    'class="queue" id="review-list"></ul></div>'
    "</div>",
    "queues-card",
)

_DOCS = _read_asset("curator_ui__docs.html")

_ADD = card(
    "Add a document",
    '<form id="add-doc-form" class="col">'
    '<div class="row"><input id="add-filename" placeholder="filename, e.g. qa/onboarding.md" '
    'style="flex:1" required>'
    '<select id="add-acl"><option value="public">public</option><option '
    'value="restricted">restricted</option></select></div>'
    '<textarea id="add-text" placeholder="Paste the document text (markdown, csv, transcript…)" '
    "required></textarea>"
    '<div class="row"><button class="btn primary" id="add-btn" type="submit">Ingest '
    "document</button>"
    '<span class="muted small" id="add-status">Runs the full 7-step pipeline and bumps the dataset '
    "version.</span></div>"
    "</form>",
    "add-card",
)

_AUTHORITY = card(
    "Source authority ranks",
    '<div class="muted small">Rank 1 is most authoritative; the answer path boosts cited passages '
    "by this weight (read-only here — admins change ranks).</div>"
    '<div id="authority-ranks" class="row" style="margin-top:8px"></div>',
    "authority-card",
)

_DRAWER = _read_asset("curator_ui__drawer.html")

_FEEDBACK = card(
    "User feedback to check",
    '<div class="muted small">Answers readers flagged as unhelpful (&#128078;). Each one is a '
    "candidate "
    "gap, a wrong route, or a document to fix.</div>"
    '<div class="tablewrap" style="margin-top:8px"><table id="feedback-table">'
    "<thead><tr><th>When</th><th>Reader</th><th>Question</th><th>Level</th><th>Note</th></tr></thead>"
    '<tbody id="feedback-rows"><tr><td colspan="5" class="empty">No negative feedback — readers '
    "are "
    ""
    ""
    ""
    ""
    ""
    ""
    ""
    ""
    "happy.</td></tr></tbody>"
    "</table></div>",
    "feedback-card",
)

_BODY = (
    f'<div class="grid" style="grid-template-columns:2fr 1fr">{_QUALITY}{_RISK}</div>'
    f'<div style="margin-top:14px">{_QUEUES}</div>'
    f'<div style="margin-top:14px">{_FEEDBACK}</div>'
    f"{_DOCS}"
    f'<div class="grid two" style="margin-top:14px">{_ADD}{_AUTHORITY}</div>'
    f"{_DRAWER}"
)

_JS = _read_asset("curator_ui__js.js")

CURATOR_HTML = shell("Curator", "", _BODY, _JS, "Curator", _CSS)
