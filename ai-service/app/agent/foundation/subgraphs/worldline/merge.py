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
        if previous is not None and previous != source:
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
