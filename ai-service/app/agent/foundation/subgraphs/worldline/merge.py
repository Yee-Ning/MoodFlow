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


def _source_identity(source: EvidenceSource) -> EvidenceSource:
    # Retrieval observations vary per query; claim/span/context/URL/hash remain
    # immutable and are deliberately NOT removed from this comparison.
    observations = {
        "fusion_score", "channel_evidence", "retrieval_warnings", "rerank_warnings", "context_warnings",
        "context_pack_rank", "context_source_rank", "context_token_count", "context_token_budget",
        "retrieval_rank", "rerank_rank", "rerank_score", "rerank_status", "rerank_fallback_used",
        "subquery_ids", "retrieval_legs", "sufficiency_status",
    }
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
