"""Capability detection (T40) — evidence lines per capability, deterministic.

``detect`` scans every text file of a clone for the evidence table in the
spec and writes rows to ``data/capabilities.json``::

    {repo, capability, confidence, evidence: [{path, line, snippet}], attributes}

Confidence grows with distinct evidence files (0.45 for one, up to 1.0);
``rag`` additionally needs a generation call, ``ml_model`` a training call —
without them the row is capped at 0.5. Weak evidence (0 < confidence < 0.6)
gets ONE ``capability_classify`` call on ``model_small`` when a provider is
configured (``platform.model.messages``); the response must be the JSON
``{present, confidence, reason}`` or it is ignored and the deterministic row
stands. Without a provider the step is skipped and says so — nothing is
fabricated.

``enterprise_readiness`` is the 15-point checklist with ``score`` 0–100 and
``checklist: [{item, present, evidence}]``.
"""

from __future__ import annotations

import json
import os
import re

from .. import fabric_data as fd

SKIP_DIRS = {
    ".git",
    "node_modules",
    "vendor",
    "dist",
    "build",
    "__pycache__",
    ".venv",
    "venv",
    "site-packages",
    ".mypy_cache",
    "fabric-data",
}
TEXT_EXT = {
    ".py", ".js", ".jsx", ".mjs", ".cjs", ".ts", ".tsx", ".java", ".go", ".cs", ".rb",
    ".sql", ".yaml", ".yml", ".toml", ".json", ".md", ".txt", ".rst", ".cfg", ".ini",
    ".html", ".css", ".scss", ".ipynb", ".sh", ".tf", ".xml", ".gradle", ".kts", ".csproj",
}  # fmt: skip
MAX_FILE_BYTES = 512_000
MAX_FILES = 4000
MAX_EVIDENCE = 8
WEAK = 0.6

# capability → (evidence regex, attribute hints)
_R = {
    "rag": re.compile(
        r"\b(faiss|chromadb|pgvector|pinecone|weaviate|qdrant|milvus|lancedb|embedding|"
        r"embeddings|retriev\w*|bm25|rank_bm25|rerank\w*|\brrf\b|\bmmr\b|hybrid search|"
        r"reciprocal rank)\b",
        re.I,
    ),
    "knowledge_graph": re.compile(
        r"\b(networkx|neo4j|rdflib|igraph|vis-network|three\.js|force-graph|forceSimulation|"
        r"knowledge[_ -]?graph|entities|triples|graph_edges|GraphEdge|GraphNode)\b",
        re.I,
    ),
    "knowledge_fabric": re.compile(r"knowledge[ _-]?fabric", re.I),
    "caching": re.compile(
        r"\b(lru_cache|cachetools|diskcache|redis|valkey|cache_control|prompt caching|"
        r"kv[ _-]?cache|paged attention|[a-z_]*cache[a-z_]*)\b",
        re.I,
    ),
    "auth": re.compile(
        r"\b(login|sign_in|signin|authenticate|verify_token|jwt|oauth2?|passlib|bcrypt|"
        r"flask_login|fastapi\.security|msal|saml|oidc)\b",
        re.I,
    ),
    "dashboard_ui": re.compile(
        r"(\.(dashboard|kpi|card|chart|tile|panel|grid)\b|\b(plotly|chart\.js|chartjs|d3|"
        r"recharts|echarts)\b|dashboard)",
        re.I,
    ),
    "api_service": re.compile(
        r"\b(fastapi|flask|django|express|gin-gonic|gin\.Default|spring(?:boot|framework)?|"
        r"HTTPServer|BaseHTTPRequestHandler|@app\.(get|post|route)|app\.(get|post|use)\()",
        re.I,
    ),
    "agent": re.compile(
        r"\b(tool_use|tool_result|tools=|function[_ ]calling|langgraph|\bmcp\b|MCPServer|"
        r"stop_reason|agent loop|ReAct|run_agent|toolrunner|tool_runner)\b",
        re.I,
    ),
    "data_pipeline": re.compile(
        r"\b(airflow|prefect|dagster|luigi|cron:|schedule:|\betl\b|pipeline\.run|DAG\()",
        re.I,
    ),
    "ml_model": re.compile(
        r"\b(torch|tensorflow|sklearn|scikit-learn|xgboost|lightgbm|transformers|keras)\b",
        re.I,
    ),
}
_GENERATION = re.compile(
    r"(messages\.create|chat\.completions|/v1/messages|anthropic|openai|generate\(|"
    r"\bllm\b|completion\()",
    re.I,
)
_TRAINING = re.compile(r"(\.fit\(|Trainer\(|optimizer\.step|model\.train\(|\.train\()")
_TECHNIQUES = (
    ("bm25", re.compile(r"bm25", re.I)),
    ("dense", re.compile(r"\b(dense|embedding|embeddings|vector search|cosine)\b", re.I)),
    ("hybrid", re.compile(r"\bhybrid\b", re.I)),
    ("rrf", re.compile(r"\b(rrf|reciprocal rank)\b", re.I)),
    ("mmr", re.compile(r"\bmmr\b|maximal marginal", re.I)),
    ("rerank", re.compile(r"rerank", re.I)),
    ("graph_expansion", re.compile(r"graph[ _-]?(expansion|expand|walk|neighbou?r)", re.I)),
    ("multi_query", re.compile(r"multi[ _-]?query", re.I)),
    ("hyde", re.compile(r"\bhyde\b|hypothetical document", re.I)),
    ("parent_child", re.compile(r"parent[ _-]?child|parent document", re.I)),
)
_VECTOR_STORES = (
    "faiss",
    "chromadb",
    "pgvector",
    "pinecone",
    "weaviate",
    "qdrant",
    "milvus",
    "lancedb",
)
_EMBED_MODEL = re.compile(
    r"(text-embedding-[\w.-]+|bge-[\w.-]+|all-MiniLM[\w.-]*|e5-[\w.-]+|nomic-embed[\w.-]*|"
    r"voyage-[\w.-]+)",
    re.I,
)
_CHART_LIBS = ("plotly", "chart.js", "chartjs", "d3", "recharts", "echarts")
_FRAMEWORKS = ("fastapi", "flask", "django", "express", "gin", "spring")
_CACHE_KINDS = (
    ("answer", re.compile(r"answer[_ ]?cache|response[_ ]?cache", re.I)),
    ("embedding", re.compile(r"embed\w*[_ ]?cache", re.I)),
    ("retrieval", re.compile(r"retriev\w*[_ ]?cache|search[_ ]?cache", re.I)),
    ("prompt_kv", re.compile(r"prompt[_ ]?cach|cache_control|kv[_ ]?cache|paged attention", re.I)),
    ("http", re.compile(r"http[_ ]?cache|etag|cache-control|max-age", re.I)),
)
_KG_STORES = ("networkx", "neo4j", "rdflib", "igraph", "sqlite")
_KG_VIS = ("vis-network", "three.js", "force-graph", "forceSimulation", "d3")
_AGENT_TOOLS = re.compile(r"\"name\"\s*:\s*\"([a-z_][\w]*)\"|@(?:beta_)?tool\b.*?def\s+(\w+)", re.I)


def iter_text_files(clone_dir: str):
    """``(rel_path, text)`` for every text file under the clone (bounded)."""
    n = 0
    for root, dirs, files in os.walk(clone_dir):
        dirs[:] = sorted(d for d in dirs if d not in SKIP_DIRS)
        for name in sorted(files):
            full = os.path.join(root, name)
            ext = os.path.splitext(name)[1].lower()
            if ext not in TEXT_EXT and name not in (
                "Dockerfile",
                "Makefile",
                "Procfile",
                "LICENSE",
                "LICENCE",
            ):
                continue
            try:
                if os.path.getsize(full) > MAX_FILE_BYTES:
                    continue
                with open(full, encoding="utf-8", errors="replace") as f:
                    text = f.read()
            except OSError:
                continue
            n += 1
            yield os.path.relpath(full, clone_dir).replace(os.sep, "/"), text
            if n >= MAX_FILES:
                return


def _snippet(line: str) -> str:
    return line.strip()[:160]


def _confidence(evidence: list[dict]) -> float:
    files = {e["path"] for e in evidence}
    if not files:
        return 0.0
    return round(min(1.0, 0.3 + 0.15 * len(files)), 2)


def scan(clone_dir: str) -> tuple[dict[str, list[dict]], dict]:
    """Evidence per capability plus the raw signals the attribute builders need."""
    ev: dict[str, list[dict]] = {k: [] for k in _R}
    signals: dict = {
        "generation": False,
        "training": False,
        "techniques": set(),
        "vector_store": set(),
        "embedding_model": set(),
        "chunking": False,
        "chart_library": set(),
        "framework": set(),
        "cache_kinds": set(),
        "kg_store": set(),
        "kg_vis": set(),
        "agent_tools": set(),
        "css_files": set(),
        "readme_kf": False,
        "files": [],
    }
    for rel, text in iter_text_files(clone_dir):
        signals["files"].append(rel)
        low = text.lower()
        if _GENERATION.search(text):
            signals["generation"] = True
        if _TRAINING.search(text):
            signals["training"] = True
        for name, rx in _TECHNIQUES:
            if rx.search(text):
                signals["techniques"].add(name)
        for vs in _VECTOR_STORES:
            if vs in low:
                signals["vector_store"].add(vs)
        for m in _EMBED_MODEL.finditer(text):
            signals["embedding_model"].add(m.group(1))
        if re.search(r"chunk_size|chunker|chunking|\bchunk\(", text, re.I):
            signals["chunking"] = True
        for lib in _CHART_LIBS:
            if re.search(r"(?<![a-z])" + re.escape(lib) + r"(?![a-z])", low):
                signals["chart_library"].add(lib)
        for fw in _FRAMEWORKS:
            if re.search(r"\b" + re.escape(fw) + r"\b", low):
                signals["framework"].add(fw)
        for kind, rx in _CACHE_KINDS:
            if rx.search(text):
                signals["cache_kinds"].add(kind)
        for st in _KG_STORES:
            if st in low and ("graph" in low or "triple" in low or "entit" in low):
                signals["kg_store"].add(st)
        for vis in _KG_VIS:
            if vis.lower() in low and "graph" in low:
                signals["kg_vis"].add(vis)
        if rel.lower().endswith((".css", ".scss")) and re.search(
            r"\.(dashboard|kpi|card|chart|tile|panel|grid)\b", text
        ):
            signals["css_files"].add(rel)
        if rel.lower() in ("readme.md", "readme.rst", "readme.txt") and "knowledge fabric" in low:
            signals["readme_kf"] = True
        for i, line in enumerate(text.splitlines(), 1):
            for cap, rx in _R.items():
                if len(ev[cap]) >= MAX_EVIDENCE * 3:
                    continue
                if rx.search(line):
                    ev[cap].append({"path": rel, "line": i, "snippet": _snippet(line)})
            if cap_agent := ("agent" if "tool" in line.lower() else None):
                for m in _AGENT_TOOLS.finditer(line):
                    name = m.group(1) or m.group(2)
                    if name and len(signals["agent_tools"]) < 30:
                        signals["agent_tools"].add(name)
                del cap_agent
    return ev, signals


def _distinct(evidence: list[dict], limit: int = MAX_EVIDENCE) -> list[dict]:
    """At most one evidence line per file, then more lines, up to ``limit``."""
    seen, first, rest = set(), [], []
    for e in evidence:
        if e["path"] in seen:
            rest.append(e)
        else:
            seen.add(e["path"])
            first.append(e)
    return (first + rest)[:limit]


def _readiness(clone_dir: str, facts: dict, signals: dict, symbols: list[dict]) -> dict:
    files = set(signals["files"])
    low_files = {f.lower() for f in files}

    def has(*names):
        for n in names:
            if os.path.exists(os.path.join(clone_dir, n)):
                return n
        return ""

    def any_file(rx):
        r = re.compile(rx, re.I)
        for f in sorted(files):
            if r.search(f):
                return f
        return ""

    tests = [f for f in files if re.search(r"(^|/)(tests?|__tests__|spec)/", f)]
    n_tests = len(
        [f for f in files if re.search(r"(test_.*\.py|_test\.(py|go)|\.(test|spec)\.[jt]sx?)$", f)]
    )
    envs = ((facts or {}).get("deployments") or {}).get("environments") or []
    checks = [
        ("Dockerfile", has("Dockerfile")),
        ("compose", has("docker-compose.yml", "compose.yaml", "compose.yml")),
        ("k8s or terraform", any_file(r"(\.tf$|(^|/)(k8s|kubernetes|helm)/|Chart\.yaml$)")),
        ("CI", any_file(r"^\.github/workflows/.*\.ya?ml$|^\.gitlab-ci\.yml$|^Jenkinsfile$")),
        ("tests directory", tests[0] if tests else ""),
        ("tests ≥ 20", f"{n_tests} test files" if n_tests >= 20 else ""),
        (
            "auth",
            (signals.get("auth_evidence") or [{}])[0].get("path", "")
            if signals.get("auth_evidence")
            else "",
        ),
        ("logging or telemetry", any_file(r"(telemetry|logging|otel|opentelemetry|metrics)")),
        (
            "env config",
            has(".env.example", ".env.sample", "config.yaml", "settings.py")
            or any_file(r"(^|/)config/"),
        ),
        ("migrations", any_file(r"(^|/)(migrations|alembic|db/migrate)/")),
        ("rate limiting", (signals.get("rate_limit_file") or "")),
        ("load test", any_file(r"(load_?test|locust|k6|gatling)")),
        ("setup README", "README.md" if "readme.md" in low_files else has("README.rst", "README")),
        ("licence file", has("LICENSE", "LICENSE.md", "LICENCE", "LICENSE.txt")),
        (
            "releases",
            f"{len((facts or {}).get('releases') or [])} releases"
            if (facts or {}).get("releases")
            else "",
        ),
        ("multi-environment deployments", ", ".join(envs) if len(envs) >= 2 else ""),
    ]
    checklist = [{"item": i, "present": bool(e), "evidence": e} for i, e in checks]
    present = [c for c in checklist if c["present"]]
    score = round(100 * len(present) / len(checklist))
    return {
        "repo": "",
        "capability": "enterprise_readiness",
        "confidence": 1.0,
        "evidence": [
            {"path": c["evidence"], "line": 1, "snippet": c["item"]}
            for c in present
            if "/" in c["evidence"] or "." in c["evidence"] or c["evidence"].isupper()
        ][:MAX_EVIDENCE],
        "attributes": {"score": score, "checklist": checklist},
    }


def _classify(platform, repo: str, cap: str, evidence: list[dict]) -> dict | None:
    """One ``capability_classify`` call for weak evidence; ``None`` when no
    provider is configured or the reply is not the expected JSON."""
    model_client = getattr(platform, "model", None) if platform is not None else None
    if model_client is None or not hasattr(model_client, "messages"):
        return None
    from ..adapters.model import resolve_models

    small, _large = resolve_models()
    snippets = "\n".join(
        f"{e['path']}:{e['line']}: {e['snippet']}" for e in evidence[:MAX_EVIDENCE]
    )
    body = {
        "model": small,
        "max_tokens": 200,
        "system": (
            "You classify whether a code repository implements a capability from evidence "
            'lines. Reply with ONLY a JSON object {"present": bool, "confidence": number '
            '0-1, "reason": string}. Do not invent evidence.'
        ),
        "messages": [
            {
                "role": "user",
                "content": f"Repository {repo}. Capability: {cap}.\nEvidence:\n{snippets}",
            }
        ],
    }
    try:
        data = model_client.messages(body, purpose="capability_classify", repo=repo)
    except Exception as e:  # noqa: BLE001 — a provider failure must surface, not fabricate
        return {"error": f"{type(e).__name__}: {str(e)[:120]}"}
    text = "".join(b.get("text", "") for b in data.get("content", []) if b.get("type") == "text")
    m = re.search(r"\{.*\}", text, re.S)
    if not m:
        return {"error": "no JSON in reply"}
    try:
        obj = json.loads(m.group(0))
        return {
            "present": bool(obj["present"]),
            "confidence": max(0.0, min(1.0, float(obj["confidence"]))),
            "reason": str(obj.get("reason", ""))[:200],
            "model": data.get("model", small),
        }
    except (ValueError, KeyError, TypeError):
        return {"error": "invalid JSON shape"}


def detect(
    repo: str,
    clone_dir: str,
    *,
    symbols: list[dict] | None = None,
    facts: dict | None = None,
    platform=None,
    write: bool = True,
) -> list[dict]:
    """Capability rows for one repository (written to ``data/capabilities.json``)."""
    symbols = symbols or []
    ev, signals = scan(clone_dir)
    signals["auth_evidence"] = ev["auth"]
    for rel, text in iter_text_files(clone_dir):
        if re.search(r"rate[_ ]?limit", text, re.I):
            signals["rate_limit_file"] = rel
            break
    rows: list[dict] = []
    sym_names = [
        (s.get("symbol") or "", s.get("path") or "", s.get("start_line") or 1) for s in symbols
    ]
    # reusable caching symbols: named *cache* OR wrapped in a cache decorator
    cache_syms = [
        (s.get("symbol") or "", s.get("path") or "", s.get("start_line") or 1)
        for s in symbols
        if "cache" in (s.get("symbol") or "").lower()
        or any("cache" in str(d).lower() for d in (s.get("decorators") or []))
    ]
    for cap, evidence in ev.items():
        if not evidence:
            continue
        conf = _confidence(evidence)
        attrs: dict = {}
        if cap == "rag":
            if not signals["generation"]:
                conf = min(conf, 0.5)
            attrs = {
                "retrieval_techniques": sorted(signals["techniques"]),
                "vector_store": sorted(signals["vector_store"]),
                "embedding_model": sorted(signals["embedding_model"]),
                "chunking": signals["chunking"],
            }
        elif cap == "knowledge_graph":
            attrs = {"store": sorted(signals["kg_store"]), "visualised": bool(signals["kg_vis"])}
        elif cap == "knowledge_fabric":
            if not (signals["readme_kf"] or "knowledge" in repo.lower()):
                conf = min(conf, 0.5)
            attrs = {"links": [c for c in ("rag", "knowledge_graph") if ev.get(c)]}
        elif cap == "caching":
            attrs = {
                "kinds": sorted(signals["cache_kinds"]),
                "reusable": [{"symbol": n, "path": p, "line": ln} for n, p, ln in cache_syms][:10],
            }
        elif cap == "auth":
            attrs = {
                "login_symbols": [
                    n
                    for n, _p, _l in sym_names
                    if re.search(r"login|sign_?in|authenticate|verify_token", n, re.I)
                ][:20]
            }
        elif cap == "dashboard_ui":
            attrs = {
                "css_files": sorted(signals["css_files"]),
                "chart_library": sorted(signals["chart_library"]),
            }
            if not signals["css_files"] and not signals["chart_library"]:
                conf = min(conf, 0.5)
        elif cap == "api_service":
            attrs = {"framework": sorted(signals["framework"])}
        elif cap == "agent":
            attrs = {"tools": sorted(signals["agent_tools"])}
        elif cap == "ml_model":
            if not signals["training"]:
                conf = min(conf, 0.5)
        row = {
            "repo": repo,
            "capability": cap,
            "confidence": conf,
            "evidence": _distinct(evidence),
            "attributes": attrs,
        }
        if 0 < conf < WEAK:
            verdict = _classify(platform, repo, cap, row["evidence"])
            if verdict is None:
                row["attributes"]["classified"] = "skipped (no provider)"
            elif "error" in verdict:
                row["attributes"]["classified"] = f"failed: {verdict['error']}"
            else:
                row["attributes"]["classified"] = verdict
                row["confidence"] = (
                    round(max(conf, verdict["confidence"]), 2)
                    if verdict["present"]
                    else round(min(conf, 0.3), 2)
                )
        rows.append(row)
    ready = _readiness(clone_dir, facts or {}, signals, symbols)
    ready["repo"] = repo
    rows.append(ready)
    if write:
        p = fd.data_path("capabilities.json", mkdir=True)
        existing = [
            c for c in (fd.read_json(p, []) or []) if isinstance(c, dict) and c.get("repo") != repo
        ]
        fd.write_json(p, existing + rows)
    return rows
