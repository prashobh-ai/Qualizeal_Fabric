"""Jira Cloud LIVE connector (T41) — the REST API, read-only, allow-listed.

    env: JIRA_URL  JIRA_EMAIL  JIRA_TOKEN      (basic auth: email:api-token)
    config: {projects: [KEY…], interval: "7d", acl: [...], sprint_field?: "customfield_10020",
             board_id?: 34, page_size: 100, max_issues: 5000, facts_max_issues: 2000,
             write_facts: true}

* ``pull(cursor)`` searches ``updated >= -<interval>`` (JQL) per allow-listed
  project with fields summary, description, status, assignee, priority, labels,
  issuetype, sprint, comments and links; every issue becomes ONE canonical
  record (``application/x-kf-jira+json``) whose converter yields a passage per
  field and per comment, cited at ``<JIRA_URL>/browse/<KEY>``.
* Facts per project → ``data/facts.json["jira_projects"][KEY]`` =
  ``{issues: {total, by_status, by_priority, by_type, by_assignee}, sprint: {name, state}|null,
  as_of}`` — counted over a lightweight census of the whole project (not just
  the window) so ``total`` means the project.
* ``live_jql(jql, fields, max_results)`` is the agent's ad-hoc query.

Transport: ``transport(url, headers, timeout) -> (status, bytes)`` — the
default is ``urllib``; tests inject a fake. Non-2xx raises ``ConnectorError``
with the status and body; nothing is swallowed. Search uses
``/rest/api/3/search/jql`` (token pagination) and falls back to the classic
``/rest/api/3/search`` (``startAt``) when a server answers 404/410.
"""

from __future__ import annotations

import base64
import json
import os
import time
import urllib.error
import urllib.parse
import urllib.request
from collections import Counter

from .. import fabric_data as fd
from ..contracts.types import RawItem, now_ms
from .base import BaseConnector

JIRA_MIME = "application/x-kf-jira+json"
_FIELDS = (
    "summary,description,status,assignee,priority,labels,issuetype,comment,issuelinks,"
    "updated,created,project,parent"
)
_CENSUS_FIELDS = "status,priority,issuetype,assignee,updated"


class ConnectorError(RuntimeError):
    """A live connector call failed (HTTP status + body, or unreachable)."""

    def __init__(self, status: int, body: str, url: str = "", detail: str = ""):
        self.status, self.body, self.url = status, body, url
        msg = detail or f"connector call returned HTTP {status}"
        super().__init__(f"{msg} ({url}): {body[:600]}")


class ConnectorConfigError(ValueError):
    """Required credentials/config are missing — raised on use, never on construction."""


def http_transport(url: str, headers: dict, timeout: int = 30) -> tuple[int, bytes]:
    req = urllib.request.Request(url, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return int(r.status), r.read()
    except urllib.error.HTTPError as e:
        return int(e.code), e.read()
    except urllib.error.URLError as e:
        raise ConnectorError(0, str(e.reason), url, detail="connector unreachable") from e


def basic_auth(email: str, token: str) -> str:
    return "Basic " + base64.b64encode(f"{email}:{token}".encode()).decode("ascii")


# ---------------------------------------------------------------------------
# Atlassian Document Format → text
# ---------------------------------------------------------------------------
def adf_text(node) -> str:
    """Flatten an ADF document (Jira Cloud v3 rich text) to plain paragraphs.
    Plain strings (v2 / already-flat) pass through."""
    if node is None:
        return ""
    if isinstance(node, str):
        return node.strip()
    if isinstance(node, list):
        return "\n".join(t for t in (adf_text(n) for n in node) if t)
    if not isinstance(node, dict):
        return str(node)
    t = node.get("type", "")
    if t == "text":
        return node.get("text", "")
    if t == "hardBreak":
        return "\n"
    if t in ("mention", "emoji", "status", "date"):
        return str(node.get("attrs", {}).get("text") or node.get("attrs", {}).get("id", ""))
    if t == "inlineCard":
        return str(node.get("attrs", {}).get("url", ""))
    children = node.get("content", []) or []
    if t in ("paragraph", "heading"):
        return "".join(adf_text(c) for c in children).strip()
    if t == "listItem":
        return "- " + " ".join(adf_text(c) for c in children).strip()
    if t == "codeBlock":
        return "\n".join(adf_text(c) for c in children)
    if t in ("tableRow",):
        return " | ".join(adf_text(c) for c in children)
    if t in ("tableCell", "tableHeader"):
        return " ".join(adf_text(c) for c in children).strip()
    parts = [adf_text(c) for c in children]
    return "\n".join(p for p in parts if p)


# ---------------------------------------------------------------------------
# issue flattening
# ---------------------------------------------------------------------------
def _name(obj, key="name") -> str:
    return str((obj or {}).get(key) or "") if isinstance(obj, dict) else ""


def _sprint(value) -> dict | None:
    """The sprint field is a list of sprint objects (or legacy strings)."""
    if not value:
        return None
    items = value if isinstance(value, list) else [value]
    best = None
    for it in items:
        if isinstance(it, dict):
            cand = {"name": str(it.get("name", "")), "state": str(it.get("state", "")).lower()}
        elif isinstance(it, str):  # "com.atlassian.greenhopper...[id=1,name=Sprint 3,state=ACTIVE"
            fields = dict(
                kv.split("=", 1) for kv in it.strip("[]").split(",") if "=" in kv and kv.strip()
            )
            cand = {
                "name": fields.get("name", "").strip(),
                "state": fields.get("state", "").strip().lower(),
            }
        else:
            continue
        if best is None or cand["state"] == "active":
            best = cand
    return best


def flatten_issue(issue: dict, base_url: str, sprint_field: str | None = None) -> dict:
    f = issue.get("fields", {}) or {}
    key = issue.get("key", "")
    comments = []
    for c in (f.get("comment") or {}).get("comments") or []:
        comments.append(
            {
                "id": str(c.get("id", "")),
                "author": _name(c.get("author"), "displayName"),
                "created": str(c.get("created", "")),
                "body": adf_text(c.get("body")),
            }
        )
    links = []
    for ln in f.get("issuelinks") or []:
        kind = _name(ln.get("type"))
        if ln.get("outwardIssue"):
            other, direction = ln["outwardIssue"], (ln.get("type") or {}).get("outward", kind)
        elif ln.get("inwardIssue"):
            other, direction = ln["inwardIssue"], (ln.get("type") or {}).get("inward", kind)
        else:
            continue
        links.append(
            {
                "type": kind,
                "direction": direction,
                "key": other.get("key", ""),
                "summary": _name(other.get("fields"), "summary"),
            }
        )
    project = _name(f.get("project"), "key") or (key.split("-")[0] if "-" in key else "")
    return {
        "key": key,
        "id": str(issue.get("id", "")),
        "project": project,
        "summary": str(f.get("summary") or ""),
        "description": adf_text(f.get("description")),
        "status": _name(f.get("status")),
        "assignee": _name(f.get("assignee"), "displayName") or "Unassigned",
        "priority": _name(f.get("priority")),
        "type": _name(f.get("issuetype")),
        "labels": [str(x) for x in (f.get("labels") or [])],
        "sprint": _sprint(f.get(sprint_field)) if sprint_field else None,
        "parent": _name(f.get("parent"), "key"),
        "created": str(f.get("created") or ""),
        "updated": str(f.get("updated") or ""),
        "url": f"{base_url}/browse/{key}",
        "comments": comments,
        "links": links,
    }


def project_facts(flat_issues: list[dict], as_of: str, window: str) -> dict:
    counts = lambda k: dict(sorted(Counter(i.get(k) or "" for i in flat_issues).items()))  # noqa: E731
    active = Counter(
        i["sprint"]["name"]
        for i in flat_issues
        if i.get("sprint") and i["sprint"].get("state") == "active"
    )
    sprint = None
    if active:
        sprint = {"name": active.most_common(1)[0][0], "state": "active", "start": "", "end": ""}
    else:
        named = Counter(i["sprint"]["name"] for i in flat_issues if i.get("sprint"))
        if named:
            name = named.most_common(1)[0][0]
            state = next(
                i["sprint"]["state"]
                for i in flat_issues
                if i.get("sprint") and i["sprint"]["name"] == name
            )
            sprint = {"name": name, "state": state, "start": "", "end": ""}
    return {
        "issues": {
            "total": len(flat_issues),
            "by_status": counts("status"),
            "by_priority": counts("priority"),
            "by_type": counts("type"),
            "by_assignee": counts("assignee"),
        },
        "sprint": sprint,
        "as_of": as_of,
        "window": window,
    }


def write_project_facts(project: str, facts: dict) -> str:
    p = fd.data_path("facts.json", mkdir=True)
    all_facts = fd.read_json(p, {}) or {}
    all_facts.setdefault("jira_projects", {})[project] = facts
    fd.write_json(p, all_facts)
    return p


# ---------------------------------------------------------------------------
# the connector
# ---------------------------------------------------------------------------
class JiraLiveConnector(BaseConnector):
    source_name = "jira_live"

    def __init__(self, tenant: str, config: dict, transport=None):
        super().__init__(tenant, config)
        self.base = (config.get("url") or os.environ.get("JIRA_URL", "")).rstrip("/")
        self.email = config.get("email") or os.environ.get("JIRA_EMAIL", "")
        self.token = config.get("token") or os.environ.get("JIRA_TOKEN", "")
        self.projects = [str(p).upper() for p in config.get("projects", [])]
        self.interval = str(config.get("interval", "7d"))
        self.acl = list(config.get("acl", ["public"]))
        self.page_size = int(config.get("page_size", 100))
        self.max_issues = int(config.get("max_issues", 5000))
        self.facts_max_issues = int(config.get("facts_max_issues", 2000))
        self.write_facts = bool(config.get("write_facts", True))
        self._sprint_field = config.get("sprint_field")
        self.board_id = config.get("board_id")  # T97: the board whose columns define the workflow
        self._transport = transport or http_transport
        self._classic = False  # switched on when /search/jql is not served
        self._statuses: dict[str, str] | None = None
        self.last_tombstones: list[str] = []
        self.calls = 0

    def scopes(self) -> list[str]:
        return ["jira:read"]

    # -- HTTP ---------------------------------------------------------------
    def _require(self) -> None:
        missing = [
            n
            for n, v in (
                ("JIRA_URL", self.base),
                ("JIRA_EMAIL", self.email),
                ("JIRA_TOKEN", self.token),
            )
            if not v
        ]
        if missing:
            raise ConnectorConfigError(
                "jira_live needs "
                + ", ".join(missing)
                + " (env or connector config url/email/token)"
            )

    def _headers(self) -> dict:
        return {"Authorization": basic_auth(self.email, self.token), "Accept": "application/json"}

    def _get(self, path: str, params: dict | None = None, *, ok_missing: bool = False):
        self._require()
        url = self.base + path
        if params:
            url += "?" + urllib.parse.urlencode(params, doseq=True)
        self.calls += 1
        status, body = self._transport(url, self._headers(), 30)
        if ok_missing and status in (404, 410):
            return None
        if status < 200 or status >= 300:
            raise ConnectorError(status, body.decode("utf-8", "replace"), url)
        try:
            return json.loads(body.decode("utf-8")) if body else {}
        except ValueError as e:
            raise ConnectorError(
                status, body[:600].decode("utf-8", "replace"), url, detail="non-JSON reply"
            ) from e

    # -- search -------------------------------------------------------------
    def sprint_field(self) -> str | None:
        """The custom field id that carries the sprint (``/rest/api/3/field``)."""
        if self._sprint_field is None:
            self._sprint_field = ""
            fields = self._get("/rest/api/3/field", ok_missing=True) or []
            for f in fields:
                if str(f.get("name", "")).lower() == "sprint":
                    self._sprint_field = str(f.get("id", ""))
                    break
        return self._sprint_field or None

    # -- board (T97) --------------------------------------------------------
    def status_names(self) -> dict[str, str]:
        """``status id → name`` for every workflow status (``/rest/api/3/status``,
        cached). Board columns reference statuses by id; the facts and the
        by_status table are keyed by name, so we resolve ids to names once."""
        if self._statuses is None:
            self._statuses = {}
            for s in self._get("/rest/api/3/status", ok_missing=True) or []:
                sid, name = str(s.get("id", "")), str(s.get("name", ""))
                if sid and name:
                    self._statuses[sid] = name
        return self._statuses

    def board_config(self, board_id) -> dict | None:
        """The board's columns — each a named group of statuses — from the Agile
        API (``/rest/agile/1.0/board/{id}/configuration``). ``None`` when the
        board is not served (404/410) so a missing board never fails the sync."""
        data = self._get(f"/rest/agile/1.0/board/{board_id}/configuration", ok_missing=True)
        if not data:
            return None
        names = self.status_names()
        columns = []
        for col in (data.get("columnConfig") or {}).get("columns") or []:
            sids = [str((s or {}).get("id", "")) for s in (col.get("statuses") or [])]
            columns.append(
                {
                    "name": str(col.get("name", "")),
                    "statuses": [names.get(sid, sid) for sid in sids if sid],
                }
            )
        return {"id": int(board_id), "name": str(data.get("name", "")), "columns": columns}

    def active_sprint(self, board_id) -> dict | None:
        """The board's active sprint with its dates (``/rest/agile/1.0/board/{id}/sprint``),
        or ``None`` (no active sprint / not a scrum board)."""
        data = self._get(
            f"/rest/agile/1.0/board/{board_id}/sprint", {"state": "active"}, ok_missing=True
        )
        for s in (data or {}).get("values") or []:
            return {
                "name": str(s.get("name", "")),
                "state": str(s.get("state", "active")).lower(),
                "start": str(s.get("startDate", "")),
                "end": str(s.get("endDate", "")),
            }
        return None

    def search(self, jql: str, fields: str, max_results: int = 100, cap: int | None = None):
        """Every issue matching ``jql`` (paginated), up to ``cap``."""
        out: list[dict] = []
        cap = cap or self.max_issues
        if not self._classic:
            token = None
            while True:
                params = {
                    "jql": jql,
                    "fields": fields,
                    "maxResults": min(max_results, cap - len(out)),
                }
                if token:
                    params["nextPageToken"] = token
                data = self._get("/rest/api/3/search/jql", params, ok_missing=True)
                if data is None:
                    self._classic = True
                    break
                out.extend(data.get("issues") or [])
                token = data.get("nextPageToken")
                if not token or data.get("isLast") or len(out) >= cap:
                    return out[:cap]
        start = 0
        while True:
            data = self._get(
                "/rest/api/3/search",
                {
                    "jql": jql,
                    "fields": fields,
                    "maxResults": min(max_results, cap - len(out)),
                    "startAt": start,
                },
            )
            page = data.get("issues") or []
            out.extend(page)
            start += len(page)
            if not page or start >= int(data.get("total", start)) or len(out) >= cap:
                return out[:cap]

    def live_jql(
        self, jql: str, fields: list[str] | str | None = None, max_results: int = 100
    ) -> list[dict]:
        """Ad-hoc JQL for the agent: flattened issues (summary, status, comments, …)."""
        f = ",".join(fields) if isinstance(fields, (list, tuple)) else (fields or _FIELDS)
        sf = self.sprint_field()
        if sf and sf not in f:
            f += "," + sf
        return [
            flatten_issue(i, self.base, sf)
            for i in self.search(jql, f, max_results, cap=max_results)
        ]

    # -- pull ---------------------------------------------------------------
    def _window_jql(self, project: str) -> str:
        return f'project = "{project}" AND updated >= -{self.interval} ORDER BY updated ASC'

    def pull(self, cursor: str | None) -> tuple[list[RawItem], str | None]:
        self._require()
        if not self.projects:
            raise ConnectorConfigError("jira_live needs an allow-list: config projects=[KEY, …]")
        sf = self.sprint_field()
        fields = _FIELDS + (f",{sf}" if sf else "")
        census_fields = _CENSUS_FIELDS + (f",{sf}" if sf else "")
        as_of = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        items: list[RawItem] = []
        newest = cursor or ""
        # T97: the configured board (columns + active sprint dates) is fetched
        # once and stamped onto the facts of the project(s) it tracks.
        board = self.board_config(self.board_id) if self.board_id else None
        board_sprint = self.active_sprint(self.board_id) if self.board_id else None
        for project in self.projects:
            window = [
                flatten_issue(i, self.base, sf)
                for i in self.search(self._window_jql(project), fields)
            ]
            if self.write_facts:
                census = [
                    flatten_issue(i, self.base, sf)
                    for i in self.search(
                        f'project = "{project}" ORDER BY updated DESC',
                        census_fields,
                        cap=self.facts_max_issues,
                    )
                ]
                facts = project_facts(census, as_of, f"updated >= -{self.interval}")
                if board is not None:
                    facts["board"] = board
                if board_sprint is not None:  # the board's dated sprint wins over the issue guess
                    facts["sprint"] = board_sprint
                write_project_facts(project, facts)
            for flat in window:
                if cursor and flat["updated"] and flat["updated"] <= cursor:
                    continue  # already landed by an earlier pull (idempotent anyway)
                newest = max(newest, flat["updated"])
                items.append(self._record(flat, as_of))
        self.last_tombstones = []
        return items, (newest or cursor)

    def _record(self, flat: dict, as_of: str) -> RawItem:
        return RawItem(
            tenant=self.tenant,
            source=self.source_name,
            source_version=flat["updated"] or "1",
            uri=f"jira://{flat['project']}/{flat['key']}",
            mime=JIRA_MIME,
            title=f"{flat['key']} · {flat['summary']}",
            bytes_=json.dumps(flat, sort_keys=True).encode("utf-8"),
            meta={
                "acl": list(self.acl),
                "source_kind": "jira",
                "citation_url": flat["url"],
                "arrived_at": now_ms(),
                "as_of": as_of,
                "provenance": {"project": flat["project"], "issue": flat["key"]},
                "jira": {
                    k: flat[k]
                    for k in (
                        "status",
                        "priority",
                        "type",
                        "assignee",
                        "labels",
                        "sprint",
                        "updated",
                    )
                },
            },
        )


def live_jql(
    jql: str, fields: list[str] | None = None, max_results: int = 100, transport=None
) -> list[dict]:
    """Module-level convenience for the agent: env-configured connector, one query."""
    return JiraLiveConnector("agent", {}, transport=transport).live_jql(jql, fields, max_results)


def sync(platform, tenant: str, transport=None) -> dict:
    """``scripts/ingest.py --jira``: pull the allow-listed projects (admin
    config ``projects``, else ``JIRA_PROJECTS`` comma-separated) and ingest;
    ``skipped`` with the reason when the credentials are not configured."""
    from ..ingestion.sync import SyncManager
    from . import admin

    cfg = admin.effective_config(platform, tenant, "jira", {})
    if not cfg.get("projects") and os.environ.get("JIRA_PROJECTS"):
        cfg["projects"] = [p.strip() for p in os.environ["JIRA_PROJECTS"].split(",") if p.strip()]
    have = all(
        cfg.get(k) or os.environ.get(env)
        for k, env in (("url", "JIRA_URL"), ("token", "JIRA_TOKEN"))
    )
    if not have:
        return {"status": "skipped", "reason": "JIRA_URL / JIRA_EMAIL / JIRA_TOKEN not set"}
    if not cfg.get("projects"):
        return {"status": "skipped", "reason": "no Jira projects allow-listed (JIRA_PROJECTS)"}
    res = SyncManager(platform).sync(tenant, "jira", cfg, transport=transport)
    res["status"] = "ran"
    return res
