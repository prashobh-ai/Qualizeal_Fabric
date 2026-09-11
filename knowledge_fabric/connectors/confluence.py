"""Confluence Cloud connector (T41) — REST v2 pages by space, read-only.

    env: CONFLUENCE_URL  CONFLUENCE_EMAIL  CONFLUENCE_TOKEN   (basic auth)
    config: {spaces: [KEY…], acl: [...], attachments: true, max_attachment_bytes: 20MB,
             page_size: 100, max_pages: 5000, write_facts: true}

* ``GET /wiki/api/v2/spaces?keys=…`` resolves the allow-listed space keys to ids;
  ``GET /wiki/api/v2/pages?space-id=…&body-format=storage`` lists pages with
  their storage body (XHTML) → one ``text/html`` canonical record per page
  (the converter's HTML path), cited at the page's web URL.
* Attachments (``/wiki/api/v2/pages/{id}/attachments``) are downloaded and
  emitted with their own media type so the converter routes them like any
  other file (xlsx → tables, png → images, pdf → Docling, …).
* Facts → ``data/facts.json["confluence_spaces"][KEY] = {pages, last_updated, as_of}``.
* ``live_cql(cql)`` — the agent's ad-hoc search (``/wiki/rest/api/search``).

Transport injection and error semantics are shared with ``jira_live``.
"""

from __future__ import annotations

import html
import json
import os
import time
import urllib.parse

from .. import fabric_data as fd
from ..contracts.types import RawItem, now_ms
from .base import BaseConnector
from .jira_live import ConnectorConfigError, ConnectorError, basic_auth, http_transport

_DEFAULT_MAX_ATTACHMENT = 20 * 1024 * 1024


class ConfluenceConnector(BaseConnector):
    source_name = "confluence"

    def __init__(self, tenant: str, config: dict, transport=None):
        super().__init__(tenant, config)
        base = (config.get("url") or os.environ.get("CONFLUENCE_URL", "")).rstrip("/")
        if base.endswith("/wiki"):
            base = base[: -len("/wiki")]
        self.base = base
        self.email = config.get("email") or os.environ.get("CONFLUENCE_EMAIL", "")
        self.token = config.get("token") or os.environ.get("CONFLUENCE_TOKEN", "")
        self.spaces = [str(s).upper() for s in config.get("spaces", [])]
        self.acl = list(config.get("acl", ["public"]))
        self.attachments = bool(config.get("attachments", True))
        self.max_attachment_bytes = int(config.get("max_attachment_bytes", _DEFAULT_MAX_ATTACHMENT))
        self.page_size = int(config.get("page_size", 100))
        self.max_pages = int(config.get("max_pages", 5000))
        self.write_facts = bool(config.get("write_facts", True))
        self._transport = transport or http_transport
        self.last_tombstones: list[str] = []
        self.skipped: list[dict] = []
        self.calls = 0

    def scopes(self) -> list[str]:
        return ["confluence:read"]

    # -- HTTP ---------------------------------------------------------------
    def _require(self) -> None:
        missing = [
            n
            for n, v in (
                ("CONFLUENCE_URL", self.base),
                ("CONFLUENCE_EMAIL", self.email),
                ("CONFLUENCE_TOKEN", self.token),
            )
            if not v
        ]
        if missing:
            raise ConnectorConfigError(
                "confluence needs "
                + ", ".join(missing)
                + " (env or connector config url/email/token)"
            )

    def _headers(self, accept: str = "application/json") -> dict:
        return {"Authorization": basic_auth(self.email, self.token), "Accept": accept}

    def _raw(self, path_or_url: str) -> bytes:
        self._require()
        url = path_or_url if path_or_url.startswith("http") else self.base + path_or_url
        self.calls += 1
        status, body = self._transport(url, self._headers("*/*"), 60)
        if status < 200 or status >= 300:
            raise ConnectorError(status, body.decode("utf-8", "replace"), url)
        return body

    def _get(self, path: str, params: dict | None = None) -> dict:
        url = path
        if params:
            url += ("&" if "?" in url else "?") + urllib.parse.urlencode(params, doseq=True)
        body = self._raw(url)
        try:
            return json.loads(body.decode("utf-8")) if body else {}
        except ValueError as e:
            raise ConnectorError(
                200, body[:600].decode("utf-8", "replace"), url, detail="non-JSON reply"
            ) from e

    def _paged(self, path: str, params: dict, cap: int) -> list[dict]:
        """Follow v2 ``_links.next`` cursors until exhausted or ``cap``."""
        out: list[dict] = []
        data = self._get(path, params)
        while True:
            out.extend(data.get("results") or [])
            nxt = (data.get("_links") or {}).get("next")
            if not nxt or len(out) >= cap:
                return out[:cap]
            data = self._get(nxt if nxt.startswith("/") else "/wiki" + nxt)

    # -- API ----------------------------------------------------------------
    def list_spaces(self) -> list[dict]:
        params = {"limit": 250}
        if self.spaces:
            params["keys"] = ",".join(self.spaces)
        return [
            {
                "id": str(s.get("id")),
                "key": str(s.get("key", "")).upper(),
                "name": s.get("name", ""),
            }
            for s in self._paged("/wiki/api/v2/spaces", params, 1000)
        ]

    def list_pages(self, space_id: str) -> list[dict]:
        return self._paged(
            "/wiki/api/v2/pages",
            {
                "space-id": space_id,
                "body-format": "storage",
                "limit": self.page_size,
                "sort": "-modified-date",
            },
            self.max_pages,
        )

    def list_attachments(self, page_id: str) -> list[dict]:
        return self._paged(f"/wiki/api/v2/pages/{page_id}/attachments", {"limit": 50}, 500)

    def live_cql(self, cql: str, limit: int = 25) -> list[dict]:
        """Ad-hoc CQL for the agent → ``[{id, title, type, url, excerpt, last_modified}]``."""
        data = self._get("/wiki/rest/api/search", {"cql": cql, "limit": limit})
        out = []
        for r in data.get("results") or []:
            content = r.get("content") or {}
            web = (content.get("_links") or {}).get("webui") or (r.get("url") or "")
            out.append(
                {
                    "id": str(content.get("id", "")),
                    "title": r.get("title") or content.get("title", ""),
                    "type": content.get("type", r.get("entityType", "")),
                    "url": self._web(web),
                    "excerpt": html.unescape(r.get("excerpt", "") or ""),
                    "last_modified": r.get("lastModified", ""),
                }
            )
        return out

    def _web(self, webui: str) -> str:
        if not webui:
            return self.base
        if webui.startswith("http"):
            return webui
        return self.base + ("" if webui.startswith("/wiki") else "/wiki") + webui

    # -- pull ---------------------------------------------------------------
    def pull(self, cursor: str | None) -> tuple[list[RawItem], str | None]:
        self._require()
        if not self.spaces:
            raise ConnectorConfigError("confluence needs an allow-list: config spaces=[KEY, …]")
        as_of = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        items: list[RawItem] = []
        newest = cursor or ""
        self.skipped = []
        for space in self.list_spaces():
            if space["key"] not in self.spaces:
                continue
            pages = self.list_pages(space["id"])
            last = max(
                (str((p.get("version") or {}).get("createdAt", "")) for p in pages), default=""
            )
            if self.write_facts:
                self._write_facts(
                    space["key"], {"pages": len(pages), "last_updated": last, "as_of": as_of}
                )
            for page in pages:
                version = page.get("version") or {}
                modified = str(version.get("createdAt", ""))
                if cursor and modified and modified <= cursor:
                    continue
                newest = max(newest, modified)
                items.append(self._page_record(space, page, as_of))
                if self.attachments:
                    items.extend(self._attachment_records(space, page, as_of))
        self.last_tombstones = []
        return items, (newest or cursor)

    def _page_record(self, space: dict, page: dict, as_of: str) -> RawItem:
        version = page.get("version") or {}
        body = ((page.get("body") or {}).get("storage") or {}).get("value", "") or ""
        title = str(page.get("title", ""))
        url = self._web((page.get("_links") or {}).get("webui", ""))
        page_id = str(page.get("id", ""))
        doc = f"<h1>{html.escape(title)}</h1>\n{body}"
        return RawItem(
            tenant=self.tenant,
            source=self.source_name,
            source_version=str(version.get("number", "1")),
            uri=f"confluence://{space['key']}/{page_id}",
            mime="text/html",
            title=title,
            bytes_=doc.encode("utf-8"),
            meta={
                "acl": list(self.acl),
                "source_kind": "confluence",
                "citation_url": url,
                "arrived_at": now_ms(),
                "as_of": as_of,
                "provenance": {
                    "space": space["key"],
                    "page_id": page_id,
                    "version": version.get("number"),
                },
                "confluence": {
                    "space": space["key"],
                    "page_id": page_id,
                    "version": version.get("number"),
                    "modified": str(version.get("createdAt", "")),
                    "author": str(version.get("authorId", "")),
                },
            },
        )

    def _attachment_records(self, space: dict, page: dict, as_of: str) -> list[RawItem]:
        out: list[RawItem] = []
        page_id = str(page.get("id", ""))
        for att in self.list_attachments(page_id):
            title = str(att.get("title", "") or att.get("id", ""))
            size = int(att.get("fileSize", 0) or 0)
            link = att.get("downloadLink") or (att.get("_links") or {}).get("download") or ""
            if not link:
                continue
            if size > self.max_attachment_bytes:
                self.skipped.append(
                    {"page_id": page_id, "attachment": title, "reason": f"{size} bytes > cap"}
                )
                continue
            data = self._raw(link if link.startswith("http") else "/wiki" + link)
            mime = str(att.get("mediaType") or "application/octet-stream")
            out.append(
                RawItem(
                    tenant=self.tenant,
                    source=self.source_name,
                    source_version=str((att.get("version") or {}).get("number", "1")),
                    uri=f"confluence://{space['key']}/{page_id}/attachments/{title}",
                    mime=mime,
                    title=title,
                    bytes_=data,
                    meta={
                        "acl": list(self.acl),
                        "citation_url": self._web(
                            (att.get("_links") or {}).get("webui", "") or link
                        ),
                        "arrived_at": now_ms(),
                        "as_of": as_of,
                        "provenance": {
                            "space": space["key"],
                            "page_id": page_id,
                            "attachment": title,
                        },
                        "confluence": {
                            "space": space["key"],
                            "page_id": page_id,
                            "attachment": title,
                        },
                    },
                )
            )
        return out

    @staticmethod
    def _write_facts(space: str, facts: dict) -> str:
        p = fd.data_path("facts.json", mkdir=True)
        all_facts = fd.read_json(p, {}) or {}
        all_facts.setdefault("confluence_spaces", {})[space] = facts
        fd.write_json(p, all_facts)
        return p


def live_cql(cql: str, limit: int = 25, transport=None) -> list[dict]:
    """Module-level convenience for the agent: env-configured connector, one query."""
    return ConfluenceConnector("agent", {}, transport=transport).live_cql(cql, limit)


def sync(platform, tenant: str, transport=None) -> dict:
    """``scripts/ingest.py --confluence``: pull the allow-listed spaces (admin
    config ``spaces``, else ``CONFLUENCE_SPACES`` comma-separated) and ingest;
    ``skipped`` with the reason when the credentials are not configured."""
    from ..ingestion.sync import SyncManager
    from . import admin

    cfg = admin.effective_config(platform, tenant, "confluence", {})
    if not cfg.get("spaces") and os.environ.get("CONFLUENCE_SPACES"):
        cfg["spaces"] = [s.strip() for s in os.environ["CONFLUENCE_SPACES"].split(",") if s.strip()]
    have = all(
        cfg.get(k) or os.environ.get(env)
        for k, env in (("url", "CONFLUENCE_URL"), ("token", "CONFLUENCE_TOKEN"))
    )
    if not have:
        return {
            "status": "skipped",
            "reason": "CONFLUENCE_URL / CONFLUENCE_EMAIL / CONFLUENCE_TOKEN not set",
        }
    if not cfg.get("spaces"):
        return {
            "status": "skipped",
            "reason": "no Confluence spaces allow-listed (CONFLUENCE_SPACES)",
        }
    res = SyncManager(platform).sync(tenant, "confluence", cfg, transport=transport)
    res["status"] = "ran"
    return res
