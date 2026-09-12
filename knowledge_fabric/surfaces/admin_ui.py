"""Admin console (Section G) — served at ``/admin``.

The admin owns the *plumbing*: connectors and their permissions, continuous
refresh, bulk data operations, budgets, users, audit and cloud readiness.
Every panel is wired to an admin endpoint of ``surfaces/http_api.py``:

* source cards           ``GET /admin/sources`` (T47: GitHub / Jira / Confluence —
                         last run, next run, counts from facts.json, rate limit);
* connector cards        ``GET/POST /admin/connectors`` (enable/disable toggle,
                         allow-list, refresh interval, "Sync now" → ``POST /admin/sync``,
                         health badge with freshness / last status / errors / SLA breach);
* pipeline runs          ``GET /admin/runs`` polled every 2 s while a run is active
                         (or right after Sync/Upload), the 7 pipeline stages
                         (detect → convert → chunk → extract → graph → embed → health)
                         plus the sync/tombstone/ingest bookkeeping steps;
* run-due               ``POST /admin/refresh/run-due``;
* bulk upload            ``POST /admin/upload`` (batch builder or raw JSON);
* bulk delete            ``POST /admin/bulk-delete`` (ids / source / uri prefix, with confirm);
* budgets                ``POST /admin/budget``;
* users & roles          ``GET /admin/users``;
* source authority       ``GET/POST /admin/authority``;
* audit tail             ``GET /admin/audit``;
* AWS readiness          ``GET /admin/doctor?target=aws|local``.

401/403 are rendered as a role message by the shared runtime. Zero external
dependencies: inline CSS/JS/SVG only.
"""

from __future__ import annotations

from .ui_common import _read_asset, card, shell

__all__ = ["ADMIN_HTML"]

_CSS = _read_asset("admin_ui__css.css")

_CONNECTORS = _read_asset("admin_ui__connectors.html")

_RUNS = card(
    'Pipeline runs <span class="dot" id="runs-live"></span>',
    '<div id="runs" class="empty">—</div>',
    "runs-panel",
    right='<span class="muted small" id="runs-status">polls every 2 s while a run is active</span>',
)

_UPLOAD = card(
    "Bulk upload",
    '<div class="col">'
    # Door 1 — pick real files from disk (PDF / DOCX / XLSX / images / MD / CSV …).
    # They are read in the browser and sent as base64 to the same multi-format
    # intake the connectors use; ACL applies to every file in the drop.
    '<div class="row"><label class="btn sm" for="upload-files" style="cursor:pointer">Choose '
    "files…</label>"
    '<input id="upload-files" type="file" multiple style="display:none">'
    '<select id="upload-file-acl"><option value="public">public</option>'
    '<option value="restricted">restricted</option></select>'
    '<span class="muted small" id="upload-files-hint">Documents are read in your browser and '
    "uploaded as-is — nothing is sent until you press Upload.</span></div>"
    # Door 2 — type/paste a document inline.
    '<div class="row"><input id="upload-filename" placeholder="filename" style="flex:1">'
    '<select id="upload-acl"><option value="public">public</option><option '
    'value="restricted">restricted</option></select>'
    '<button class="btn sm" id="upload-add" type="button">Add to batch</button></div>'
    '<textarea id="upload-text" placeholder="document text for the file above"></textarea>'
    '<ol class="batch" id="upload-batch"></ol>'
    "<details><summary>…or paste a JSON array of {filename, text, acl?}</summary>"
    '<textarea id="upload-json" placeholder=\'[{"filename":"qa/a.md","text":"# '
    "A\"}]'></textarea></details>"
    '<div class="row"><button class="btn primary" id="upload-btn">Upload batch</button><span '
    'class="muted small" id="upload-status"></span></div>'
    "</div>",
    "bulk-upload",
)

_DELETE = card(
    "Bulk delete",
    '<div class="col">'
    '<textarea id="delete-ids" placeholder="document ids, one per line or '
    'comma-separated"></textarea>'
    '<div class="row"><input id="delete-source" placeholder="…or every document of a source (e.g. '
    'jira)" style="flex:1">'
    '<input id="delete-prefix" placeholder="…or by uri prefix (e.g. jira://REL/)" '
    'style="flex:1"></div>'
    '<div class="row"><button class="btn danger" id="delete-btn">Delete matching documents</button>'
    '<span class="muted small" id="delete-status">Tombstones documents, removes passages from '
    "retrieval, bumps the dataset version.</span></div>"
    "</div>",
    "bulk-delete",
)

_BUDGET = card(
    "Budget",
    '<div class="row"><label class="muted small">Cap (USD)</label><input type="number" '
    'id="budget-cap" step="0.5" min="0" value="5">'
    '<button class="btn primary sm" id="budget-btn">Set cap</button></div>'
    '<div class="small" style="margin-top:8px">Spent: <b id="budget-spent">—</b> <span '
    'class="muted" id="budget-note">enforced before every model call</span></div>',
    "budget",
)

_USERS = card(
    "Users &amp; access",
    '<div class="row" id="add-user-form" style="margin-bottom:10px;gap:6px">'
    '<input id="nu-subject" placeholder="user id (e.g. analyst.jo)" style="flex:1;min-width:130px">'
    '<input id="nu-designation" placeholder="designation (e.g. Developer, CTO)" '
    'title="The user&#39;s org title — conditions how answers are framed (T27)" '
    'style="flex:1;min-width:150px">'
    '<select id="nu-role"><option value="asker">Asker</option>'
    '<option value="curator">Curator</option><option value="admin">Admin</option></select>'
    '<label class="small muted"><input type="checkbox" id="nu-restricted"> restricted</label>'
    '<button class="btn primary sm" id="add-user-btn">Add user</button></div>'
    '<div class="tablewrap"><table id="users-table">'
    "<thead><tr><th>Subject</th><th>Designation</th><th>Roles</th><th>Scopes</th><th></th></tr></thead>"
    '<tbody id="users-rows"><tr><td colspan="5" class="empty">—</td></tr></tbody></table></div>',
    "users",
)

_AUTHORITY = card(
    "Source authority",
    '<div id="authority-ranks" class="row"></div>'
    '<div class="row" style="margin-top:8px"><select id="authority-source"></select>'
    '<input type="number" id="authority-rank" min="1" max="9" value="1" style="width:70px">'
    '<button class="btn sm" id="authority-btn">Set rank</button></div>',
    "authority-editor",
)

_AUDIT = card(
    "Audit tail",
    '<div class="tablewrap"><table '
    'id="audit-table"><thead><tr><th>When</th><th>Subject</th><th>Action</th><th>Resource</th><th>D'
    "ecision</th></tr></thead>"
    '<tbody id="audit-rows"><tr><td colspan="5" class="empty">—</td></tr></tbody></table></div>',
    "audit-tail",
    right='<button class="btn sm" id="audit-refresh">Refresh</button>',
)

_AWS = card(
    "AWS readiness",
    '<div class="row"><label class="muted small">Target</label><select id="doctor-target"><option '
    'value="aws">aws</option><option value="local">local</option></select>'
    '<button class="btn sm primary" id="doctor-btn">Run doctor</button><span '
    'id="doctor-exit"></span></div>'
    '<div class="row" id="doctor-selection" style="margin:8px 0"></div>'
    '<pre class="report" id="doctor-report">Run the readiness doctor to see env vars, adapters, '
    "IaC and secrets checks.</pre>",
    "aws-panel",
)

_MODELS = card(
    "Models — provider &amp; API consumption",
    '<div id="provider-card" class="row"><span class="muted small">Run the provider check '
    "(doctor --require anthropic) to pin the models and prove the key.</span></div>"
    '<div class="row" style="margin:8px 0"><label class="muted small">Window</label>'
    '<select id="models-days"><option value="1">24h</option><option value="7" selected>7 days'
    '</option><option value="30">30 days</option></select>'
    '<button class="btn sm" id="models-refresh">Refresh</button></div>'
    '<div id="models-totals" class="row"></div>'
    # T55/T56 — the token-meter overview: which provider answers, efficiency
    # (cited vs total tokens), active-vs-idle time, cost by phase and burn rate.
    '<div id="models-telemetry" class="row" style="margin-top:8px"></div>'
    '<div class="grid two" style="margin-top:8px">'
    '<div><div class="muted small">By purpose</div><div class="tablewrap">'
    '<table id="models-purpose">'
    "<thead><tr><th>Purpose</th><th>Calls</th><th>In</th><th>Out</th><th>Cache read</th>"
    "<th>Cost</th></tr></thead><tbody></tbody></table></div></div>"
    '<div><div class="muted small">By model</div><div class="tablewrap"><table id="models-model"'
    "><thead><tr><th>Model</th><th>Calls</th><th>In</th><th>Out</th><th>Cache read</th>"
    "<th>Cost</th></tr></thead><tbody></tbody></table></div></div></div>"
    '<div class="muted small" style="margin-top:8px">By day</div>'
    '<div class="tablewrap"><table id="models-day"><thead><tr><th>Day</th><th>Calls</th>'
    "<th>In</th><th>Out</th><th>Cache read</th><th>Cache write</th><th>Cost</th></tr></thead>"
    "<tbody></tbody></table></div>"
    '<div class="muted small" style="margin-top:8px">Prices (USD per million tokens, '
    'maintained by hand)</div><div id="models-prices" class="row"></div>'
    '<div class="muted small" style="margin-top:8px">Last 50 calls</div>'
    '<div class="tablewrap"><table id="models-calls"><thead><tr><th>When</th><th>Purpose</th>'
    "<th>Model</th><th>Workflow</th><th>In</th><th>Out</th><th>Cache read</th><th>Latency</th>"
    "<th>Cost</th><th>Request</th></tr></thead>"
    '<tbody><tr><td colspan="10" class="empty">no API calls recorded</td></tr></tbody>'
    "</table></div>",
    "models-panel",
)

# T47 — one card per analysed source (GitHub / Jira / Confluence): last run and
# next run from the refresh scheduler, counts from facts.json, the live GitHub
# rate limit when the live connector exposes it. Served by GET /admin/sources.
_SOURCES = card(
    "Sources — GitHub, Jira, Confluence",
    '<div class="conn" id="sources-cards"><div class="empty">Sign in as an admin to load the '
    "source cards.</div></div>",
    "sources-panel",
    right='<span class="muted small">last run · next run · counts · rate limit</span>',
)

# T83 — the audience coverage matrix as a heatmap: every data type (rows) ×
# every persona (columns), green passing / amber weak / coral failing / grey
# n/a. A cell opens its questions and last results below the grid.
_COVERAGE = card(
    "Coverage — every audience, every data type",
    '<div class="muted small">Each cell asks that persona real questions of that '
    "data type through the one governed answer path and checks the answer is "
    "grounded and cited. The gate blocks a regression that turns a held cell "
    "coral.</div>"
    '<div id="coverage-summary" class="muted small" style="margin-top:6px"></div>'
    '<div class="tablewrap" style="margin-top:8px"><table id="coverage-table">'
    "<thead><tr><th>Data type</th></tr></thead>"
    '<tbody id="coverage-rows"><tr><td class="empty">Sign in as an admin to load '
    "the coverage matrix.</td></tr></tbody></table></div>"
    '<div id="coverage-detail" class="muted small" style="margin-top:8px"></div>',
    "coverage-panel",
    right='<span class="pill" id="coverage-verdict"></span>',
)

# T86 — the business SLA & path panel: time-to-answer (median/p95) per persona
# and per data type, the fast-vs-agent split, explain-request rate and cost per
# answer, under a headline SLA line.
_SLA = card(
    "Service levels — time to answer",
    '<div id="sla-headline" class="empty">Sign in as an admin to load the service '
    "levels.</div>"
    '<div class="grid two" style="margin-top:10px">'
    '<div><div class="section-title">By persona</div><div class="tablewrap">'
    '<table id="sla-persona"><thead><tr><th>Persona</th><th>n</th><th>median</th>'
    "<th>p95</th><th>fast</th><th>agent</th><th>explain</th><th>$/answer</th></tr></thead>"
    "<tbody></tbody></table></div></div>"
    '<div><div class="section-title">By data type</div><div class="tablewrap">'
    '<table id="sla-datatype"><thead><tr><th>Data type</th><th>n</th><th>median</th>'
    "<th>p95</th><th>fast</th><th>agent</th><th>$/answer</th></tr></thead>"
    "<tbody></tbody></table></div></div></div>",
    "sla-panel",
    right='<span class="muted small">median · p95 · fast/agent · explain rate · cost</span>',
)

# T96 — the leadership ROI page: value (hours saved), cost + cost avoided, the
# ROI ratio, adoption, quality and the service SLA line. Two Settings knobs drive
# the money math. Everything reconciles with the panels below.
_OVERVIEW = card(
    "Overview — value &amp; ROI",
    '<div class="row" id="roi-settings" style="gap:10px;flex-wrap:wrap;margin-bottom:6px">'
    '<label class="muted small">Minutes saved / question <input type="number" id="roi-minutes" '
    'min="0" step="1" style="width:70px"></label>'
    '<label class="muted small">Loaded rate $/h <input type="number" id="roi-rate" min="0" '
    'step="5" style="width:80px"></label>'
    '<button class="btn sm" id="roi-save" type="button">Save</button>'
    '<span class="hint" id="roi-status"></span></div>'
    '<div id="roi-tiles" class="kpi-row"><div class="empty">Sign in as an admin to load '
    "the ROI overview.</div></div>"
    '<div class="grid two" style="margin-top:10px">'
    '<div><div class="section-title">Adoption</div>'
    '<div id="roi-adoption" class="muted small"></div></div>'
    '<div><div class="section-title">Quality &amp; service</div><div id="roi-quality" '
    'class="muted small"></div></div></div>',
    "overview-panel",
    right='<span class="muted small">hours saved &middot; cost avoided &middot; ROI ratio</span>',
)

# T96 — the technical OTel view: a recent-trace list (click for the span
# waterfall), error rate, latency percentiles and the reconciliation check.
_OBSERVABILITY = card(
    "Observability — traces &amp; OTel",
    '<div id="obs-kpis" class="kpi-row"><div class="empty">Sign in as an admin to load '
    "observability.</div></div>"
    '<div class="tablewrap" style="margin-top:8px"><table id="obs-traces">'
    "<thead><tr><th>Trace</th><th>Subject</th><th>Level</th><th>Latency</th><th>Cost</th>"
    '<th>Status</th></tr></thead><tbody id="obs-rows"></tbody></table></div>'
    '<div id="obs-waterfall" class="waterfall" hidden></div>'
    '<div id="obs-recon" class="muted small" style="margin-top:8px"></div>',
    "observability-panel",
    right='<span class="muted small">error rate &middot; p50/p95 &middot; recon ±1%</span>',
)

_BODY = (
    f"<div>{_OVERVIEW}</div>"
    + f'<div style="margin-top:14px">{_CONNECTORS}</div>'
    + f'<div style="margin-top:14px">{_SLA}</div>'
    + f'<div style="margin-top:14px">{_OBSERVABILITY}</div>'
    + f'<div style="margin-top:14px">{_COVERAGE}</div>'
    + f'<div style="margin-top:14px">{_SOURCES}</div>'
    + f'<div style="margin-top:14px">{_MODELS}</div>'
    + f'<div style="margin-top:14px">{_RUNS}</div>'
    + f'<div class="grid two" style="margin-top:14px">{_UPLOAD}{_DELETE}</div>'
    + f'<div class="grid" '
    f'style="grid-template-columns:repeat(auto-fit,minmax(300px,1fr));margin-top:14px">'
    f"{_BUDGET}{_USERS}{_AUTHORITY}</div>"
    + f'<div class="grid two" style="margin-top:14px">{_AUDIT}{_AWS}</div>'
)

_JS = _read_asset("admin_ui__js.js")

ADMIN_HTML = shell("Admin", "", _BODY, _JS, "Admin", _CSS)
