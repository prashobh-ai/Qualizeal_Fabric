"""The 7-step ingestion pipeline (Build Plan Section 7).

detect · convert · chunk(+provenance,+abstract/overview) · extract&type ·
graph-fusion · embed&index · health-snapshot.

Every step emits a span on ONE ingest-job trace (I11). Re-ingesting the same
content is a no-op (I9, idempotent-by-hash). A changed source item supersedes
its old passages (incremental); a deleted item tombstones them.
"""

from __future__ import annotations

import hashlib
import re
from itertools import combinations

from ..adapters.converter import source_kind_for
from ..contracts.types import (
    Document,
    GraphEdge,
    GraphNode,
    Passage,
    Provenance,
    RawItem,
    new_id,
    now_ms,
)
from ..ontology.packs import get_pack
from ..stores import versioning
from .extract import extract


def _sentences(text: str) -> list[str]:
    return [s.strip() for s in re.split(r"(?<=[.!?])\s+", text.strip()) if s.strip()]


def _abstract(text: str) -> str:
    s = _sentences(text)
    return s[0] if s else text[:120]


def _overview(text: str) -> str:
    s = _sentences(text)
    return " ".join(s[:2]) if s else text[:280]


SOURCE_KINDS = ("document", "table", "image", "jira", "confluence", "code", "analysis")


def stamp_meta(raw: RawItem) -> dict:
    """T41 — every document carries ``source_kind``, ``citation_url``, ``acl`` and
    ``arrived_at`` in its persisted meta. Connectors set them explicitly; the
    intake doors get deterministic defaults (kind from mime/uri, citation =
    the uri, arrival = now). Transient keys (``tombstones``) are not stored."""
    m = raw.meta
    m.setdefault("acl", ["public"])
    m.setdefault("arrived_at", now_ms())
    kind = m.get("source_kind") or source_kind_for(raw.mime, raw.uri)
    m["source_kind"] = kind if kind in SOURCE_KINDS else "document"
    m.setdefault("citation_url", raw.uri)
    return {k: v for k, v in m.items() if k != "tombstones"}


class IngestionPipeline:
    def __init__(self, platform, ontology_name: str = "quality-assurance"):
        self.p = platform
        self.ontology_name = ontology_name

    def _live_version(self, tenant: str) -> int:
        r = self.p.db.one(
            "SELECT MAX(version) v FROM index_versions WHERE tenant=? AND state='live'", (tenant,)
        )
        if r and r["v"]:
            return r["v"]
        self.p.db.execute(
            (
                "INSERT OR IGNORE INTO index_versions(tenant,version,state,promoted_at) "
                "VALUES(?,?,?,?)"
            ),
            (tenant, 1, "live", now_ms()),
        )
        return 1

    def run(self, raw: RawItem, ontology_name: str | None = None) -> dict:
        pack = get_pack(ontology_name or self.ontology_name)
        tenant = raw.tenant
        trace_id = new_id("ingest_")
        result = {"trace_id": trace_id, "tenant": tenant, "uri": raw.uri}

        with self.p.telemetry.span(
            "ingest", {"tenant": tenant, "trace_id": trace_id, "stage": "ingest"}
        ) as root:
            # ---- Step 1: Detect --------------------------------------
            content_hash = hashlib.sha256(raw.bytes_).hexdigest()
            with self.p.telemetry.span(
                "ingest.detect", {"tenant": tenant, "trace_id": trace_id, "stage": "detect"}
            ):
                existing = self.p.documents.by_source_uri(tenant, raw.source, raw.uri)
                if existing and existing["content_hash"] == content_hash:
                    result.update(
                        status="noop", reason="idempotent-by-hash", document_id=existing["id"]
                    )
                    root.set(status="noop")
                    return result
                is_update = existing is not None
                version = (existing["current_version"] + 1) if is_update else 1

            # ---- Step 2: Convert + store original --------------------
            with self.p.telemetry.span(
                "ingest.convert", {"tenant": tenant, "trace_id": trace_id, "stage": "convert"}
            ):
                self.p.objects.put(
                    tenant, content_hash, raw.bytes_, {"mime": raw.mime, "uri": raw.uri}
                )
                # The document id is fixed BEFORE conversion so converters that
                # persist side artefacts (tables/<doc>/<sheet>.sqlite,
                # images/<doc>/<name>.json) file them under the real id.
                doc_id = existing["id"] if is_update else new_id("doc_")
                raw.meta["doc_id"] = doc_id
                converted = self.p.converter.convert(raw)

            meta = stamp_meta(raw)
            acl = meta["acl"]
            doc = Document(
                id=doc_id,
                tenant=tenant,
                source=raw.source,
                source_version=raw.source_version,
                content_hash=content_hash,
                type=raw.mime,
                language=converted.language,
                title=raw.title,
                uri=raw.uri,
                ingested_at=now_ms(),
                status="active",
                current_version=version,
                acl=acl,
                meta=meta,
            )

            if is_update:  # incremental: supersede prior passages (lineage kept)
                self.p.passages.supersede_document(tenant, doc_id, version)
            self.p.documents.upsert(doc)
            live_v = self._live_version(tenant)

            # ---- Step 3: Chunk with provenance + abstract/overview ---
            passages: list[Passage] = []
            with self.p.telemetry.span(
                "ingest.chunk", {"tenant": tenant, "trace_id": trace_id, "stage": "chunk"}
            ):
                for region in converted.regions:
                    # every passage resolves to a place AND a URL (I2 + T41)
                    region.coordinate.locator.setdefault("citation_url", meta["citation_url"])
                    region.coordinate.locator.setdefault("source_kind", meta["source_kind"])
                    prov = Provenance(
                        content_hash, raw.source, raw.source_version, region.coordinate
                    )
                    pas = Passage(
                        id=new_id("pas_"),
                        tenant=tenant,
                        document_id=doc_id,
                        text=region.text,
                        abstract=_abstract(region.text),
                        overview=_overview(region.text),
                        coordinate=region.coordinate,
                        provenance=prov,
                        version=version,
                    )
                    passages.append(pas)
                    self.p.passages.add(pas, live_v, acl)
                # data versioning: immutable ledger row for this document version (I8)
                versioning.record_version(
                    self.p,
                    tenant,
                    doc_id,
                    version,
                    content_hash,
                    [p_.id for p_ in passages],
                    raw.source_version,
                )

            # ---- Step 4: Extract & type (curator queue on low conf) --
            all_mentions, all_relations, passage_mentions = [], [], []
            with self.p.telemetry.span(
                "ingest.extract", {"tenant": tenant, "trace_id": trace_id, "stage": "extract"}
            ):
                for pas in passages:
                    mentions, relations = extract(pas.text, pack)
                    passage_mentions.append((pas, mentions))
                    all_mentions.extend(mentions)
                    all_relations.extend(relations)
                    for m in mentions:
                        if m.confidence < 0.55:
                            self.p.curation.add(
                                tenant,
                                f"low-confidence entity '{m.text}' in {pas.id}",
                                "low-confidence",
                                now_ms(),
                            )

            # ---- Step 5: Graph fusion (resolve, merge, flag conflicts)
            with self.p.telemetry.span(
                "ingest.graph", {"tenant": tenant, "trace_id": trace_id, "stage": "graph"}
            ):
                self._fuse_graph(tenant, content_hash, raw, passage_mentions, all_relations)

            # ---- Step 6: Embed & index -------------------------------
            with self.p.telemetry.span(
                "ingest.embed", {"tenant": tenant, "trace_id": trace_id, "stage": "embed"}
            ):
                vecs = self.p.embedder.embed([p.text for p in passages])
                self.p.vindex.upsert(
                    tenant,
                    [{"passage_id": p.id, "vec": v} for p, v in zip(passages, vecs, strict=False)],
                )

            # ---- Step 7: Health snapshot -----------------------------
            with self.p.telemetry.span(
                "ingest.health", {"tenant": tenant, "trace_id": trace_id, "stage": "health"}
            ):
                from ..health.metrics import snapshot

                snapshot(self.p, tenant, pack)

            root.set(status="ok", passages=len(passages))
            result.update(
                status="ok" if not is_update else "updated",
                document_id=doc_id,
                passages=len(passages),
                version=version,
                entities=len({m.text for m in all_mentions}),
            )
            return result

    def _fuse_graph(self, tenant, content_hash, raw, passage_mentions, relations):
        # resolve mentions to canonical nodes, record provenance
        node_ids: dict[str, str] = {}
        cooc: dict[tuple[str, str], int] = {}
        for pas, mentions in passage_mentions:
            keys = []
            for m in mentions:
                existing = self.p.graph_repo.resolve(tenant, m.text, m.type)
                if existing:
                    nid = existing["id"]
                else:
                    nid = new_id("node_")
                    self.p.graph.upsert_nodes(
                        tenant,
                        [
                            GraphNode(
                                id=nid,
                                tenant=tenant,
                                canonical_key=m.text,
                                type=m.type,
                                labels=[m.text],
                                provenance=[
                                    Provenance(
                                        content_hash, raw.source, raw.source_version, pas.coordinate
                                    )
                                ],
                            )
                        ],
                    )
                node_ids[m.text] = nid
                keys.append(m.text)
            for a, b in combinations(sorted(set(keys)), 2):
                cooc[(a, b)] = cooc.get((a, b), 0) + 1

        # stated relations -> edges (contradiction flag on functional conflict)
        for rel in relations:
            src_id = node_ids.get(rel.src) or self._ensure_concept(
                tenant, rel.src, content_hash, raw
            )
            dst_id = node_ids.get(rel.dst) or self._ensure_concept(
                tenant, rel.dst, content_hash, raw
            )
            conflict = self._is_contradiction(tenant, src_id, rel.relation, dst_id)
            self.p.graph.upsert_edges(
                tenant,
                [
                    GraphEdge(
                        id=new_id("edge_"),
                        tenant=tenant,
                        src=src_id,
                        dst=dst_id,
                        relation=rel.relation,
                        weight=rel.confidence,
                        contextual_weight=0.0,
                        provenance=[Provenance(content_hash, raw.source, raw.source_version)],
                        conflict_flag=conflict,
                    )
                ],
            )
            if conflict:
                self.p.curation.add(
                    tenant,
                    f"contradiction on '{rel.relation}' from {rel.src}",
                    "contradiction",
                    now_ms(),
                )

        # co-occurrence-gated contextual edges: only pairs seen in >=2 passages
        for (a, b), n in cooc.items():
            if n >= 2:
                self.p.graph.upsert_edges(
                    tenant,
                    [
                        GraphEdge(
                            id=new_id("edge_"),
                            tenant=tenant,
                            src=node_ids[a],
                            dst=node_ids[b],
                            relation="co_occurs",
                            weight=0.0,
                            contextual_weight=min(1.0, n / 5.0),
                            provenance=[Provenance(content_hash, raw.source, raw.source_version)],
                        )
                    ],
                )

    def _ensure_concept(self, tenant, key, content_hash, raw) -> str:
        existing = self.p.graph_repo.resolve(tenant, key, "Concept")
        if existing:
            return existing["id"]
        nid = new_id("node_")
        self.p.graph.upsert_nodes(
            tenant,
            [
                GraphNode(
                    id=nid,
                    tenant=tenant,
                    canonical_key=key,
                    type="Concept",
                    labels=[key],
                    provenance=[Provenance(content_hash, raw.source, raw.source_version)],
                )
            ],
        )
        return nid

    def _is_contradiction(self, tenant, src_id, relation, dst_id) -> bool:
        # functional relation already pointing elsewhere from the same src
        functional = {"complies_with", "belongs_to"}
        if relation not in functional:
            return False
        for e in self.p.graph_repo.neighbors(tenant, src_id):
            if e["src"] == src_id and e["relation"] == relation and e["dst"] != dst_id:
                return True
        return False
