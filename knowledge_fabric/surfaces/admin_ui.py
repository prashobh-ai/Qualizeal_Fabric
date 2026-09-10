"""Admin console (Section G) — served at ``/admin``.

The admin owns the *plumbing*: connectors and their permissions, continuous
refresh, bulk data operations, budgets, users, audit and cloud readiness.
Every panel is wired to an admin endpoint of ``surfaces/http_api.py``:

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

_BODY = (
    _CONNECTORS
    + f'<div style="margin-top:14px">{_RUNS}</div>'
    + f'<div class="grid two" style="margin-top:14px">{_UPLOAD}{_DELETE}</div>'
    + f'<div class="grid" '
    f'style="grid-template-columns:repeat(auto-fit,minmax(300px,1fr));margin-top:14px">'
    f"{_BUDGET}{_USERS}{_AUTHORITY}</div>"
    + f'<div class="grid two" style="margin-top:14px">{_AUDIT}{_AWS}</div>'
)

_JS = _read_asset("admin_ui__js.js")

ADMIN_HTML = shell("Admin", "", _BODY, _JS, "Admin", _CSS)
