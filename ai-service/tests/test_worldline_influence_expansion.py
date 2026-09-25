"""V2.4C Influence claims must have traceable evidence and unambiguous direction."""

from __future__ import annotations

import pytest

from app.agent.foundation.subgraphs.worldline.influence_expansion import (
    InfluenceExpansionService,
    is_explicit_influence_query,
    merge_influence_relations,
)
from app.agent.foundation.subgraphs.worldline.builder import build_worldline_graph
from app.agent.foundation.subgraphs.worldline.models import (
    EntityType as CatalogEntityType, MusicEntityFacts, MusicFact, ResolvedEntity,
    EvidenceSource, EvidenceType, MusicKnowledgeDocument, WorldLineGraph,
    WorldLineNode, WorldLineNodeType, InfluenceRelation, WorldLineSessionResult,
)
from app.rag.knowledge.entity_linking.models import EntityType
from app.rag.query_understanding.models import (
    CatalogReference, MetadataFilter, QueryEntityStatus, QueryType,
    QueryVariant, QueryVariantKind, ResolvedQueryEntity, RetrievalStrategy,
    SearchChannel, SearchPlan,
)


NODES = [
    ("netease:artist:1", WorldLineNodeType.ARTIST, "A", "artist:a", "1"),
    ("netease:artist:2", WorldLineNodeType.ARTIST, "B", "artist:b", "2"),
    ("netease:song:3", WorldLineNodeType.SONG, "Song B", "song:b", "3"),
    ("netease:album:4", WorldLineNodeType.ALBUM, "Album B", "album:b", "4"),
]


def graph(*, edges=[]) -> WorldLineGraph:
    return WorldLineGraph(
        root="netease:artist:1",
        nodes=[WorldLineNode(id=node_id, type=kind, name=name) for node_id, kind, name, _, _ in NODES]
        + [WorldLineNode(id="genre:jazz", type=WorldLineNodeType.GENRE, name="Jazz")],
        edges=edges,
    )


def plan() -> SearchPlan:
    entities = [ResolvedQueryEntity(
        surface=name, canonical_name=name, entity_type=EntityType(kind.value),
        knowledge_entity_id=knowledge_id,
        catalog_reference=CatalogReference(
            provider="netease", entity_id=catalog_id,
            entity_type=EntityType(kind.value), name=name,
        ),
        status=QueryEntityStatus.RESOLVED, confidence=1,
    ) for _, kind, name, knowledge_id, catalog_id in NODES]
    return SearchPlan(
        plan_id="query-plan:0123456789abcdef01234567",
        original_query="A 的音乐影响", standalone_query="A 的音乐影响",
        query_types=[QueryType.RELATIONSHIP], entities=entities,
        queries=[QueryVariant(text="A 的音乐影响", kind=QueryVariantKind.ORIGINAL,
                              channel=SearchChannel.BOTH, weight=1)],
        metadata_filter=MetadataFilter(), strategy=RetrievalStrategy.HYBRID,
        candidate_top_k=40, final_top_k=10, needs_rerank=True,
        analysis_version="v1", prompt_version="v1",
    )


def document(index: int, span: str, *, predicate="influenced", subject="artist:a",
             target="artist:b", confidence=0.97, supported=True, sufficient=True,
             include_span=True) -> MusicKnowledgeDocument:
    claim = {
        "claim_id": f"claim:{index:024x}",
        "subject_entity_id": subject, "object_entity_id": target,
        "predicate": predicate, "source_chunk_id": f"child-{index}",
        "evidence_span": span, "extraction_confidence": confidence,
        "verification_confidence": confidence,
    }
    return MusicKnowledgeDocument(
        id=f"parent-{index}", title=f"Reviewed source {index}",
        content=f"Context. {span if include_span else 'No direct span.'} More context.",
        source="reviewed-archive", url=f"https://example.com/influence/{index}",
        metadata={
            "parent_id": f"parent-{index}", "chunk_id": f"child-{index}",
            "context_version": "context-v1", "retrieval_version": "retrieval-v1",
            "sufficiency_status": "sufficient" if sufficient else "insufficient",
            "supported_claims": [claim] if supported else [],
        },
    )


def expand(documents, *, query="A 的音乐影响", old_graph=None, sources=None):
    return InfluenceExpansionService().expand(
        query, plan(), old_graph or graph(), sources or [], documents,
    )


@pytest.mark.parametrize("predicate,subject,target,span,relation_type,source_id,target_id", [
    ("influenced", "artist:a", "artist:b", "A influenced B.", "influenced", "netease:artist:1", "netease:artist:2"),
    ("influenced_by", "artist:b", "artist:a", "B was influenced by A.", "influenced", "netease:artist:1", "netease:artist:2"),
    ("influenced", "artist:a", "artist:b", "A had an influence on B.", "influenced", "netease:artist:1", "netease:artist:2"),
    ("inspired_by", "artist:b", "artist:a", "B was inspired by A.", "inspired", "netease:artist:1", "netease:artist:2"),
    ("cited_as_influence", "artist:b", "artist:a", "B cited A as an influence.", "cited_as_influence", "netease:artist:1", "netease:artist:2"),
    ("shaped_style", "artist:a", "artist:b", "A shaped B's sound and style.", "shaped_style", "netease:artist:1", "netease:artist:2"),
    ("influenced", "artist:a", "song:b", "A influenced Song B.", "influenced", "netease:artist:1", "netease:song:3"),
    ("inspired", "artist:a", "album:b", "A inspired Album B.", "inspired", "netease:artist:1", "netease:album:4"),
    ("influenced", "artist:a", "genre:jazz", "A influenced Jazz.", "influenced", "netease:artist:1", "genre:jazz"),
])
def test_explicit_claim_projects_normalized_direction_and_source(
    predicate, subject, target, span, relation_type, source_id, target_id,
):
    result = expand([document(1, span, predicate=predicate, subject=subject, target=target)])
    assert len(result.relations) == 1
    relation = result.relations[0]
    assert (relation.source_entity.node_id, relation.target_entity.node_id) == (source_id, target_id)
    assert relation.relation_type.value == relation_type
    assert relation.direction.value == "influencer_to_influenced"
    edge = result.graph.edges[0]
    assert (edge.source, edge.target, edge.relation.value) == (source_id, target_id, relation_type)
    assert edge.evidence_ids == [relation.evidence[0].source_id]
    source = next(item for item in result.sources if item.id == edge.evidence_ids[0])
    assert source.excerpt == span
    assert (source.metadata["document_id"], source.metadata["child_id"], source.metadata["claim_id"]) == (
        "parent-1", "child-1", relation.evidence[0].claim_id,
    )


@pytest.mark.parametrize("span,reason", [
    ("A was influenced by B.", "direction_reversed"),
    ("A and B were in the same scene.", "direction_missing"),
    ("A and B were active in the same era.", "direction_missing"),
    ("A and B both make Jazz music.", "direction_missing"),
    ("A sounds similar to B.", "direction_missing"),
    ("A released music before B.", "direction_missing"),
    ("A collaborated with B.", "direction_missing"),
    ("A may have influenced B.", "speculative_influence"),
    ("B was not influenced by A.", "negated_influence"),
    ("A did not influence B.", "negated_influence"),
])
def test_unsupported_or_reversed_claim_never_projects(span, reason):
    result = expand([document(1, span)])
    assert result.relations == []
    assert result.graph.edges == []
    assert reason in result.rejected[0].rejection_reasons


def test_missing_claim_span_or_sufficient_context_rejected():
    for doc in (
        document(1, "A influenced B.", supported=False),
        document(2, "A influenced B.", include_span=False),
        document(3, "A influenced B.", sufficient=False),
    ):
        assert expand([doc]).relations == []


def test_non_influence_queries_do_not_change_graph():
    doc = document(1, "A influenced B.")
    for query in ("探索 A 的世界线", "A 的音乐时代", "A 的音乐场景", "探索歌曲《Influence》"):
        result = expand([doc], query=query)
        assert result.relations == [] and result.graph.edges == []
    assert is_explicit_influence_query("A 的音乐影响")


def test_conflicting_direction_and_attribution_keep_provenance():
    docs = [
        document(1, "A influenced B."),
        document(2, "B influenced A.", subject="artist:b", target="artist:a"),
        document(3, "A did not influence B."),
    ]
    result = expand(docs)
    assert len(result.relations) == 2
    forward = next(item for item in result.relations if item.source_entity.canonical_name == "A")
    assert forward.metadata["direction_conflict"] is True
    assert forward.metadata["attribution_conflict"] is True
    assert {item.stance.value for item in forward.evidence} == {"support", "contradiction"}
    assert set(result.graph.edges[0].evidence_ids).issubset({item.id for item in result.sources})
    assert forward.confidence < 0.97


def test_conflicting_scope_keeps_each_claim_without_inventing_common_scope():
    result = expand([
        document(1, "A influenced B's sound."),
        document(2, "A influenced B's songwriting."),
    ])
    relation = result.relations[0]
    assert relation.influence_scope is None
    assert relation.metadata["scope_conflict"] is True
    assert len(relation.evidence) == 2
    assert relation.confidence < 0.97


def test_weak_speculation_is_rejected_with_reduced_candidate_confidence():
    result = expand([document(1, "A may have influenced B.")])
    decision = result.rejected[0]
    assert "speculative_influence" in decision.rejection_reasons
    assert decision.candidate.uncertain is True
    assert decision.candidate.confidence < decision.candidate.claim_confidence
    assert decision.candidate.evidence.strength.value == "speculative"


def test_capacity_skips_influence_without_mutating_existing_graph():
    initial = graph()
    sources = [EvidenceSource(id=f"old:{i}", source_type=EvidenceType.KNOWLEDGE,
                              title="Old", source="archive") for i in range(30)]
    result = expand([document(1, "A influenced B.")], old_graph=initial, sources=sources)
    assert result.relations == []
    assert result.graph == initial
    assert result.trace.rejection_reasons["capacity_reached"] == 1


def test_merge_relation_deduplicates_evidence():
    first = expand([document(1, "A influenced B.")]).relations[0]
    merged = merge_influence_relations([first], [first])
    assert len(merged) == 1
    assert merged[0].evidence == first.evidence
    assert merged[0].confidence == first.confidence


def test_catalog_and_raw_graph_metadata_cannot_bypass_influence_validator():
    a = ResolvedEntity(entity_id="netease:artist:1", entity_type=CatalogEntityType.ARTIST,
                       name="A", source="netease")
    b = ResolvedEntity(entity_id="netease:artist:2", entity_type=CatalogEntityType.ARTIST,
                       name="B", source="netease")
    facts = MusicEntityFacts(
        entity=a, related_entities=[b],
        facts=[MusicFact(id="fact:1", subject_id=a.entity_id, object_entity_id=b.entity_id,
                         predicate="influenced", value="unreviewed", evidence_ids=["catalog:1"])],
        sources=[EvidenceSource(id="catalog:1", source_type=EvidenceType.MUSIC_API,
                                title="Catalog", source="catalog")],
    )
    doc = document(1, "A and B are similar.", supported=False)
    doc = doc.model_copy(update={"metadata": {
        **doc.metadata,
        "graph_edges": [{"source": a.entity_id, "target": b.entity_id,
                         "relation": "influenced", "evidence_ids": []}],
    }})
    built, _ = build_worldline_graph(facts, [doc])
    assert built.edges == []


def test_result_schema_rejects_wrong_direction_or_unmatched_graph_evidence():
    result = expand([document(1, "A influenced B.")])
    relation = result.relations[0]
    with pytest.raises(ValueError):
        InfluenceRelation.model_validate({
            **relation.model_dump(mode="json"), "direction": "influenced_to_influencer",
        })
    root = ResolvedEntity(
        entity_id="netease:artist:1", entity_type=CatalogEntityType.ARTIST,
        name="A", source="netease",
    )
    valid = {
        "root_entity": root, "graph": result.graph, "narrative": "Evidence-based relation.",
        "branches": [], "sources": result.sources, "citations": [],
        "influence_relations": result.relations,
    }
    assert len(WorldLineSessionResult.model_validate(valid).influence_relations) == 1
    with pytest.raises(ValueError, match="directed graph edge"):
        WorldLineSessionResult.model_validate({
            **valid,
            "influence_relations": [relation.model_copy(update={
                "source_entity": relation.target_entity,
                "target_entity": relation.source_entity,
            })],
        })
    with pytest.raises(ValueError, match="evidence must match"):
        WorldLineSessionResult.model_validate({
            **valid, "sources": [],
        })
