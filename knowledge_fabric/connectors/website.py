"""Website connector — the public site as a source (``scripts/ingest.py --website``).

A bounded, same-host crawl (``KF_WEBSITE_URL``, or config ``url``/``urls``;
``max_pages`` 60, ``depth`` 2) fetched over ``urllib`` through an injectable
transport. Every HTML page becomes ONE canonical record (``text/html``, uri =
the page URL, ``citation_url`` = the page URL) that the converter turns into
cited paragraphs. Read-only; the cursor is the crawl time (the pipeline is
idempotent by content hash, so re-crawls are no-ops for unchanged pages).
``sync(platform, tenant)`` reports ``skipped`` when no URL is configured.
"""

from __future__ import annotations

import os
import re
import time
import urllib.error
import urllib.parse
import urllib.request
from html.parser import HTMLParser

from ..contracts.types import RawItem, now_ms
from .base import BaseConnector

_SKIP_EXT = (
    ".png",
    ".jpg",
    ".jpeg",
    ".gif",
    ".svg",
    ".pdf",
    ".zip",
    ".css",
    ".js",
    ".ico",
    ".woff",
)


class WebsiteError(RuntimeError):
    """A page fetch failed with a status the crawl cannot proceed from."""


def http_transport(url: str, headers: dict, timeout: int = 20) -> tuple[int, bytes]:
    req = urllib.request.Request(url, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.status, resp.read()
    except urllib.error.HTTPError as e:
        return e.code, e.read()
    except urllib.error.URLError as e:
        raise ConnectionError(str(e.reason)) from e


class _Links(HTMLParser):
    def __init__(self):
        super().__init__()
        self.links: list[str] = []
        self.title = ""
        self._in_title = False

    def handle_starttag(self, tag, attrs):
        if tag == "a":
            href = dict(attrs).get("href")
            if href:
                self.links.append(href)
        elif tag == "title":
            self._in_title = True

    def handle_endtag(self, tag):
        if tag == "title":
            self._in_title = False

    def handle_data(self, data):
        if self._in_title:
            self.title += data


def _canon(url: str) -> str:
    u = urllib.parse.urlsplit(url)
    path = re.sub(r"/+$", "", u.path) or "/"
    return urllib.parse.urlunsplit((u.scheme, u.netloc.lower(), path, u.query, ""))


class WebsiteConnector(BaseConnector):
    source_name = "website"

    def __init__(self, tenant: str, config: dict, transport=None):
        super().__init__(tenant, config)
        urls = config.get("urls") or ([config["url"]] if config.get("url") else [])
        if not urls and os.environ.get("KF_WEBSITE_URL"):
            urls = [u.strip() for u in os.environ["KF_WEBSITE_URL"].split(",") if u.strip()]
        self.urls = [u if "://" in u else "https://" + u for u in urls]
        self.max_pages = int(config.get("max_pages", 60))
        self.depth = int(config.get("depth", 2))
        self.acl = list(config.get("acl", ["public"]))
        self._transport = transport or http_transport
        self.last_tombstones: list[str] = []
        self.fetched: list[dict] = []

    def scopes(self) -> list[str]:
        return ["web:read"]

    def crawl(self) -> list[tuple[str, str, bytes]]:
        """``[(url, title, html_bytes)]`` breadth-first within the start hosts."""
        hosts = {urllib.parse.urlsplit(u).netloc.lower() for u in self.urls}
        queue = [(_canon(u), 0) for u in self.urls]
        seen: set[str] = set()
        out: list[tuple[str, str, bytes]] = []
        while queue and len(out) < self.max_pages:
            url, d = queue.pop(0)
            if url in seen:
                continue
            seen.add(url)
            try:
                status, body = self._transport(
                    url, {"User-Agent": "QualiZeal-Knowledge-Fabric/1.0", "Accept": "text/html"}, 20
                )
            except (ConnectionError, OSError) as e:
                self.fetched.append({"url": url, "status": f"unreachable: {str(e)[:80]}"})
                continue
            self.fetched.append({"url": url, "status": status})
            if status != 200:
                continue
            html = body.decode("utf-8", "replace")
            if "<html" not in html.lower()[:2000] and "<body" not in html.lower():
                continue
            p = _Links()
            try:
                p.feed(html)
            except Exception:  # noqa: BLE001 — a broken page still counts as fetched
                pass
            out.append((url, p.title.strip() or url, body))
            if d < self.depth:
                for href in p.links:
                    nxt = urllib.parse.urljoin(url, href.split("#", 1)[0])
                    su = urllib.parse.urlsplit(nxt)
                    if su.scheme not in ("http", "https") or su.netloc.lower() not in hosts:
                        continue
                    if su.path.lower().endswith(_SKIP_EXT):
                        continue
                    queue.append((_canon(nxt), d + 1))
        return out

    def pull(self, cursor: str | None) -> tuple[list[RawItem], str | None]:
        if not self.urls:
            raise WebsiteError("website connector needs KF_WEBSITE_URL (or config url/urls)")
        as_of = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        items = [
            RawItem(
                tenant=self.tenant,
                source=self.source_name,
                source_version=as_of,
                uri=url,
                mime="text/html",
                title=title[:200],
                bytes_=body,
                meta={
                    "acl": list(self.acl),
                    "source_kind": "document",
                    "citation_url": url,
                    "arrived_at": now_ms(),
                    "as_of": as_of,
                },
            )
            for url, title, body in self.crawl()
        ]
        self.last_tombstones = []
        return items, as_of


def sync(platform, tenant: str, transport=None) -> dict:
    """``scripts/ingest.py --website``: crawl and ingest, or say why not."""
    from ..ingestion.sync import SyncManager
    from . import admin

    cfg = admin.effective_config(platform, tenant, "website", {})
    if not (cfg.get("url") or cfg.get("urls") or os.environ.get("KF_WEBSITE_URL")):
        return {"status": "skipped", "reason": "KF_WEBSITE_URL not set"}
    res = SyncManager(platform).sync(tenant, "website", cfg, transport=transport)
    res["status"] = "ran"
    return res
