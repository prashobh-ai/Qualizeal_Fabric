"""The ``fabric-data`` branch as a checkout (T46) — clone, commit, push.

The fabric's data (facts, answers, analysis, tables, images, the API ledger)
lives on the ``fabric-data`` branch, never in ``main``. Every scheduled and
event-driven workflow (ingest, ask, showcase) checks that branch out into a
directory, points ``KF_FABRIC_ROOT`` / ``KF_DATA_ROOT`` at it, and — when it
wrote something — commits and pushes with ``[skip ci]`` so a data commit never
re-triggers a build.

* ``ensure_checkout(root, branch)`` — clones the branch (shallow) when it
  exists on the remote, creates it as an ORPHAN when it does not, and fetches
  it when ``root`` is already a checkout.
* ``commit_and_push(root, message)`` — ``git add -A``, commit with
  ``[skip ci]``, push ``HEAD:<branch>``; a checkout with nothing to commit is
  a no-op.

Both take a ``runner`` (``subprocess.run``-shaped) so tests drive them with a
fake and no real git is ever touched. ``python -m knowledge_fabric.fabric_data_git
checkout|push`` is the CLI the workflows call.
"""

from __future__ import annotations

import argparse
import os
import re
import subprocess
import sys

DEFAULT_BRANCH = "fabric-data"
SKIP_CI = "[skip ci]"
_LAYOUT = (
    "data",
    "data/quality",
    "data/api_calls",
    "corpus/public",
    "analysis",
    "tables",
    "images",
)
_LAYOUT += ("answers",)

_README = """# fabric-data

The QualiZeal Knowledge Fabric's data branch — written by the `ingest`, `ask`
and `showcase` workflows, never by hand. Layout:

    data/{facts.json, capabilities.json, dependencies.json, provider_status.json,
          prices.json, quality/, api_calls/}
    corpus/public/**
    analysis/<repo>/{card.md, architecture.md, symbols.jsonl, comments.jsonl, callgraph.json}
    tables/<doc>/<sheet>.sqlite
    images/<doc>/<name>.json
    answers/<hash>.json

Every commit here carries `[skip ci]`.
"""


class GitError(RuntimeError):
    """A git command failed; carries the command and its stderr."""


def _redact(text: str) -> str:
    return re.sub(r"://[^/@\s]+@", "://***@", text or "")


def _git(runner, args: list[str], cwd: str | None = None, check: bool = True):
    """Run ``git <args>`` through ``runner``; raise ``GitError`` on failure."""
    kwargs = {"capture_output": True, "text": True}
    if cwd:
        kwargs["cwd"] = cwd
    proc = runner(["git", *args], **kwargs)
    rc = getattr(proc, "returncode", 0)
    if check and rc != 0:
        raise GitError(
            f"git {' '.join(_redact(a) for a in args)} failed ({rc}): "
            f"{_redact((getattr(proc, 'stderr', '') or '').strip())[:600]}"
        )
    return proc


def remote_url(runner=subprocess.run, repo: str | None = None, token: str | None = None) -> str:
    """The URL to clone/push the data branch: ``KF_FABRIC_REMOTE`` → the
    token-authenticated GitHub URL (``GITHUB_REPOSITORY`` + ``GITHUB_TOKEN``,
    the Actions shape) → this checkout's ``origin``."""
    env = os.environ
    if env.get("KF_FABRIC_REMOTE"):
        return env["KF_FABRIC_REMOTE"]
    repo = repo or env.get("GITHUB_REPOSITORY", "")
    token = token if token is not None else env.get("GITHUB_TOKEN", "")
    if repo:
        auth = f"x-access-token:{token}@" if token else ""
        return f"https://{auth}github.com/{repo}.git"
    proc = _git(runner, ["config", "--get", "remote.origin.url"], check=False)
    url = (getattr(proc, "stdout", "") or "").strip()
    if not url:
        raise GitError("no remote: set KF_FABRIC_REMOTE or GITHUB_REPOSITORY")
    return url


def _identity_args() -> list[str]:
    name = os.environ.get("GIT_AUTHOR_NAME") or "fabric-bot"
    email = os.environ.get("GIT_AUTHOR_EMAIL") or "fabric-bot@users.noreply.github.com"
    return ["-c", f"user.name={name}", "-c", f"user.email={email}"]


def branch_exists(url: str, branch: str, runner=subprocess.run) -> bool:
    proc = _git(runner, ["ls-remote", "--heads", url, branch], check=False)
    return bool((getattr(proc, "stdout", "") or "").strip())


def _scaffold(root: str) -> None:
    for rel in _LAYOUT:
        os.makedirs(os.path.join(root, rel), exist_ok=True)
        keep = os.path.join(root, rel, ".gitkeep")
        if not os.path.exists(keep):
            open(keep, "w").close()
    readme = os.path.join(root, "README.md")
    if not os.path.exists(readme):
        with open(readme, "w", encoding="utf-8") as f:
            f.write(_README)


def ensure_checkout(
    root: str,
    branch: str = DEFAULT_BRANCH,
    runner=subprocess.run,
    url: str | None = None,
    depth: int = 1,
) -> dict:
    """Make ``root`` a checkout of ``branch``. Returns ``{root, branch, mode}``
    with ``mode`` ∈ ``existing | cloned | orphan``."""
    root = os.path.abspath(root)
    url = url or remote_url(runner)
    if os.path.isdir(os.path.join(root, ".git")) or os.path.isfile(os.path.join(root, ".git")):
        _git(runner, ["fetch", "--depth", str(depth), "origin", branch], cwd=root, check=False)
        _git(runner, ["checkout", "-B", branch, f"origin/{branch}"], cwd=root, check=False)
        _scaffold(root)
        return {"root": root, "branch": branch, "mode": "existing"}
    os.makedirs(os.path.dirname(root) or ".", exist_ok=True)
    if branch_exists(url, branch, runner):
        _git(
            runner,
            ["clone", "--branch", branch, "--single-branch", "--depth", str(depth), url, root],
        )
        _scaffold(root)
        return {"root": root, "branch": branch, "mode": "cloned"}
    # no data branch yet: create it as an orphan (no history from main)
    os.makedirs(root, exist_ok=True)
    _git(runner, ["init", "-q", root])
    _git(runner, ["checkout", "-q", "--orphan", branch], cwd=root)
    _git(runner, ["remote", "add", "origin", url], cwd=root)
    _scaffold(root)
    return {"root": root, "branch": branch, "mode": "orphan"}


def commit_and_push(
    root: str,
    message: str,
    runner=subprocess.run,
    branch: str | None = None,
    push: bool = True,
) -> dict:
    """``git add -A`` → commit (``[skip ci]`` appended) → push ``HEAD:<branch>``.
    Returns ``{committed, pushed, sha, message}``; nothing to commit → all False."""
    root = os.path.abspath(root)
    _git(runner, ["add", "-A"], cwd=root)
    status = _git(runner, ["status", "--porcelain"], cwd=root)
    if not (getattr(status, "stdout", "") or "").strip():
        return {"committed": False, "pushed": False, "sha": "", "message": ""}
    message = (message or "fabric-data update").strip()
    if SKIP_CI not in message:
        message = f"{message} {SKIP_CI}"
    _git(runner, [*_identity_args(), "commit", "-q", "-m", message], cwd=root)
    sha = (getattr(_git(runner, ["rev-parse", "HEAD"], cwd=root), "stdout", "") or "").strip()
    if branch is None:
        proc = _git(runner, ["rev-parse", "--abbrev-ref", "HEAD"], cwd=root, check=False)
        branch = (getattr(proc, "stdout", "") or "").strip() or DEFAULT_BRANCH
    pushed = False
    if push:
        _git(runner, ["push", "-u", "origin", f"HEAD:{branch}"], cwd=root)
        pushed = True
    return {"committed": True, "pushed": pushed, "sha": sha, "message": message}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="fabric_data_git", description=__doc__.splitlines()[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    c = sub.add_parser("checkout", help="clone or create the data branch into --root")
    c.add_argument("--root", default="fabric-data")
    c.add_argument("--branch", default=DEFAULT_BRANCH)
    p = sub.add_parser("push", help="commit everything under --root with [skip ci] and push")
    p.add_argument("--root", default="fabric-data")
    p.add_argument("--branch", default=None)
    p.add_argument("-m", "--message", required=True)
    args = ap.parse_args(argv)
    try:
        if args.cmd == "checkout":
            res = ensure_checkout(args.root, args.branch)
            print(f"fabric-data: {res['mode']} checkout of {res['branch']} at {res['root']}")
        else:
            res = commit_and_push(args.root, args.message, branch=args.branch)
            if res["committed"]:
                print(f"fabric-data: committed {res['sha'][:10]} · pushed={res['pushed']}")
            else:
                print("fabric-data: nothing to commit")
    except GitError as e:
        print(f"fabric-data: {e}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
