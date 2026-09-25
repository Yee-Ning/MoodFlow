"""Representative V2.4A Era Relation acceptance and rejection cases."""

from __future__ import annotations

from copy import deepcopy

import pytest

from app.agent.foundation.subgraphs.worldline.era_expansion import (
    EraExpansionService,
    EraRelationValidator,
    is_explicit_era_query,
    merge_era_relations,
)
from app.agent.foundation.subgraphs.worldline.models import (
    EraRelationType,
    EraTemporalPrecision,
    EvidenceSource,
    EvidenceType,
    MusicKnowledgeDocument,
    WorldLineGraph,
    WorldLineNode,
    WorldLineNodeType,
    WorldLineRelation,
    WorldLineSessionResult,
)
from app.rag.knowledge.entity_linking.models import EntityType
from app.rag.query_understanding.models import (
    CatalogReference,
    MetadataFilter,
    QueryEntityStatus,
    QueryType,
    QueryVariant,
    QueryVariantKind,
    ResolvedQueryEntity,
    RetrievalStrategy,
    SearchChannel,
    SearchPlan,
)


ARTIST_ID = "netease:artist:42"
SONG_ID = "netease:song:7"
ALBUM_ID = "netease:album:9"


def plan(*, timeline: bool = True) -> SearchPlan:
    query = "测试歌手的早期阶段与 2000 年代"
    return SearchPlan(
        plan_id="query-plan:0123456789abcdef01234567",
        original_query=query,
        standalone_query=query,
        query_types=[QueryType.TIMELINE if timeline else QueryType.EXPLORATORY],
        entities=[
            ResolvedQueryEntity(
                surface="测试歌手",
                canonical_name="测试歌手",
                entity_type=EntityType.ARTIST,
                knowledge_entity_id="artist:test_artist",
                catalog_reference=CatalogReference(
                    provider="netease", entity_id="42", entity_type=EntityType.ARTIST,
                    name="测试歌手",
                ),
                status=QueryEntityStatus.RESOLVED,
                confidence=1,
            ),
            ResolvedQueryEntity(
                surface="测试单曲",
                canonical_name="测试单曲",
                entity_type=EntityType.SONG,
                knowledge_entity_id="song:test_song",
                catalog_reference=CatalogReference(
                    provider="netease", entity_id="7", entity_type=EntityType.SONG,
                    name="测试单曲",
                ),
                status=QueryEntityStatus.RESOLVED,
                confidence=1,
            ),
            ResolvedQueryEntity(
                surface="测试专辑",
                canonical_name="测试专辑",
                entity_type=EntityType.ALBUM,
                knowledge_entity_id="album:test_album",
                catalog_reference=CatalogReference(
                    provider="netease", entity_id="9", entity_type=EntityType.ALBUM,
                    name="测试专辑",
                ),
                status=QueryEntityStatus.RESOLVED,
                confidence=1,
            ),
        ],
        queries=[QueryVariant(
            text=query, kind=QueryVariantKind.ORIGINAL,
            channel=SearchChannel.BOTH, weight=1,
        )],
        metadata_filter=MetadataFilter(),
        strategy=RetrievalStrategy.HYBRID,
        candidate_top_k=40,
        final_top_k=10,
        needs_rerank=True,
        analysis_version="v1",
        prompt_version="v1",
    )


def graph() -> WorldLineGraph:
    return WorldLineGraph(
        root=ARTIST_ID,
        nodes=[
            WorldLineNode(id=ARTIST_ID, type=WorldLineNodeType.ARTIST, name="测试歌手"),
            WorldLineNode(id=SONG_ID, type=WorldLineNodeType.SONG, name="测试单曲"),
            WorldLineNode(id=ALBUM_ID, type=WorldLineNodeType.ALBUM, name="测试专辑"),
            WorldLineNode(
                id="genre:jazz", type=WorldLineNodeType.GENRE, name="Jazz",
                metadata={"entity_reference": {"knowledge_entity_id": "genre:jazz"}},
            ),
        ],
    )


def sources() -> list[EvidenceSource]:
    return [EvidenceSource(
        id="music-api:artist:42", source_type=EvidenceType.MUSIC_API,
        title="目录", source="catalog",
    )]


def document(
    index: int,
    *,
    predicate: str,
    subject: str,
    span: str,
    value: str | None = None,
    target: str | None = None,
    time: dict | None = None,
    sufficient: bool = True,
    confidence: float = 0.96,
) -> MusicKnowledgeDocument:
    claim = {
        "claim_id": f"claim:{index:024x}",
        "subject_entity_id": subject,
        "predicate": predicate,
        "source_chunk_id": f"child-{index}",
        "evidence_span": span,
        "extraction_confidence": confidence,
        "verification_confidence": confidence,
    }
    if value is not None:
        claim["object_value"] = value
    if target is not None:
        claim["object_entity_id"] = target
    if time is not None:
        claim["time"] = time
    return MusicKnowledgeDocument(
        id=f"parent-{index}", title=f"审核来源 {index}",
        content=f"背景。{span}更多资料。", source="reviewed-source",
        url=f"https://example.com/era/{index}",
        metadata={
            "parent_id": f"parent-{index}",
            "chunk_id": f"child-{index}",
            "context_version": "context-v1",
            "retrieval_version": "retrieval-v1",
            "sufficiency_status": "sufficient" if sufficient else "insufficient",
            "supported_claims": [claim],
        },
    )


def expand(documents, *, query: str = "测试歌手的时代"):
    return EraExpansionService().expand(query, plan(), graph(), sources(), documents)


def test_exact_year_entity_era_and_source_traceability() -> None:
    doc = document(
        1, predicate="belongs_to_era", subject="artist:test_artist",
        value="2000s", time={"year": 2004, "raw": "2004"},
        span="测试歌手在 2004 年属于 2000s 音乐时期。",
    )
    result = expand([doc])

    assert result.trace.accepted_count == 1
    relation = result.relations[0]
    assert relation.relation_type is EraRelationType.BELONGS_TO_ERA
    assert (relation.start_year, relation.end_year) == (2004, 2004)
    assert relation.temporal_precision is EraTemporalPrecision.EXACT
    assert relation.era.node_id == "era:2000s"
    edge = next(item for item in result.graph.edges if item.relation is WorldLineRelation.BELONGS_TO_ERA)
    assert edge.evidence_ids == [relation.evidence[0].source_id]
    source = next(item for item in result.sources if item.id == edge.evidence_ids[0])
    assert source.excerpt == doc.metadata["supported_claims"][0]["evidence_span"]
    assert source.metadata["claim_id"] == relation.evidence[0].claim_id


def test_cross_era_artist_keeps_both_evidence_grounded_phases() -> None:
    docs = [
        document(2, predicate="career_phase", subject="artist:test_artist",
                 value="early", time={"year": 1999},
                 span="测试歌手在 1999 年进入早期创作阶段。"),
        document(3, predicate="career_phase", subject="artist:test_artist",
                 value="breakthrough", time={"year": 2004},
                 span="测试歌手在 2004 年进入突破期。"),
    ]
    result = expand(docs)
    assert {item.era.canonical_name for item in result.relations} == {
        "Early career", "Breakthrough",
    }
    assert len({item.era.node_id for item in result.relations}) == 2
    assert all(item.relation_type is EraRelationType.CAREER_PHASE for item in result.relations)


def test_conflicting_sources_preserve_provenance_and_lower_confidence() -> None:
    docs = [
        document(4, predicate="belongs_to_era", subject="artist:test_artist",
                 value="2000s", time={"year": 2001},
                 span="测试歌手在 2001 年属于 2000s 音乐时期。"),
        document(5, predicate="belongs_to_era", subject="artist:test_artist",
                 value="2000s", time={"year": 2003},
                 span="测试歌手在 2003 年属于 2000s 音乐时期。"),
    ]
    result = expand(docs)
    relation = result.relations[0]
    assert len(result.relations) == 1
    assert len(relation.evidence) == 2
    assert relation.start_year is None and relation.end_year is None
    assert relation.temporal_precision is EraTemporalPrecision.UNKNOWN
    assert relation.confidence < 0.96
    assert relation.metadata["temporal_conflict"] is True
    assert {item["start_year"] for item in relation.metadata["temporal_assertions"]} == {2001, 2003}
    assert len(result.graph.edges[0].evidence_ids) == 2


def test_approximate_year_keeps_precision_without_invented_bounds() -> None:
    doc = document(
        6, predicate="career_phase", subject="artist:test_artist",
        value="early", time={"year": 1998, "raw": "约 1998 年"},
        span="测试歌手约 1998 年进入早期阶段。",
    )
    relation = expand([doc]).relations[0]
    assert (relation.start_year, relation.end_year) == (1998, 1998)
    assert relation.temporal_precision is EraTemporalPrecision.APPROXIMATE
    assert relation.evidence[0].raw_time == "约 1998 年"


def test_no_supported_evidence_and_non_era_query_leave_graph_unchanged() -> None:
    doc = document(
        7, predicate="belongs_to_era", subject="artist:test_artist",
        value="2000s", span="测试歌手属于 2000s 音乐时期。",
    )
    doc_without_claim = doc.model_copy(update={"metadata": {
        **doc.metadata, "supported_claims": [],
    }})
    no_evidence = expand([doc_without_claim])
    ordinary = EraExpansionService().expand(
        "探索测试歌手的世界线", plan(timeline=False), graph(), sources(), [doc],
    )
    assert no_evidence.relations == []
    assert no_evidence.graph == graph()
    assert ordinary.relations == []
    assert ordinary.graph == graph()
    assert ordinary.trace.requested is False
    assert not is_explicit_era_query("探索测试歌手的世界线", plan(timeline=False))


def test_full_graph_keeps_existing_result_without_era_capacity_failure() -> None:
    full = WorldLineGraph(
        root=ARTIST_ID,
        nodes=[
            graph().nodes[0],
            *[
                WorldLineNode(
                    id=f"netease:song:{index}",
                    type=WorldLineNodeType.SONG,
                    name=f"作品 {index}",
                )
                for index in range(1, 15)
            ],
        ],
    )
    doc = document(
        22, predicate="belongs_to_era", subject="artist:test_artist",
        value="2000s", time={"year": 2004},
        span="测试歌手在 2004 年属于 2000s 音乐时期。",
    )
    result = EraExpansionService().expand("测试歌手的时代", plan(), full, sources(), [doc])
    assert result.graph == full
    assert result.relations == []
    assert result.trace.rejection_reasons == {"capacity_reached": 1}


def test_many_sources_keep_bounded_evidence_without_losing_base_graph() -> None:
    docs = [
        document(
            30 + index, predicate="belongs_to_era", subject="artist:test_artist",
            value="2000s", time={"year": 2004},
            span=f"测试歌手在 2004 年属于 2000s 音乐时期。来源 {index}。",
        )
        for index in range(9)
    ]
    result = expand(docs)
    assert len(result.relations) == 1
    assert len(result.relations[0].evidence) == 8
    assert len(result.graph.edges[0].evidence_ids) == 8
    assert result.trace.rejection_reasons["evidence_capacity_reached"] == 1
    next_result = EraExpansionService().expand(
        "测试歌手的时代", plan(), result.graph, result.sources, [docs[8]],
    )
    assert next_result.graph == result.graph
    assert next_result.sources == result.sources
    assert next_result.trace.rejection_reasons["capacity_reached"] == 1


@pytest.mark.parametrize("mutation,reason", [
    (lambda doc: {**doc.metadata, "sufficiency_status": "insufficient"}, "insufficient_context"),
    (lambda doc: {**doc.metadata, "supported_claims": [
        {**doc.metadata["supported_claims"][0], "time": {"year": 2005}}
    ]}, "time_not_in_evidence_span"),
])
def test_invalid_evidence_is_rejected(mutation, reason) -> None:
    doc = document(
        8, predicate="belongs_to_era", subject="artist:test_artist",
        value="2000s", time={"year": 2004},
        span="测试歌手在 2004 年属于 2000s 音乐时期。",
    )
    invalid = doc.model_copy(update={"metadata": mutation(doc)})
    result = expand([invalid])
    assert result.relations == []
    assert result.graph == graph()
    assert reason in result.rejected[0].rejection_reasons


def test_verified_released_song_and_album_project_decades() -> None:
    docs = [
        document(9, predicate="released", subject="song:test_song",
                 value="2004", time={"year": 2004},
                 span="测试单曲于 2004 年发行。"),
        document(10, predicate="released", subject="album:test_album",
                 value="1998", time={"year": 1998},
                 span="测试专辑于 1998 年发行。"),
    ]
    result = expand(docs)
    assert {(item.source_entity.node_id, item.era.node_id) for item in result.relations} == {
        (SONG_ID, "era:2000s"), (ALBUM_ID, "era:1990s"),
    }


def test_verified_artist_release_only_proves_activity_in_decade() -> None:
    doc = document(
        19, predicate="released", subject="artist:test_artist",
        target="album:test_album", time={"year": 2004, "raw": "2004 年"},
        span="测试歌手于 2004 年发行测试专辑。",
    )
    result = expand([doc])
    assert len(result.relations) == 1
    relation = result.relations[0]
    assert relation.source_entity.node_id == ARTIST_ID
    assert relation.era.node_id == "era:2000s"
    assert relation.metadata["derivation"] == "release_activity"
    assert "发行活动" in result.graph.edges[0].description
    assert all(item.relation_type is not EraRelationType.REPRESENTATIVE_OF_ERA for item in result.relations)


def test_verified_debut_only_projects_early_career() -> None:
    doc = document(
        20, predicate="debuted", subject="artist:test_artist",
        value="出道", time={"year": 2001},
        span="测试歌手于 2001 年出道。",
    )
    result = expand([doc])
    assert len(result.relations) == 1
    assert result.relations[0].relation_type is EraRelationType.CAREER_PHASE
    assert result.relations[0].era.canonical_name == "Early career"
    assert result.relations[0].metadata["derivation"] == "debut_activity"


def test_representative_temporal_and_style_relations_use_closed_vocabulary() -> None:
    docs = [
        document(11, predicate="representative_of_era", subject="artist:test_artist",
                 value="2000s", time={"year": 2004},
                 span="测试歌手是 2000s 的代表艺人，2004 年活跃。"),
        document(12, predicate="temporal_association", subject="artist:test_artist",
                 target="song:test_song", time={"year": 2004},
                 span="测试歌手与测试单曲在 2004 年具有同期关联。"),
        document(13, predicate="style_era_association", subject="genre:jazz",
                 value="1990s", time={"year": 1998},
                 span="Jazz 风格在 1998 年的 1990s 进入新的发展阶段。"),
    ]
    result = expand(docs)
    assert {item.relation_type for item in result.relations} == {
        EraRelationType.REPRESENTATIVE_OF_ERA,
        EraRelationType.TEMPORAL_ASSOCIATION,
        EraRelationType.STYLE_ERA_ASSOCIATION,
    }
    assert all(edge.evidence_ids for edge in result.graph.edges)
    representative = next(item for item in result.relations if item.relation_type is EraRelationType.REPRESENTATIVE_OF_ERA)
    assert representative.source_entity.node_type is WorldLineNodeType.ERA
    assert representative.target_entity.node_id == ARTIST_ID


def test_unsupported_predicate_and_absent_year_do_not_project() -> None:
    docs = [
        document(14, predicate="influenced_by", subject="artist:test_artist",
                 value="1990s", span="测试歌手受 1990s 音乐影响。"),
        document(15, predicate="temporal_association", subject="artist:test_artist",
                 target="song:test_song", span="测试歌手与测试单曲具有联系。"),
    ]
    result = expand(docs)
    assert result.relations == []
    assert "temporal_evidence_missing" in result.rejected[0].rejection_reasons


def test_co_mention_and_negated_era_claims_are_rejected() -> None:
    docs = [
        document(23, predicate="belongs_to_era", subject="artist:test_artist",
                 value="2000s", span="测试歌手与 2000s 一同出现于目录。"),
        document(24, predicate="belongs_to_era", subject="artist:test_artist",
                 value="2000s", span="测试歌手不是 2000s 时期的艺人。"),
    ]
    result = expand(docs)
    assert result.relations == []
    assert result.trace.rejection_reasons["relation_not_in_evidence_span"] == 2


def test_relation_merge_preserves_old_evidence_without_mutation() -> None:
    first = expand([document(
        16, predicate="belongs_to_era", subject="artist:test_artist",
        value="2000s", time={"year": 2004},
        span="测试歌手在 2004 年属于 2000s 音乐时期。",
    )]).relations
    old = deepcopy(first)
    second = expand([document(
        17, predicate="belongs_to_era", subject="artist:test_artist",
        value="2000s", time={"year": 2004},
        span="2004 年测试歌手继续属于 2000s 音乐时期。",
    )]).relations
    merged = merge_era_relations(first, second)
    assert len(merged) == 1
    assert len(merged[0].evidence) == 2
    assert first == old


def test_old_result_without_era_relations_deserializes() -> None:
    from app.agent.foundation.subgraphs.worldline.models import ResolvedEntity, EntityType as CatalogEntityType

    payload = {
        "root_entity": ResolvedEntity(
            entity_id=ARTIST_ID, entity_type=CatalogEntityType.ARTIST,
            name="测试歌手", source="netease",
        ).model_dump(mode="json"),
        "graph": graph().model_dump(mode="json"),
        "narrative": "基于已有目录事实。",
        "branches": [],
        "sources": [item.model_dump(mode="json") for item in sources()],
        "citations": [],
    }
    result = WorldLineSessionResult.model_validate(payload)
    assert result.era_relations == []


def test_result_rejects_era_dto_without_matching_edge() -> None:
    from app.agent.foundation.subgraphs.worldline.models import (
        EntityType as CatalogEntityType,
        ResolvedEntity,
    )

    expanded = expand([document(
        21, predicate="belongs_to_era", subject="artist:test_artist",
        value="2000s", time={"year": 2004},
        span="测试歌手在 2004 年属于 2000s 音乐时期。",
    )])
    payload = {
        "root_entity": ResolvedEntity(
            entity_id=ARTIST_ID, entity_type=CatalogEntityType.ARTIST,
            name="测试歌手", source="netease",
        ).model_dump(mode="json"),
        "graph": expanded.graph.model_copy(update={"edges": []}).model_dump(mode="json"),
        "narrative": "来源资料。",
        "branches": [],
        "sources": [item.model_dump(mode="json") for item in expanded.sources],
        "citations": [],
        "era_relations": [item.model_dump(mode="json") for item in expanded.relations],
    }
    with pytest.raises(ValueError, match="era relation must reference result graph edge"):
        WorldLineSessionResult.model_validate(payload)


def test_claim_time_tampering_is_rejected_even_when_span_contains_year() -> None:
    doc = document(
        18, predicate="belongs_to_era", subject="artist:test_artist",
        value="2000s", time={"year": 2004},
        span="测试歌手在 2004 年属于 2000s 音乐时期。",
    )
    service = EraExpansionService()
    candidate = service.extractor.extract(plan(), graph(), [doc])[0]
    tampered = candidate.model_copy(update={"start_year": 2003, "end_year": 2003})
    relations, decisions = EraRelationValidator().validate(graph(), [doc], [tampered])
    assert relations == []
    assert "time_not_in_evidence_span" in decisions[0].rejection_reasons
