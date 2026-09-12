"""T124a — preflight: prove each source's credential against one live call.

Run before ingest (in ``ingest.yml`` / ``showcase.yml``). For every configured
source in ``data/showcase_sources.json`` this makes ONE cheap authenticated
call and records a single result line to ``$GITHUB_STEP_SUMMARY`` and
``data/preflight.json``:

    github_qualizeal: OK · 12 repos visible
    jira: FAIL · 401 (check JIRA_EMAIL + JIRA_TOKEN pairing)
    confluence: FAIL · 404 (page 1703938 not visible to this user)

Failure kinds are distinguished because they need different fixes:
  401 = wrong email/token pairing or token typo
  403 = token works but this user can't see that dashboard/board/page
  404 = wrong id or site
  network = URL wrong / unreachable

The script **exits non-zero** if any source whose required secrets are all set
fails its check — so the workflow stops and the log names the wrong credential
before an ingest is spent. A source with a required secret missing is skipped
(not failed); a source with no required secret (the website) always runs.

The HTTP layer is injectable (``fetch(method, url, headers) -> (status, bytes)``)
so the checks are unit-tested offline.
"""

from __future__ import annotations

import base64
import json
import os
import sys
import urllib.error
import urllib.request

ROOT = os.path.abspath(os.path.dirname(os.path.dirname(__file__)))
sys.path.insert(0, ROOT)

SOURCES_JSON = os.path.join(ROOT, "data", "showcase_sources.json")


# --------------------------------------------------------------------------
# HTTP
# --------------------------------------------------------------------------
def http_fetch(method: str, url: str, headers: dict, timeout: int = 20):
    """``(status, body_bytes)``; status 0 on a network/DNS error."""
    req = urllib.request.Request(url, method=method, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:  # noqa: S310 — configured hosts
            return int(r.status), r.read(), dict(r.headers.items())
    except urllib.error.HTTPError as e:
        return int(e.code), e.read(), dict(e.headers.items())
    except (urllib.error.URLError, TimeoutError, OSError):
        return 0, b"", {}


def _basic(email: str, token: str) -> str:
    return "Basic " + base64.b64encode(f"{email}:{token}".encode()).decode("ascii")


def _kind(status: int) -> str:
    if status == 0:
        return "network"
    if status == 401:
        return "401"
    if status == 403:
        return "403"
    if status == 404:
        return "404"
    return str(status)


_HINTS = {
    "jira": {
        "401": "check JIRA_EMAIL + JIRA_TOKEN pairing",
        "403": "token valid, dashboard/board not shared with this user",
        "404": "wrong id or JIRA_URL site",
        "network": "JIRA_URL unreachable",
    },
    "confluence": {
        "401": "check CONFLUENCE_EMAIL + CONFLUENCE_TOKEN pairing",
        "403": "token valid, page not visible to this user",
        "404": "page not found — is CONFLUENCE_URL the wiki site (…/wiki)?",
        "network": "CONFLUENCE_URL unreachable",
    },
    "github": {
        "401": "KF_GITHUB_TOKEN invalid or expired",
        "403": "token exists but the org has not approved it (org admin needed)",
        "404": "org not found or not visible to this token",
        "network": "api.github.com unreachable",
    },
    "website": {"network": "site unreachable"},
}


def _hint(kind: str, source_kind: str) -> str:
    return _HINTS.get(source_kind, {}).get(kind, "")


# --------------------------------------------------------------------------
# per-source checks — each returns a result dict
# --------------------------------------------------------------------------
def _result(key, status, ok, detail, *, warn=False, skipped=False):
    return {
        "key": key,
        "status": status,
        "ok": ok,
        "warn": warn,
        "skipped": skipped,
        "detail": detail,
    }


def _missing_secrets(source: dict, env: dict) -> list[str]:
    return [s for s in source.get("required_secrets", []) if not env.get(s)]


def check_website(source: dict, env: dict, fetch) -> dict:
    url = source["url"].rstrip("/") + "/sitemap.xml"
    status, _body, _h = fetch("GET", url, {"User-Agent": "KF-preflight/1.0"})
    if status == 200:
        return _result(source["key"], 200, True, "sitemap reachable")
    if status == 0:
        return _result(source["key"], 0, False, "network · " + _hint("network", "website"))
    # a 404 sitemap is not fatal — the homepage may still crawl; WARN.
    return _result(
        source["key"],
        status,
        True,
        f"{status} on sitemap.xml (homepage still crawlable)",
        warn=True,
    )


def check_github(source: dict, env: dict, fetch) -> dict:
    org = source["org"]
    token = env.get("KF_GITHUB_TOKEN") or env.get("GITHUB_TOKEN") or ""
    headers = {
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
        "User-Agent": "KF-preflight/1.0",
        "Authorization": f"Bearer {token}",
    }
    status, body, hdrs = fetch(
        "GET", f"https://api.github.com/orgs/{org}/repos?per_page=1", headers
    )
    if status == 200:
        try:
            rows = json.loads(body or b"[]")
        except ValueError:
            rows = []
        if not rows:
            return _result(
                source["key"],
                200,
                True,
                f"0 repos visible for org {org} (org may need to approve the token)",
                warn=True,
            )
        count = _repo_count(hdrs) or len(rows)
        return _result(source["key"], 200, True, f"{count} repos visible")
    kind = _kind(status)
    return _result(source["key"], status, False, f"{kind} ({_hint(kind, 'github')})")


def _repo_count(headers: dict) -> int | None:
    """With per_page=1, the Link ``rel="last"`` page number equals the repo count."""
    link = ""
    for k, v in (headers or {}).items():
        if str(k).lower() == "link":
            link = str(v)
            break
    if 'rel="last"' not in link:
        return None
    import re

    for part in link.split(","):
        if 'rel="last"' in part and "page=" in part:
            m = re.search(r"[?&]page=(\d+)", part)
            if m:
                return int(m.group(1))
    return None


def check_jira(source: dict, env: dict, fetch) -> dict:
    base = (env.get("JIRA_URL") or "").rstrip("/")
    auth = _basic(env.get("JIRA_EMAIL", ""), env.get("JIRA_TOKEN", ""))
    headers = {
        "Authorization": auth,
        "Accept": "application/json",
        "User-Agent": "KF-preflight/1.0",
    }
    # 1) auth works?
    status, _b, _h = fetch("GET", f"{base}/rest/api/3/myself", headers)
    if status != 200:
        kind = _kind(status)
        return _result(source["key"], status, False, f"{kind} on /myself ({_hint(kind, 'jira')})")
    # 2) each configured dashboard + board visible?
    for did in source.get("dashboards", []):
        s2, _b2, _h2 = fetch("GET", f"{base}/rest/api/3/dashboard/{did}", headers)
        if s2 != 200:
            kind = _kind(s2)
            return _result(
                source["key"],
                s2,
                False,
                f"auth OK but dashboard {did}: {kind} ({_hint(kind, 'jira')})",
            )
    for bid in source.get("boards", []):
        s3, _b3, _h3 = fetch("GET", f"{base}/rest/agile/1.0/board/{bid}", headers)
        if s3 != 200:
            kind = _kind(s3)
            return _result(
                source["key"], s3, False, f"auth OK but board {bid}: {kind} ({_hint(kind, 'jira')})"
            )
    who = source.get("dashboards", []) + source.get("boards", [])
    return _result(
        source["key"], 200, True, f"auth OK · dashboard/board visible ({', '.join(who)})"
    )


def check_confluence(source: dict, env: dict, fetch) -> dict:
    base = (env.get("CONFLUENCE_URL") or "").rstrip("/")
    auth = _basic(env.get("CONFLUENCE_EMAIL", ""), env.get("CONFLUENCE_TOKEN", ""))
    headers = {
        "Authorization": auth,
        "Accept": "application/json",
        "User-Agent": "KF-preflight/1.0",
    }
    for pid in source.get("pages", []):
        url = f"{base}/api/v2/pages/{pid}?body-format=storage"
        status, body, _h = fetch("GET", url, headers)
        if status != 200:
            kind = _kind(status)
            return _result(
                source["key"], status, False, f"page {pid}: {kind} ({_hint(kind, 'confluence')})"
            )
        if not body:
            return _result(source["key"], 200, False, f"page {pid}: 200 but empty body")
    return _result(
        source["key"], 200, True, f"page(s) {', '.join(source.get('pages', []))} readable"
    )


_CHECKS = {
    "website": check_website,
    "github": check_github,
    "jira": check_jira,
    "confluence": check_confluence,
}


# --------------------------------------------------------------------------
# orchestration
# --------------------------------------------------------------------------
def run_preflight(sources: list[dict], env: dict, fetch) -> dict:
    """Check each source; return ``{results, reachable, total, failed}``."""
    results = []
    for src in sources:
        missing = _missing_secrets(src, env)
        if missing:
            results.append(
                _result(
                    src["key"],
                    None,
                    True,
                    "skipped · secret not set (" + ", ".join(missing) + ")",
                    skipped=True,
                )
            )
            continue
        check = _CHECKS.get(src["kind"])
        if check is None:
            results.append(_result(src["key"], None, False, f"no check for kind {src['kind']!r}"))
            continue
        results.append(check(src, env, fetch))
    active = [r for r in results if not r["skipped"]]
    reachable = sum(1 for r in active if r["ok"])
    failed = [r for r in active if not r["ok"]]
    return {"results": results, "reachable": reachable, "total": len(active), "failed": failed}


def _line(r: dict) -> str:
    if r["skipped"]:
        return f"{r['key']}: SKIP · {r['detail']}"
    tag = "WARN" if r.get("warn") else ("OK" if r["ok"] else "FAIL")
    return f"{r['key']}: {tag} · {r['detail']}"


def main(argv=None) -> int:
    with open(SOURCES_JSON, encoding="utf-8") as f:
        sources = json.load(f)["sources"]
    report = run_preflight(sources, dict(os.environ), http_fetch)

    lines = [_line(r) for r in report["results"]]
    summary = (
        "### Source preflight (T124)\n\n```\n"
        + "\n".join(lines)
        + f"\n```\n\nPreflight: {report['reachable']}/{report['total']} sources reachable\n"
    )
    for ln in lines:
        print(ln)
    print(f"Preflight: {report['reachable']}/{report['total']} sources reachable")

    step_summary = os.environ.get("GITHUB_STEP_SUMMARY")
    if step_summary:
        try:
            with open(step_summary, "a", encoding="utf-8") as f:
                f.write(summary + "\n")
        except OSError:
            pass
    try:
        os.makedirs(os.path.join(ROOT, "data"), exist_ok=True)
        with open(os.path.join(ROOT, "data", "preflight.json"), "w", encoding="utf-8") as f:
            json.dump(report, f, indent=2)
    except OSError:
        pass

    if report["failed"]:
        print(f"\n{len(report['failed'])} source(s) with a set secret FAILED — stopping the build.")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
