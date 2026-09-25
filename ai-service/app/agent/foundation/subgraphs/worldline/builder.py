import logging
from typing import Any

from pydantic import ValidationError

from app.agent.foundation.subgraphs.worldline.models import (
    EntityType,
    EvidenceSource,
    EvidenceType,
    MusicEntityFacts,
    MusicKnowledgeDocument,
    WorldLineEdge,
    WorldLineGraph,
    WorldLineNode,
    WorldLineNodeType,
    WorldLineRelation,
)

logger = logging.getLogger(__name__)

MAX_WORLDLINE_NODES = 15
MAX_WORLDLINE_EDGES = 20

_NODE_TYPES = {
    EntityType.SONG: WorldLineNodeType.SONG,
    EntityType.ARTIST: WorldLineNodeType.ARTIST,
    EntityType.ALBUM: WorldLineNodeType.ALBUM,
}


def build_worldline_graph(
    entity_facts: MusicEntityFacts,
    retrieved_docs: list[MusicKnowledgeDocument],
) -> tuple[WorldLineGraph, list[EvidenceSource]]:
    """Build a bounded graph only from catalog facts and structured retrieval data."""
    root = entity_facts.entity
    nodes: dict[str, WorldLineNode] = {
        root.entity_id: _entity_node(root, is_root=True)
    }
    for entity in entity_facts.related_entities:
        if len(nodes) >= MAX_WORLDLINE_NODES:
            break
        nodes.setdefault(entity.entity_id, _entity_node(entity))

    edges: list[WorldLineEdge] = []
    edge_keys: set[tuple[str, str, WorldLineRelation]] = set()
    for fact in entity_facts.facts:
        if fact.object_entity_id is None:
            continue
        try:
            relation = WorldLineRelation(fact.predicate)
        except ValueError:
            continue
        if relation in {
            WorldLineRelation.INFLUENCED_BY,
            WorldLineRelation.INFLUENCED,
            WorldLineRelation.INSPIRED,
            WorldLineRelation.SHAPED_STYLE,
            WorldLineRelation.CITED_AS_INFLUENCE,
        }:
            continue
        if fact.subject_id not in nodes or fact.object_entity_id not in nodes:
            continue
        edge = WorldLineEdge(
            source=fact.subject_id,
            target=fact.object_entity_id,
            relation=relation,
            description=fact.value,
            evidence_ids=fact.evidence_ids,
        )
        _append_edge(edges, edge_keys, edge)
        if len(edges) >= MAX_WORLDLINE_EDGES:
            break

    sources = list(entity_facts.sources)
    source_ids = {source.id for source in sources}
    remaining_source_slots = max(0, 30 - len(sources))
    for document in retrieved_docs[:remaining_source_slots]:
        evidence_id = f"knowledge:{document.id}"
        if evidence_id not in source_ids:
            sources.append(
                EvidenceSource(
                    id=evidence_id,
                    source_type=EvidenceType.KNOWLEDGE,
                    title=document.title,
                    source=document.source,
                    url=document.url,
                    excerpt=document.content[:2_000],
                    metadata=document.metadata,
                )
            )
            source_ids.add(evidence_id)
        _merge_structured_knowledge(
            document,
            evidence_id,
            nodes,
            edges,
            edge_keys,
        )

    graph = WorldLineGraph(
        root=root.entity_id,
        nodes=list(nodes.values()),
        edges=edges,
    )
    return graph, sources[:30]


def _entity_node(entity, *, is_root: bool = False) -> WorldLineNode:
    description = f"{entity.source} 音乐目录中的{_NODE_TYPES[entity.entity_type].value}实体"
    if is_root:
        description = f"世界线根实体；{description}"
    return WorldLineNode(
        id=entity.entity_id,
        type=_NODE_TYPES[entity.entity_type],
        name=entity.name,
        description=description,
        metadata={
            **entity.metadata,
            "artist": entity.artist,
            "album": entity.album,
            "source": entity.source,
        },
    )


def _append_edge(
    edges: list[WorldLineEdge],
    edge_keys: set[tuple[str, str, WorldLineRelation]],
    edge: WorldLineEdge,
) -> None:
    key = (edge.source, edge.target, edge.relation)
    if key in edge_keys or len(edges) >= MAX_WORLDLINE_EDGES:
        return
    edges.append(edge)
    edge_keys.add(key)


def _merge_structured_knowledge(
    document: MusicKnowledgeDocument,
    evidence_id: str,
    nodes: dict[str, WorldLineNode],
    edges: list[WorldLineEdge],
    edge_keys: set[tuple[str, str, WorldLineRelation]],
) -> None:
    """Consume optional RAG graph payloads; prose alone never creates relationships."""
    raw_nodes = document.metadata.get("graph_nodes", [])
    raw_edges = document.metadata.get("graph_edges", [])
    if not isinstance(raw_nodes, list) or not isinstance(raw_edges, list):
        logger.warning(
            "Ignoring malformed WorldLine knowledge metadata: document_id=%s",
            document.id,
        )
        return

    for raw_node in raw_nodes:
        if len(nodes) >= MAX_WORLDLINE_NODES:
            break
        if not isinstance(raw_node, dict):
            continue
        try:
            node = WorldLineNode.model_validate(raw_node)
        except ValidationError:
            logger.warning(
                "Ignoring invalid WorldLine knowledge node: document_id=%s",
                document.id,
            )
            continue
        nodes.setdefault(node.id, node)

    for raw_edge in raw_edges:
        if len(edges) >= MAX_WORLDLINE_EDGES:
            break
        if not isinstance(raw_edge, dict):
            continue
        payload: dict[str, Any] = dict(raw_edge)
        raw_evidence = payload.get("evidence_ids", [])
        if not isinstance(raw_evidence, list):
            raw_evidence = []
        payload["evidence_ids"] = list(
            dict.fromkeys([*raw_evidence, evidence_id])
        )[:8]
        try:
            edge = WorldLineEdge.model_validate(payload)
        except ValidationError:
            logger.warning(
                "Ignoring invalid WorldLine knowledge edge: document_id=%s",
                document.id,
            )
            continue
        if edge.relation in {
            WorldLineRelation.ASSOCIATED_WITH,
            WorldLineRelation.BELONGS_TO_ERA,
            WorldLineRelation.REPRESENTATIVE_OF_ERA,
            WorldLineRelation.TEMPORAL_ASSOCIATION,
            WorldLineRelation.CAREER_PHASE,
            WorldLineRelation.STYLE_ERA_ASSOCIATION,
            WorldLineRelation.BELONGS_TO_SCENE,
            WorldLineRelation.REPRESENTATIVE_OF_SCENE,
            WorldLineRelation.SCENE_ASSOCIATION,
            WorldLineRelation.STYLE_SCENE_ASSOCIATION,
            WorldLineRelation.EMERGED_FROM_SCENE,
            WorldLineRelation.INFLUENCED_BY,
            WorldLineRelation.INFLUENCED,
            WorldLineRelation.INSPIRED,
            WorldLineRelation.SHAPED_STYLE,
            WorldLineRelation.CITED_AS_INFLUENCE,
        }:
            logger.warning(
                "Ignoring unvalidated semantic/era/scene/influence relation metadata: document_id=%s",
                document.id,
            )
            continue
        if edge.source not in nodes or edge.target not in nodes:
            continue
        _append_edge(edges, edge_keys, edge)
