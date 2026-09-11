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

T47 adds the fabric-data panels:

* ``GET /curator/repositories`` → the Repositories table (language, commits,
  PRs merged, contributors, deployments, capability pills, enterprise score,
  last push) with a ``card`` overlay from ``GET /curator/repository?repo=``
  (facts, languages bar, capability evidence, dependencies with licence
  pills, architecture summary, contributors, recent PRs / commits) and a
  Delete that tombstones the repository's documents
  (``POST /curator/repository/delete``);
* ``GET /curator/insights`` → Capabilities across repositories + Reuse candidates;
* ``GET /curator/tables`` → extracted sheets with columns and row counts, and
  a SELECT box (``POST /curator/tables/query``).

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
/* T47 — repositories / insights / tables */
.langbar{display:flex;height:10px;border-radius:6px;overflow:hidden;background:var(--panel2);
 margin:6px 0}
.langbar i{display:block;height:100%}
.legend{display:flex;gap:10px;flex-wrap:wrap;font-size:12px}
.legend .sw{display:inline-block;width:10px;height:10px;border-radius:2px;margin-right:4px;
 vertical-align:middle}
.md{white-space:pre-wrap;font-size:12.5px;line-height:1.5;max-height:320px;overflow:auto;background:var(--panel);
 border:1px solid var(--line);border-radius:8px;padding:10px}
.evidence{font-size:12px;color:var(--mut);margin:2px 0 6px 0;padding-left:14px}
.evidence code{font-family:ui-monospace,Menlo,monospace;font-size:11px}
.facts{display:grid;grid-template-columns:repeat(auto-fit,minmax(140px,1fr));gap:8px}
.facts .f{background:var(--panel);border:1px solid var(--line);border-radius:8px;padding:8px}
.facts .f .k{font-size:11px;color:var(--mut);text-transform:uppercase;letter-spacing:.3px}
.facts .f .v{font-size:16px;font-weight:700}
.sheet{border-top:1px solid var(--line);padding:8px 0}.sheet:first-child{border-top:0}
.sheet .cols{display:flex;gap:4px;flex-wrap:wrap;margin-top:4px}
#repo-panel{width:min(780px,100%)}
#repo-body h4{margin:14px 0 6px;font-size:13px}
.tq textarea{width:100%;min-height:70px;font-family:ui-monospace,Menlo,monospace;font-size:12px}
.cap-group{margin:6px 0}
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

# T47 — the fabric-data panels: repositories (facts.json + capabilities.json +
# analysis/<repo>/), insights (capabilities across repositories + reuse
# candidates) and tables (extracted sheets with a SELECT box).
_REPOS = (
    '<div class="section-title">Repositories <span class="pill" id="repo-count"></span>'
    '<span class="muted small">from the GitHub analysis (facts, capabilities, architecture)</span>'
    '<button class="btn sm" id="repo-refresh" style="margin-left:auto">Refresh</button></div>'
    '<div class="card" id="repositories-card"><div class="tablewrap"><table id="repo-table">'
    "<thead><tr><th>Repository</th><th>Language</th><th>Commits</th><th>PRs merged</th>"
    "<th>Contributors</th><th>Deployments</th><th>Capabilities</th><th>Enterprise</th>"
    "<th>Last push</th><th>Actions</th></tr></thead>"
    '<tbody id="repo-rows"><tr><td colspan="10" class="empty">No repositories analysed yet — '
    "facts.json is written by the GitHub analysis workflow.</td></tr></tbody></table></div></div>"
)

_REPO_DRAWER = (
    '<div class="drawer hidden" id="repo-panel">'
    '<div class="row"><h3 style="margin:0">Repository <span class="muted small mono" '
    'id="repo-title"></span></h3>'
    '<button class="btn sm" id="repo-close" style="margin-left:auto">Close</button></div>'
    '<div id="repo-body"><div class="empty">—</div></div></div>'
)

_INSIGHTS = card(
    "Insights — capabilities &amp; reuse",
    '<div class="grid two"><div><div class="muted small">Capabilities across repositories '
    '(confidence per repository)</div><div id="insights-capabilities" class="empty">—</div></div>'
    '<div><div class="muted small">Reuse candidates (from the architecture summaries)</div>'
    '<div id="insights-reuse" class="empty">—</div></div></div>',
    "insights-card",
    right='<span class="muted small" id="insights-meta"></span>',
)

_TABLES = card(
    "Tables — extracted sheets",
    '<div class="grid two"><div><div id="tables-list" class="empty">No tables extracted yet — '
    "sheets are written under tables/&lt;doc&gt;/&lt;sheet&gt;.sqlite by the document "
    "workflow.</div></div>"
    '<div class="tq col"><div class="row"><label class="muted small">Sheet</label>'
    '<select id="tq-sheet" style="flex:1"></select></div>'
    '<textarea id="tq-sql" placeholder="SELECT * FROM t LIMIT 20"></textarea>'
    '<div class="row"><button class="btn primary sm" id="tq-run">Run SELECT</button>'
    '<span class="muted small" id="tq-status">read-only; SELECT only, capped at 200 rows'
    "</span></div>"
    '<div class="tablewrap" id="tq-result"></div></div></div>',
    "tables-card",
    right='<span class="pill" id="tables-count"></span>',
)

_BODY = (
    f'<div class="grid" style="grid-template-columns:2fr 1fr">{_QUALITY}{_RISK}</div>'
    f'<div style="margin-top:14px">{_QUEUES}</div>'
    f'<div style="margin-top:14px">{_FEEDBACK}</div>'
    f"{_DOCS}"
    f"{_REPOS}"
    f'<div style="margin-top:14px">{_INSIGHTS}</div>'
    f'<div style="margin-top:14px">{_TABLES}</div>'
    f'<div class="grid two" style="margin-top:14px">{_ADD}{_AUTHORITY}</div>'
    f"{_DRAWER}{_REPO_DRAWER}"
)

_JS = _read_asset("curator_ui__js.js")

CURATOR_HTML = shell("Curator", "", _BODY, _JS, "Curator", _CSS)
