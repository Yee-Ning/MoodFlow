"""Validated, non-mutating merge of consecutive WorldLine turns."""

from app.agent.foundation.subgraphs.worldline.models import (
    EvidenceSource,
    WorldLineEdge,
    WorldLineGraph,
)


class WorldLineMergeError(ValueError):
    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


# Explicit query observations only: never exclude a provenance field by prefix.
_QUERY_OBSERVATIONS = frozenset({
    "fusion_score", "channel_evidence", "matched_child_ids",
    "retrieval_warnings", "rerank_warnings", "context_warnings",
    "context_pack_rank", "context_source_rank", "context_token_count", "context_token_budget",
    "retrieval_rank", "rerank_rank", "rerank_score", "rerank_status", "rerank_fallback_used",
    "subquery_ids", "retrieval_legs", "sufficiency_status",
})


def _source_identity(source: EvidenceSource) -> EvidenceSource:
    # Retrieval observations vary per query; claim/span/context/URL/hash remain
    # immutable and are deliberately NOT removed from this comparison.
    observations = set(_QUERY_OBSERVATIONS)
    if source.metadata.get("index_input_hash") and source.metadata.get("source_content_sha256"):
        # Hash of the compressed query view is not the immutable indexed Child
        # hash. The latter and every bound capture/hash remain protected.
        observations.add("context_document_sha256")
    return source.model_copy(update={"metadata": {
        k: v for k, v in source.metadata.items() if k not in observations
    }})


def merge_worldline_graph(
    old: WorldLineGraph,
    increment: WorldLineGraph,
    old_sources: list[EvidenceSource],
    new_sources: list[EvidenceSource],
    *,
    target_node_id: str,
    bounded: bool = False,
) -> tuple[WorldLineGraph, list[EvidenceSource]]:
    if increment.root != target_node_id or target_node_id not in {n.id for n in old.nodes}:
        raise WorldLineMergeError("invalid_expansion_root")

    nodes = {node.id: node for node in old.nodes}
    for node in increment.nodes:
        previous = nodes.get(node.id)
        if previous is not None and previous.type != node.type:
            raise WorldLineMergeError("node_type_conflict")
        nodes.setdefault(node.id, node)

    sources = {source.id: source for source in old_sources}
    for source in new_sources:
        previous = sources.get(source.id)
        if previous is not None and _source_identity(previous) != _source_identity(source):
            raise WorldLineMergeError("source_conflict")
        sources.setdefault(source.id, source)

    if bounded:
        # Session continuations retain the already published graph and admit
        # only complete, evidenced edges that fit the result schema. Catalog
        # fanout must not turn a valid continuation into a merge failure.
        available_sources = sources
        kept_nodes = {node.id: node for node in old.nodes}
        kept_edges = {(e.source, e.target, e.relation): e for e in old.edges}
        kept_sources = {source.id: source for source in old_sources}
        candidates = sorted(
            increment.edges,
            key=lambda edge: 0 if target_node_id in {edge.source, edge.target} else 1,
        )
        for edge in candidates:
            if any(source_id not in available_sources for source_id in edge.evidence_ids):
                raise WorldLineMergeError("missing_edge_evidence")
            key = (edge.source, edge.target, edge.relation)
            previous = kept_edges.get(key)
            evidence_ids = list(dict.fromkeys([
                *(previous.evidence_ids if previous else []), *edge.evidence_ids,
            ]))
            missing_nodes = {
                endpoint for endpoint in (edge.source, edge.target)
                if endpoint not in kept_nodes
            }
            missing_sources = {
                evidence_id for evidence_id in evidence_ids
                if evidence_id not in kept_sources
            }
            if (len(kept_nodes) + len(missing_nodes) > 15
                or len(kept_edges) + (previous is None) > 20
                or len(kept_sources) + len(missing_sources) > 30
                or len(evidence_ids) > 8):
                continue
            for node_id in missing_nodes:
                kept_nodes[node_id] = nodes[node_id]
            for source_id in missing_sources:
                kept_sources[source_id] = available_sources[source_id]
            kept_edges[key] = (
                edge if previous is None else WorldLineEdge(
                    source=previous.source, target=previous.target,
                    relation=previous.relation, description=previous.description,
                    evidence_ids=evidence_ids,
                )
            )
        for source in new_sources:
            if len(kept_sources) >= 30:
                break
            kept_sources.setdefault(source.id, source)
        try:
            graph = WorldLineGraph(
                root=old.root, nodes=list(kept_nodes.values()),
                edges=list(kept_edges.values()),
            )
        except ValueError as exc:
            raise WorldLineMergeError("invalid_merged_graph") from exc
        return graph, list(kept_sources.values())

    edges = {(e.source, e.target, e.relation): e for e in old.edges}
    for edge in increment.edges:
        key = (edge.source, edge.target, edge.relation)
        previous = edges.get(key)
        if previous is None:
            edges[key] = edge
            continue
        evidence_ids = list(dict.fromkeys([*previous.evidence_ids, *edge.evidence_ids]))
        if len(evidence_ids) > 8:
            raise WorldLineMergeError("capacity_reached")
        edges[key] = WorldLineEdge(
            source=previous.source,
            target=previous.target,
            relation=previous.relation,
            description=previous.description,
            evidence_ids=evidence_ids,
        )

    if len(nodes) > 15 or len(edges) > 20 or len(sources) > 30:
        raise WorldLineMergeError("capacity_reached")
    if any(evidence_id not in sources for edge in edges.values() for evidence_id in edge.evidence_ids):
        raise WorldLineMergeError("missing_edge_evidence")
    try:
        graph = WorldLineGraph(root=old.root, nodes=list(nodes.values()), edges=list(edges.values()))
    except ValueError as exc:
        raise WorldLineMergeError("invalid_merged_graph") from exc
    return graph, list(sources.values())
