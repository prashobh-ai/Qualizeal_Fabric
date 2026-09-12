"""Live GitHub connector (T37) — REST + GraphQL, read-only, allow-listed.

Scope: every repository of ``GITHUB_ORG`` plus ``GITHUB_EXTRA_REPOS``
(comma-separated ``owner/repo``); config ``org`` / ``repos`` override the
environment, the admin allow-list (``repos``) is applied last. Auth is
``GITHUB_TOKEN`` (``KF_GITHUB_TOKEN`` for a fine-grained PAT).

Per repository (see the spec table): metadata, languages, contributors,
exact commit count (GraphQL ``history { totalCount }``), commit history (≤
2000), pull requests with reviews, comments and files, issues with
comments, releases and tags, deployments with statuses, workflows with the
latest run — into ``data/facts.json["repositories"]["owner/repo"]`` — and a
shallow clone under ``<fabric root>/clones/<slug>`` whose README, docs,
code, manifests, notebooks and images go through the pipeline.

Records:

* ``github://o/r/pulls/<n>``, ``github://o/r/issues/<n>``,
  ``github://o/r/commits/<sha>``, ``github://o/r/releases/<tag>`` — pre-chunked
  passages (``PASSAGES_MIME``): every passage carries its own citation URL
  (``/pull/<n>``, ``/issues/<n>``, ``/commit/<sha>``, ``/releases/tag/<t>``).
* ``github://o/r/<path>`` — files from the clone (code → symbol passages).

Areas: ``Code``, ``Architecture``, ``Work item``, ``Release``, ``Repository``.
ACL: public → ``group:everyone``; private → ``group:gh:<org>`` plus the
collaborators (``user:<login>``). Rate limits are honoured from the response
headers (``rate_limit_remaining()`` feeds Admin → Sources); an exhausted limit
raises ``ConnectorError`` naming the reset time — never a silent skip. The
cursor is ``{repo: pushed_at}``: unchanged repositories are skipped.

Transport is injectable — ``transport(url, headers, timeout, method, body)
-> (status, headers, bytes)`` — and so is the clone runner, so the connector is
fully testable offline.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import time
import urllib.error
import urllib.parse
import urllib.request

from .. import fabric_data as fd
from ..adapters.converter import PASSAGES_MIME
from ..contracts.types import RawItem, now_ms
from .base import BaseConnector

API = os.environ.get("KF_GITHUB_API", "https://api.github.com").rstrip("/")
WEB = os.environ.get("KF_GITHUB_WEB", "https://github.com").rstrip("/")
MAX_COMMITS = 2000
MAX_COMMIT_DOCS = 300
MAX_PRS = 500
MAX_ISSUES = 500
MAX_FILES = 1500
MAX_FILE_BYTES = 600_000
MAX_IMAGE_BYTES = 2_000_000
CODE_EXT = (".py", ".js", ".jsx", ".mjs", ".cjs", ".ts", ".tsx", ".java", ".go", ".cs", ".rb",
            ".sql", ".sh", ".ipynb")  # fmt: skip
DOC_EXT = (".md", ".rst", ".txt", ".adoc")
MANIFEST_NAMES = (
    "pyproject.toml",
    "package.json",
    "go.mod",
    "pom.xml",
    "Gemfile",
    "Cargo.toml",
    "Pipfile",
    "Dockerfile",
    "Makefile",
    "build.gradle",
    "build.gradle.kts",
)
IMAGE_EXT = (".png", ".jpg", ".jpeg", ".gif", ".webp", ".svg")
CONFIG_EXT = (".yaml", ".yml", ".toml", ".cfg", ".ini", ".tf", ".csproj")
SKIP_DIRS = {".git", "node_modules", "vendor", "dist", "build", "__pycache__", ".venv", "venv"}


class ConnectorError(RuntimeError):
    """A GitHub call failed (HTTP status + body, rate limit, or unreachable)."""

    def __init__(self, status: int, body: str, url: str = "", detail: str = ""):
        self.status, self.body, self.url = status, body, url
        msg = detail or f"GitHub call returned HTTP {status}"
        super().__init__(f"{msg} ({_redact(url)}): {_redact(body)[:600]}")


class ConnectorConfigError(ValueError):
    """Required credentials/config are missing — raised on use, never on construction."""


def _redact(text: str) -> str:
    return re.sub(
        r"(gh[pousr]_[A-Za-z0-9]{8,}|github_pat_[A-Za-z0-9_]{8,})", "***", str(text or "")
    )


def http_transport(
    url: str, headers: dict, timeout: int = 30, method: str = "GET", body: bytes | None = None
) -> tuple[int, dict, bytes]:
    req = urllib.request.Request(url, data=body, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.status, dict(resp.headers.items()), resp.read()
    except urllib.error.HTTPError as e:
        return e.code, dict(e.headers.items()), e.read()
    except urllib.error.URLError as e:
        raise ConnectionError(str(e.reason)) from e


# ---------------------------------------------------------------------------
# rate limit — persisted so Admin → Sources can show it without a call
# ---------------------------------------------------------------------------
def _rate_path() -> str:
    return fd.data_path("github_rate_limit.json")


def rate_limit_remaining() -> int | None:
    """The remaining core rate limit recorded by the last live call (or None)."""
    d = fd.read_json(_rate_path(), {}) or {}
    v = d.get("remaining")
    return int(v) if isinstance(v, (int, float, str)) and str(v).lstrip("-").isdigit() else None


def _acl_for(meta: dict, org: str, collaborators: list[str]) -> list[str]:
    if (meta.get("visibility") or "public") == "public" and not meta.get("private"):
        return ["group:everyone", "public"]
    acl = [f"group:gh:{org or meta.get('owner', '')}"]
    acl += [f"user:{c}" for c in collaborators]
    return acl


def _iso(v) -> str:
    return str(v or "")


class GitHubLiveConnector(BaseConnector):
    source_name = "github_live"

    def __init__(self, tenant: str, config: dict, transport=None, runner=None):
        super().__init__(tenant, config)
        self.token = (
            config.get("token")
            or os.environ.get("KF_GITHUB_TOKEN", "")
            or os.environ.get("GITHUB_TOKEN", "")
        )
        self.org = (config.get("org") or os.environ.get("GITHUB_ORG", "")).strip()
        extra = list(config.get("repos") or [])
        if not extra and os.environ.get("GITHUB_EXTRA_REPOS"):
            extra = [r.strip() for r in os.environ["GITHUB_EXTRA_REPOS"].split(",") if r.strip()]
        self.extra_repos = [r for r in extra if "/" in r]
        self.allow = set(config.get("repos") or [])  # admin allow-list (same key)
        self.clone_enabled = bool(config.get("clone", True))
        self.clone_root = config.get("clone_root") or fd.path("clones")
        self.max_commits = int(config.get("max_commits", MAX_COMMITS))
        self.max_commit_docs = int(config.get("max_commit_docs", MAX_COMMIT_DOCS))
        self.max_prs = int(config.get("max_prs", MAX_PRS))
        self.max_issues = int(config.get("max_issues", MAX_ISSUES))
        self.max_files = int(config.get("max_files", MAX_FILES))
        self.write_facts = bool(config.get("write_facts", True))
        self._transport = transport or http_transport
        self._runner = runner or subprocess.run
        self.last_tombstones: list[str] = []
        self.calls = 0
        self.rate: dict = {"remaining": None, "reset": None}
        self.report: list[dict] = []

    def scopes(self) -> list[str]:
        return ["repo:read", "read:org"]

    # -- transport ----------------------------------------------------------
    def _require(self) -> None:
        if not self.token:
            raise ConnectorConfigError(
                "github_live needs GITHUB_TOKEN (or KF_GITHUB_TOKEN / connector config token)"
            )

    def _headers(self, accept: str = "application/vnd.github+json") -> dict:
        return {
            "Authorization": f"Bearer {self.token}",
            "Accept": accept,
            "X-GitHub-Api-Version": "2022-11-28",
            "User-Agent": "QualiZeal-Knowledge-Fabric/1.0",
        }

    def _request(self, method: str, url: str, body: dict | None = None, ok_missing=False):
        self._require()
        data = json.dumps(body).encode("utf-8") if body is not None else None
        headers = self._headers()
        if data is not None:
            headers["Content-Type"] = "application/json"
        try:
            status, resp_headers, raw = self._transport(url, headers, 60, method, data)
        except (ConnectionError, OSError) as e:
            raise ConnectorError(0, str(e), url, detail="GitHub unreachable") from e
        self.calls += 1
        lowered = {str(k).lower(): v for k, v in (resp_headers or {}).items()}
        if "x-ratelimit-remaining" in lowered:
            self.rate = {
                "remaining": _int(lowered.get("x-ratelimit-remaining")),
                "reset": _int(lowered.get("x-ratelimit-reset")),
                "limit": _int(lowered.get("x-ratelimit-limit")),
                "at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            }
            try:
                fd.write_json(fd.data_path("github_rate_limit.json", mkdir=True), self.rate)
            except OSError:
                pass
        text = raw.decode("utf-8", "replace") if raw else ""
        if status in (403, 429) and self.rate.get("remaining") == 0:
            reset = self.rate.get("reset")
            when = time.strftime("%H:%M:%SZ", time.gmtime(reset)) if reset else "unknown"
            raise ConnectorError(status, text, url, detail=f"rate limit exhausted (resets {when})")
        if status == 404 and ok_missing:
            return None, lowered
        if status >= 400:
            raise ConnectorError(status, text, url)
        try:
            return (json.loads(text) if text else None), lowered
        except ValueError:
            return text, lowered

    def _rest(self, path: str, params: dict | None = None, ok_missing: bool = False):
        url = API + path
        if params:
            url += "?" + urllib.parse.urlencode(params)
        data, _h = self._request("GET", url, ok_missing=ok_missing)
        return data

    def _rest_paged(self, path: str, params: dict | None = None, cap: int = 1000) -> list:
        out: list = []
        page = 1
        while len(out) < cap:
            q = dict(params or {})
            q.update({"per_page": 100, "page": page})
            data, headers = self._request("GET", API + path + "?" + urllib.parse.urlencode(q))
            batch = data if isinstance(data, list) else []
            out.extend(batch)
            link = str(headers.get("link", ""))
            if len(batch) < 100 or 'rel="next"' not in link:
                break
            page += 1
        return out[:cap]

    def _graphql(self, query: str, variables: dict) -> dict:
        data, _h = self._request("POST", API + "/graphql", {"query": query, "variables": variables})
        if not isinstance(data, dict):
            raise ConnectorError(200, str(data), API + "/graphql", detail="GraphQL: no JSON")
        if data.get("errors") and not data.get("data"):
            raise ConnectorError(200, json.dumps(data["errors"])[:600], API + "/graphql",
                                 detail="GraphQL errors")  # fmt: skip
        return data.get("data") or {}

    # -- scope ---------------------------------------------------------------
    def list_repositories(self) -> list[str]:
        repos: list[str] = []
        if self.org:
            rows = self._rest_paged(
                f"/orgs/{self.org}/repos", {"type": "all", "sort": "pushed"}, 500
            )
            if not rows:
                data = self._rest(f"/orgs/{self.org}", ok_missing=True)
                if data is None:
                    rows = self._rest_paged(f"/users/{self.org}/repos", {"sort": "pushed"}, 500)
            repos += [r["full_name"] for r in rows if r.get("full_name") and not r.get("fork")]
        repos += self.extra_repos
        seen, out = set(), []
        for r in repos:
            if r in seen:
                continue
            if self.allow and r not in self.allow and r not in self.extra_repos:
                continue
            seen.add(r)
            out.append(r)
        return sorted(out)

    # -- facts ---------------------------------------------------------------
    def repo_facts(self, full: str) -> tuple[dict, dict]:
        """``(facts_block, raw)`` — the pinned schema plus the raw PR / commit /
        issue / release lists the passages are built from."""
        owner, name = full.split("/", 1)
        meta = self._rest(f"/repos/{owner}/{name}")
        langs = self._rest(f"/repos/{owner}/{name}/languages") or {}
        total_bytes = sum(int(v or 0) for v in langs.values()) or 1
        contributors = [
            {"login": c.get("login"), "contributions": int(c.get("contributions") or 0)}
            for c in self._rest_paged(f"/repos/{owner}/{name}/contributors", {"anon": "false"}, 500)
            if c.get("login")
        ]
        default_branch = meta.get("default_branch") or "main"
        g = self._graphql(_Q_REPO, {"owner": owner, "name": name})
        repo_g = g.get("repository") or {}
        history = ((repo_g.get("defaultBranchRef") or {}).get("target") or {}).get("history") or {}
        commit_total = int(history.get("totalCount") or 0)
        prs = self._fetch_prs(owner, name)
        commits = self._fetch_commits(owner, name, default_branch)
        issues = self._fetch_issues(owner, name)
        releases = self._rest_paged(f"/repos/{owner}/{name}/releases", None, 50)
        tags = self._rest_paged(f"/repos/{owner}/{name}/tags", None, 50)
        deployments = self._deployments(owner, name)
        workflows = self._workflows(owner, name)
        collaborators: list[str] = []
        if meta.get("private"):
            rows = self._rest_paged(f"/repos/{owner}/{name}/collaborators", None, 100) or []
            collaborators = [c["login"] for c in rows if c.get("login")]
        merged = [p for p in prs if p.get("merged")]
        mean = {
            k: (round(sum(int(p.get(k) or 0) for p in merged) / len(merged)) if merged else 0)
            for k in ("additions", "deletions", "changedFiles")
        }
        facts = {
            "url": meta.get("html_url") or f"{WEB}/{full}",
            "description": meta.get("description") or "",
            "homepage": meta.get("homepage") or "",
            "topics": list(meta.get("topics") or []),
            "default_branch": default_branch,
            "visibility": meta.get("visibility")
            or ("private" if meta.get("private") else "public"),
            "archived": bool(meta.get("archived")),
            "size_kb": int(meta.get("size") or 0),
            "stars": int(meta.get("stargazers_count") or 0),
            "forks": int(meta.get("forks_count") or 0),
            "license_spdx": ((meta.get("license") or {}).get("spdx_id") or "")
            if meta.get("license")
            else "",
            "created_at": _iso(meta.get("created_at")),
            "pushed_at": _iso(meta.get("pushed_at")),
            "open_issues": int(meta.get("open_issues_count") or 0),
            "languages": {
                k: {"bytes": int(v or 0), "share": round(int(v or 0) / total_bytes, 4)}
                for k, v in sorted(langs.items(), key=lambda kv: -int(kv[1] or 0))
            },
            "primary_language": meta.get("language") or (next(iter(langs), "") if langs else ""),
            "contributors": sorted(contributors, key=lambda c: -c["contributions"]),
            "contributors_count": len(contributors),
            "commits": {"total": commit_total, "fetched": len(commits)},
            "pull_requests": {
                "open": int((repo_g.get("open") or {}).get("totalCount") or 0),
                "closed": int((repo_g.get("closed") or {}).get("totalCount") or 0),
                "merged": int((repo_g.get("merged") or {}).get("totalCount") or 0),
                "total": sum(
                    int((repo_g.get(k) or {}).get("totalCount") or 0)
                    for k in ("open", "closed", "merged")
                ),
                "mean_size": {
                    "additions": mean["additions"],
                    "deletions": mean["deletions"],
                    "changed_files": mean["changedFiles"],
                },
            },
            "issues": {
                "open": int((repo_g.get("openIssues") or {}).get("totalCount") or 0),
                "closed": int((repo_g.get("closedIssues") or {}).get("totalCount") or 0),
            },
            "releases": [
                {
                    "tag": r.get("tag_name"),
                    "name": r.get("name") or r.get("tag_name"),
                    "published_at": _iso(r.get("published_at")),
                    "url": r.get("html_url"),
                }
                for r in releases
            ],
            "tags": [t.get("name") for t in tags if t.get("name")],
            "deployments": deployments,
            "workflows": workflows,
            "acl": _acl_for(meta, owner, collaborators),
            "as_of": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        }
        raw = {"prs": prs, "commits": commits, "issues": issues, "releases": releases, "meta": meta}
        return facts, raw

    def _fetch_prs(self, owner: str, name: str) -> list[dict]:
        out: list[dict] = []
        cursor = None
        while len(out) < self.max_prs:
            g = self._graphql(_Q_PRS, {"owner": owner, "name": name, "cursor": cursor})
            conn = ((g.get("repository") or {}).get("pullRequests")) or {}
            out.extend(conn.get("nodes") or [])
            info = conn.get("pageInfo") or {}
            if not info.get("hasNextPage"):
                break
            cursor = info.get("endCursor")
        return out[: self.max_prs]

    def _fetch_commits(self, owner: str, name: str, branch: str) -> list[dict]:
        out: list[dict] = []
        cursor = None
        while len(out) < self.max_commits:
            g = self._graphql(
                _Q_COMMITS,
                {"owner": owner, "name": name, "branch": f"refs/heads/{branch}", "cursor": cursor},
            )
            hist = (((g.get("repository") or {}).get("ref") or {}).get("target") or {}).get(
                "history"
            ) or {}
            out.extend(hist.get("nodes") or [])
            info = hist.get("pageInfo") or {}
            if not info.get("hasNextPage"):
                break
            cursor = info.get("endCursor")
        return out[: self.max_commits]

    def _fetch_issues(self, owner: str, name: str) -> list[dict]:
        out: list[dict] = []
        cursor = None
        while len(out) < self.max_issues:
            g = self._graphql(_Q_ISSUES, {"owner": owner, "name": name, "cursor": cursor})
            conn = ((g.get("repository") or {}).get("issues")) or {}
            out.extend(conn.get("nodes") or [])
            info = conn.get("pageInfo") or {}
            if not info.get("hasNextPage"):
                break
            cursor = info.get("endCursor")
        return out[: self.max_issues]

    def _deployments(self, owner: str, name: str) -> dict:
        rows = self._rest_paged(f"/repos/{owner}/{name}/deployments", None, 200)
        latest: dict[str, str] = {}
        for d in rows:
            env = d.get("environment") or "default"
            if env in latest:
                continue
            statuses = self._rest(
                f"/repos/{owner}/{name}/deployments/{d['id']}/statuses", {"per_page": 1}
            )
            latest[env] = (statuses[0].get("state") if statuses else "unknown") or "unknown"
        return {"environments": sorted(latest), "latest": latest, "count": len(rows)}

    def _workflows(self, owner: str, name: str) -> list[dict]:
        data = self._rest(f"/repos/{owner}/{name}/actions/workflows", ok_missing=True) or {}
        out = []
        for w in (data.get("workflows") or [])[:30]:
            runs = (
                self._rest(
                    f"/repos/{owner}/{name}/actions/workflows/{w['id']}/runs",
                    {"per_page": 1},
                    ok_missing=True,
                )
                or {}
            )
            run = (runs.get("workflow_runs") or [None])[0]
            out.append(
                {
                    "name": w.get("name"),
                    "path": w.get("path"),
                    "state": w.get("state"),
                    "last_conclusion": (run or {}).get("conclusion") if run else None,
                    "last_run_at": _iso((run or {}).get("updated_at")) if run else "",
                }
            )
        return out

    # -- passages ------------------------------------------------------------
    def _passages_record(
        self,
        full: str,
        tail: str,
        title: str,
        passages: list[dict],
        *,
        area: str,
        acl: list[str],
        version: str,
        extra: dict | None = None,
    ) -> RawItem:
        for i, p in enumerate(passages, 1):
            loc = p.setdefault("locator", {})
            loc.setdefault("page", 1)
            loc.setdefault("paragraph", i)
            loc.setdefault("area", area)
            loc.setdefault("citation_url", loc.get("url", ""))
        meta = {
            "acl": list(acl),
            "source_kind": "document",
            "citation_url": passages[0]["locator"]["url"] if passages else f"{WEB}/{full}",
            "arrived_at": now_ms(),
            "area": area,
            "repo": full,
        }
        meta.update(extra or {})
        return RawItem(
            tenant=self.tenant,
            source=self.source_name,
            source_version=version or "1",
            uri=f"github://{full}/{tail}",
            mime=PASSAGES_MIME,
            title=title[:200],
            bytes_=json.dumps(passages).encode("utf-8"),
            meta=meta,
        )

    def activity_records(self, full: str, raw: dict, acl: list[str]) -> list[RawItem]:
        items: list[RawItem] = []
        base = f"{WEB}/{full}"
        for pr in raw.get("prs") or []:
            n = pr.get("number")
            url = pr.get("url") or f"{base}/pull/{n}"
            author = (pr.get("author") or {}).get("login") or "unknown"
            state = "merged" if pr.get("merged") else str(pr.get("state") or "").lower()
            head = (
                f"Pull request #{n} ({state}) by {author}: {pr.get('title', '')}. "
                f"Opened {pr.get('createdAt', '')}"
                + (f", merged {pr['mergedAt']}" if pr.get("mergedAt") else "")
                + f". {pr.get('baseRefName', '')} ← {pr.get('headRefName', '')}; "
                f"+{pr.get('additions', 0)}/−{pr.get('deletions', 0)} in "
                f"{pr.get('changedFiles', 0)} files."
                + (
                    " Labels: "
                    + ", ".join(
                        lb.get("name", "") for lb in (pr.get("labels") or {}).get("nodes") or []
                    )
                    if ((pr.get("labels") or {}).get("nodes"))
                    else ""
                )
            )
            passages = [{"text": head, "locator": {"url": url, "kind": "pull_request"}}]
            if pr.get("body"):
                passages.append(
                    {
                        "text": pr["body"][:6000],
                        "locator": {"url": url, "kind": "pull_request_body"},
                    }
                )
            for rv in (pr.get("reviews") or {}).get("nodes") or []:
                if rv.get("body") or rv.get("state"):
                    passages.append(
                        {
                            "text": (
                                f"Review by {_login(rv)} ({rv.get('state', '')}): "
                                f"{rv.get('body') or ''}"
                            )[:4000],
                            "locator": {"url": rv.get("url") or url, "kind": "review"},
                        }
                    )
            for c in (pr.get("comments") or {}).get("nodes") or []:
                if c.get("body"):
                    passages.append(
                        {
                            "text": f"Comment by {_login(c)}: {c['body']}"[:4000],
                            "locator": {"url": c.get("url") or url, "kind": "comment"},
                        }
                    )
            files = [
                f.get("path") for f in (pr.get("files") or {}).get("nodes") or [] if f.get("path")
            ]
            if files:
                passages.append(
                    {
                        "text": "Files changed: " + ", ".join(files[:50]),
                        "locator": {"url": url, "kind": "files"},
                    }
                )
            items.append(
                self._passages_record(
                    full,
                    f"pulls/{n}",
                    f"PR #{n}: {pr.get('title', '')}",
                    passages,
                    area="Work item",
                    acl=acl,
                    version=_iso(pr.get("updatedAt")),
                    extra={
                        "pr": {
                            "number": n,
                            "state": state,
                            "author": author,
                            "merged_at": pr.get("mergedAt"),
                        }
                    },
                )  # fmt: skip
            )
        for issue in raw.get("issues") or []:
            n = issue.get("number")
            url = issue.get("url") or f"{base}/issues/{n}"
            author = (issue.get("author") or {}).get("login") or "unknown"
            passages = [
                {
                    "text": f"Issue #{n} ({str(issue.get('state') or '').lower()}) by {author}: "
                    f"{issue.get('title', '')}. Opened {issue.get('createdAt', '')}"
                    + (f", closed {issue['closedAt']}" if issue.get("closedAt") else "")
                    + ".",
                    "locator": {"url": url, "kind": "issue"},
                }
            ]
            if issue.get("body"):
                passages.append(
                    {"text": issue["body"][:6000], "locator": {"url": url, "kind": "issue_body"}}
                )
            for c in (issue.get("comments") or {}).get("nodes") or []:
                if c.get("body"):
                    passages.append(
                        {
                            "text": f"Comment by {_login(c)}: {c['body']}"[:4000],
                            "locator": {"url": c.get("url") or url, "kind": "comment"},
                        }
                    )
            items.append(
                self._passages_record(
                    full,
                    f"issues/{n}",
                    f"Issue #{n}: {issue.get('title', '')}",
                    passages,
                    area="Work item",
                    acl=acl,
                    version=_iso(issue.get("updatedAt")),
                )  # fmt: skip
            )
        commits = raw.get("commits") or []
        for c in commits[: self.max_commit_docs]:
            sha = c.get("oid") or ""
            url = c.get("url") or f"{base}/commit/{sha}"
            who = _committer(c)
            msg = c.get("message") or c.get("messageHeadline") or ""
            text = f"Commit {sha[:12]} by {who} on {c.get('committedDate', '')}: {msg}"[:4000]
            items.append(
                self._passages_record(
                    full,
                    f"commits/{sha}",
                    f"Commit {sha[:7]}: {c.get('messageHeadline', '')}",
                    [{"text": text, "locator": {"url": url, "kind": "commit"}}],
                    area="Repository",
                    acl=acl,
                    version=sha,
                )  # fmt: skip
            )
        if len(commits) > self.max_commit_docs:
            rest = commits[self.max_commit_docs :]
            passages = [
                {
                    "text": f"Commit {c.get('oid', '')[:12]} by {_committer(c)} on "
                    f"{c.get('committedDate', '')}: {c.get('messageHeadline') or ''}",
                    "locator": {
                        "url": c.get("url") or f"{base}/commit/{c.get('oid', '')}",
                        "kind": "commit",
                    },
                }
                for c in rest
            ]
            items.append(
                self._passages_record(
                    full,
                    "commits/history",
                    f"{full} — commit history ({len(rest)} older commits)",
                    passages,
                    area="Repository",
                    acl=acl,
                    version=rest[0].get("oid", ""),
                )  # fmt: skip
            )
        for r in raw.get("releases") or []:
            tag = r.get("tag_name") or ""
            url = r.get("html_url") or f"{base}/releases/tag/{tag}"
            passages = [
                {
                    "text": f"Release {r.get('name') or tag} ({tag}) published "
                    f"{r.get('published_at', '')}"
                    + (" (pre-release)" if r.get("prerelease") else "")
                    + ".",
                    "locator": {"url": url, "kind": "release"},
                }
            ]
            if r.get("body"):
                passages.append(
                    {"text": r["body"][:8000], "locator": {"url": url, "kind": "release_notes"}}
                )
            items.append(
                self._passages_record(
                    full,
                    f"releases/{tag}",
                    f"Release {r.get('name') or tag}",
                    passages,
                    area="Release",
                    acl=acl,
                    version=_iso(r.get("published_at")),
                )  # fmt: skip
            )
        return items

    # -- clone + files -------------------------------------------------------
    def clone(self, full: str, branch: str) -> str:
        """``git clone --depth 1`` into ``<clone_root>/<slug>`` (refreshed each run)."""
        dest = os.path.join(self.clone_root, fd.repo_slug(full))
        if os.path.isdir(dest):
            shutil.rmtree(dest, ignore_errors=True)
        os.makedirs(self.clone_root, exist_ok=True)
        url = f"{WEB}/{full}.git"
        if self.token and WEB.startswith("https://"):
            url = f"https://x-access-token:{self.token}@{WEB[len('https://') :]}/{full}.git"
        cmd = ["git", "clone", "--depth", "1", "--branch", branch, "--quiet", url, dest]
        res = self._runner(cmd, capture_output=True, text=True)
        if getattr(res, "returncode", 1) != 0:
            raise ConnectorError(
                0, _redact(getattr(res, "stderr", "")), _redact(url), detail="git clone failed"
            )
        return dest

    def file_records(
        self, full: str, clone_dir: str, branch: str, acl: list[str], version: str
    ) -> list[RawItem]:
        items: list[RawItem] = []
        for root, dirs, files in os.walk(clone_dir):
            dirs[:] = sorted(d for d in dirs if d not in SKIP_DIRS)
            for name in sorted(files):
                if len(items) >= self.max_files:
                    return items
                rel = os.path.relpath(os.path.join(root, name), clone_dir).replace(os.sep, "/")
                low = name.lower()
                is_code = low.endswith(CODE_EXT)
                is_doc = low.endswith(DOC_EXT)
                is_manifest = name in MANIFEST_NAMES or low.startswith("requirements")
                is_image = low.endswith(IMAGE_EXT)
                is_config = low.endswith(CONFIG_EXT)
                if not (is_code or is_doc or is_manifest or is_image or is_config):
                    continue
                full_path = os.path.join(root, name)
                try:
                    size = os.path.getsize(full_path)
                    if size > (MAX_IMAGE_BYTES if is_image else MAX_FILE_BYTES) or size == 0:
                        continue
                    with open(full_path, "rb") as f:
                        data = f.read()
                except OSError:
                    continue
                area = "Architecture" if (is_doc or rel.lower().startswith("docs/")) else "Code"
                if is_image:
                    mime = (
                        "image/svg+xml"
                        if low.endswith(".svg")
                        else f"image/{low.rsplit('.', 1)[-1].replace('jpg', 'jpeg')}"
                    )
                elif is_code:
                    mime = "text/x-code"
                elif is_doc:
                    mime = "text/markdown" if low.endswith(".md") else "text/plain"
                else:
                    mime = "text/plain"
                items.append(
                    RawItem(
                        tenant=self.tenant,
                        source=self.source_name,
                        source_version=version or "1",
                        uri=f"github://{full}/{rel}",
                        mime=mime,
                        title=rel,
                        bytes_=data,
                        meta={
                            "acl": list(acl),
                            "source_kind": "image"
                            if is_image
                            else ("code" if is_code else "document"),
                            "citation_url": f"{WEB}/{full}/blob/{branch}/{rel}",
                            "arrived_at": now_ms(),
                            "area": area,
                            "repo": full,
                            "provenance": {"repo": full, "path": rel, "branch": branch},
                        },
                    )
                )
        return items

    # -- the pull -------------------------------------------------------------
    def pull(self, cursor: str | None) -> tuple[list[RawItem], str | None]:
        self._require()
        try:
            cursors = json.loads(cursor) if cursor else {}
        except ValueError:
            cursors = {}
        if not isinstance(cursors, dict):
            cursors = {}
        items: list[RawItem] = []
        repos = self.list_repositories()
        if not repos:
            raise ConnectorConfigError(
                "github_live found no repositories: set GITHUB_ORG and/or GITHUB_EXTRA_REPOS"
            )
        for full in repos:
            owner, name = full.split("/", 1)
            meta = self._rest(f"/repos/{owner}/{name}")
            pushed = _iso(meta.get("pushed_at"))
            if cursors.get(full) == pushed and pushed:
                self.report.append({"repo": full, "status": "unchanged", "pushed_at": pushed})
                continue
            facts, raw = self.repo_facts(full)
            acl = facts["acl"]
            branch = facts["default_branch"]
            clone_dir = ""
            if self.clone_enabled:
                clone_dir = self.clone(full, branch)
                facts["clone_dir"] = clone_dir
                items += self.file_records(full, clone_dir, branch, acl, pushed)
            items += self.activity_records(full, raw, acl)
            if self.write_facts:
                write_repo_facts(full, facts)
            cursors[full] = pushed
            self.report.append(
                {
                    "repo": full,
                    "status": "pulled",
                    "commits": facts["commits"]["total"],
                    "prs": len(raw.get("prs") or []),
                    "files": len(
                        [
                            i
                            for i in items
                            if i.uri.startswith(f"github://{full}/") and i.mime != PASSAGES_MIME
                        ]
                    ),
                    "clone": clone_dir,
                }
            )
        self.last_tombstones = []
        return items, json.dumps(cursors, sort_keys=True)


def _login(node: dict) -> str:
    return ((node or {}).get("author") or {}).get("login") or "unknown"


def _committer(c: dict) -> str:
    author = (c or {}).get("author") or {}
    return (author.get("user") or {}).get("login") or author.get("name") or "unknown"


def _int(v):
    try:
        return int(v)
    except (TypeError, ValueError):
        return None


def write_repo_facts(full: str, facts: dict) -> str:
    p = fd.data_path("facts.json", mkdir=True)
    all_facts = fd.read_json(p, {}) or {}
    all_facts.setdefault("repositories", {})[full] = facts
    fd.write_json(p, all_facts)
    return p


_Q_REPO = """
query($owner:String!, $name:String!) {
  repository(owner:$owner, name:$name) {
    defaultBranchRef { name target { ... on Commit { history { totalCount } } } }
    open: pullRequests(states: OPEN) { totalCount }
    closed: pullRequests(states: CLOSED) { totalCount }
    merged: pullRequests(states: MERGED) { totalCount }
    openIssues: issues(states: OPEN) { totalCount }
    closedIssues: issues(states: CLOSED) { totalCount }
  }
}
"""
_Q_PRS = """
query($owner:String!, $name:String!, $cursor:String) {
  repository(owner:$owner, name:$name) {
    pullRequests(first: 100, after: $cursor, orderBy: {field: UPDATED_AT, direction: DESC}) {
      pageInfo { hasNextPage endCursor }
      nodes {
        number title body state merged createdAt updatedAt mergedAt closedAt
        additions deletions changedFiles baseRefName headRefName url
        author { login }
        labels(first: 20) { nodes { name } }
        reviews(first: 20) { nodes { author { login } state body submittedAt url } }
        comments(first: 30) { nodes { author { login } body createdAt url } }
        files(first: 50) { nodes { path } }
      }
    }
  }
}
"""
_Q_COMMITS = """
query($owner:String!, $name:String!, $branch:String!, $cursor:String) {
  repository(owner:$owner, name:$name) {
    ref(qualifiedName: $branch) {
      target { ... on Commit {
        history(first: 100, after: $cursor) {
          pageInfo { hasNextPage endCursor }
          nodes { oid messageHeadline message committedDate url additions deletions
                  author { name user { login } } }
        }
      } }
    }
  }
}
"""
_Q_ISSUES = """
query($owner:String!, $name:String!, $cursor:String) {
  repository(owner:$owner, name:$name) {
    issues(first: 100, after: $cursor, orderBy: {field: UPDATED_AT, direction: DESC}) {
      pageInfo { hasNextPage endCursor }
      nodes {
        number title body state createdAt updatedAt closedAt url
        author { login }
        labels(first: 20) { nodes { name } }
        comments(first: 30) { nodes { author { login } body createdAt url } }
      }
    }
  }
}
"""


def sync(platform, tenant: str, transport=None, runner=None) -> dict:
    """``scripts/ingest.py --github``: pull every repository in scope through
    the pipeline, write the facts and refresh the ``documents`` block;
    ``skipped`` with the reason when no token or no scope is configured."""
    from .. import facts as factsmod
    from ..ingestion.sync import SyncManager
    from . import admin

    cfg = admin.effective_config(platform, tenant, "github", {})
    token = cfg.get("token") or os.environ.get("KF_GITHUB_TOKEN") or os.environ.get("GITHUB_TOKEN")
    if not token:
        return {"status": "skipped", "reason": "GITHUB_TOKEN / KF_GITHUB_TOKEN not set"}
    scope = (
        cfg.get("org")
        or os.environ.get("GITHUB_ORG")
        or cfg.get("repos")
        or os.environ.get("GITHUB_EXTRA_REPOS")
    )
    if not scope:
        return {"status": "skipped", "reason": "GITHUB_ORG / GITHUB_EXTRA_REPOS not set"}
    kwargs = {}
    if transport is not None:
        kwargs["transport"] = transport
    if runner is not None:
        kwargs["runner"] = runner
    res = SyncManager(platform).sync(tenant, "github", cfg, **kwargs)
    factsmod.write_documents_facts(platform, tenant)
    res["status"] = "ran"
    return res
