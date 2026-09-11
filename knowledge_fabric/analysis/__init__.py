"""Code understanding (T38–T40) — the per-repository analysis layer.

Everything here is deterministic and stdlib-first: symbols, comments and the
call graph come from ``ast`` (Python) or brace/declaration scanners (the other
languages); dependencies from the manifests; capabilities from evidence lines.
The only model steps — the architecture summary (``summary.py``) and the
weak-evidence capability classification (``capabilities.py``) — go through
``platform.model.messages`` (ledgered, T36) and are SKIPPED deterministically
when no provider is configured; nothing is ever fabricated.

Outputs land in the ``fabric-data`` layout (``fabric_data.py``):

    analysis/<repo>/{symbols.jsonl, comments.jsonl, callgraph.json, card.md,
                     architecture.md}
    data/{dependencies.json, capabilities.json, licence_cache.json}

``analyse_repository`` runs the whole chain for one cloned repository; the
live GitHub connector (T37) calls it after ingesting a changed repository.
"""

from __future__ import annotations


def analyse_repository(
    platform,
    tenant: str,
    repo: str,
    clone_dir: str,
    facts: dict | None = None,
    *,
    branch: str = "main",
    acl: list[str] | None = None,
    transport=None,
) -> dict:
    """Symbols → comments → call graph → dependencies → capabilities → card →
    architecture summary for one repository. Returns the counts and the
    summary status (``{"model": "skipped (extractive)"}`` without a provider).
    Submodules are imported here so ``analysis.symbols`` stays importable from
    the converter without pulling the model adapter in."""
    from . import callgraph, capabilities, cards, comments, dependencies, summary, symbols

    facts = facts or {}
    syms = symbols.extract_repo(repo, clone_dir, branch=branch)
    coms = comments.extract_repo(repo, clone_dir, branch=branch, symbols=syms)
    graph = callgraph.build(syms)
    callgraph.write(repo, graph)
    deps = dependencies.run(repo, clone_dir, transport=transport)
    caps = capabilities.detect(repo, clone_dir, symbols=syms, facts=facts, platform=platform)
    card_md = cards.build(
        repo, facts, clone_dir, symbols=syms, dependencies=deps, capabilities=caps
    )
    cards.write(repo, card_md)
    if platform is not None:
        cards.ingest(platform, tenant, repo, card_md, acl=acl)
    summ = summary.summarise(
        platform,
        tenant,
        repo,
        clone_dir,
        facts,
        card_md,
        syms,
        graph,
        caps,
        acl=acl,
        branch=branch,
    )
    return {
        "symbols": len(syms),
        "comments": len(coms),
        "callgraph": {"nodes": len(graph["nodes"]), "edges": len(graph["edges"])},
        "dependencies": len(deps),
        "capabilities": sorted(c["capability"] for c in caps),
        "summary": summ,
    }


def run(platform, tenant: str, *, repos=None, force: bool = False, transport=None) -> dict:
    """``scripts/ingest.py --analysis``: analyse every repository in
    ``facts.json`` whose clone is present and whose ``pushed_at`` changed since
    the last analysis (``data/analysis_state.json``). Returns which were
    analysed, skipped (unchanged) and missing (no clone)."""
    import datetime as _dt
    import os

    from .. import fabric_data as fd
    from .. import facts as factsmod

    facts = factsmod.load_facts()
    state_path = fd.data_path("analysis_state.json")
    state = fd.read_json(state_path, {}) or {}
    analysed, skipped, missing, failed = [], [], [], []
    for repo, block in sorted((facts.get("repositories") or {}).items()):
        if repos and repo not in repos:
            continue
        block = block or {}
        clone_dir = block.get("clone_dir") or fd.path("clones", fd.repo_slug(repo))
        if not os.path.isdir(clone_dir):
            missing.append(repo)
            continue
        prev = state.get(repo) or {}
        if (
            not force
            and prev.get("pushed_at") == block.get("pushed_at")
            and prev.get("analysed_at")
        ):
            skipped.append(repo)
            continue
        try:
            res = analyse_repository(
                platform,
                tenant,
                repo,
                clone_dir,
                facts=block,
                branch=block.get("default_branch") or "main",
                acl=block.get("acl"),
                transport=transport,
            )
        except Exception as e:  # noqa: BLE001 — one repository must not hide the others
            failed.append({"repo": repo, "error": f"{type(e).__name__}: {str(e)[:200]}"})
            continue
        state[repo] = {
            "pushed_at": block.get("pushed_at"),
            "analysed_at": _dt.datetime.now(_dt.UTC).isoformat(timespec="seconds"),
            "result": res,
        }
        analysed.append(repo)
    fd.write_json(fd.data_path("analysis_state.json", mkdir=True), state)
    return {"analysed": analysed, "skipped": skipped, "missing": missing, "failed": failed}
