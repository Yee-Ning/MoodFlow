"""V2.4B Scene Relation evidence and graph contracts."""

from __future__ import annotations

from copy import deepcopy

import pytest

from app.agent.foundation.subgraphs.worldline.models import (
    EraTemporalPrecision,
    EvidenceSource,
    EvidenceType,
    MusicKnowledgeDocument,
    SceneRelationType,
    WorldLineGraph,
    WorldLineNode,
    WorldLineNodeType,
    WorldLineRelation,
    WorldLineSessionResult,
)
from app.agent.foundation.subgraphs.worldline.scene_expansion import (
    SceneExpansionService,
    is_explicit_scene_query,
    merge_scene_relations,
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


ARTIST = "netease:artist:1"
OTHER_ARTIST = "netease:artist:2"
SONG = "netease:song:3"
ALBUM = "netease:album:4"
GENRE = "genre:grunge"
SCENE_NAME = "Seattle grunge scene"


def plan() -> SearchPlan:
    query = "Nirvana 的音乐场景"
    entities = []
    for surface, knowledge_id, provider_id, kind in (
        ("Nirvana", "artist:nirvana", "1", EntityType.ARTIST),
        ("Alice in Chains", "artist:alice_in_chains", "2", EntityType.ARTIST),
        ("Smells Like Teen Spirit", "song:teen_spirit", "3", EntityType.SONG),
        ("Nevermind", "album:nevermind", "4", EntityType.ALBUM),
    ):
        entities.append(ResolvedQueryEntity(
            surface=surface, canonical_name=surface, entity_type=kind,
            knowledge_entity_id=knowledge_id,
            catalog_reference=CatalogReference(
                provider="netease", entity_id=provider_id,
                entity_type=kind, name=surface,
            ),
            status=QueryEntityStatus.RESOLVED, confidence=1,
        ))
    return SearchPlan(
        plan_id="query-plan:0123456789abcdef01234567",
        original_query=query, standalone_query=query,
        query_types=[QueryType.RELATIONSHIP], entities=entities,
        queries=[QueryVariant(
            text=query, kind=QueryVariantKind.ORIGINAL,
            channel=SearchChannel.BOTH, weight=1,
        )],
        metadata_filter=MetadataFilter(),
        strategy=RetrievalStrategy.HYBRID,
        candidate_top_k=40, final_top_k=10,
        needs_rerank=True, analysis_version="v1", prompt_version="v1",
    )


def graph() -> WorldLineGraph:
    return WorldLineGraph(
        root=ARTIST,
        nodes=[
            WorldLineNode(id=ARTIST, type=WorldLineNodeType.ARTIST, name="Nirvana"),
            WorldLineNode(id=OTHER_ARTIST, type=WorldLineNodeType.ARTIST, name="Alice in Chains"),
            WorldLineNode(id=SONG, type=WorldLineNodeType.SONG, name="Smells Like Teen Spirit"),
            WorldLineNode(id=ALBUM, type=WorldLineNodeType.ALBUM, name="Nevermind"),
            WorldLineNode(
                id=GENRE, type=WorldLineNodeType.GENRE, name="Grunge",
                metadata={"entity_reference": {"knowledge_entity_id": GENRE}},
            ),
        ],
    )


def sources() -> list[EvidenceSource]:
    return [EvidenceSource(
        id="music-api:artist:1", source_type=EvidenceType.MUSIC_API,
        title="目录", source="catalog",
    )]


def document(
    index: int,
    *,
    predicate: str = "belongs_to_scene",
    subject: str = "artist:nirvana",
    value: str | None = SCENE_NAME,
    target: str | None = None,
    span: str = "Nirvana was part of the Seattle grunge scene in 1992.",
    time: dict | None = None,
    location: str | None = None,
    scene_name: str | None = None,
    sufficient: bool = True,
    supported: bool = True,
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
    if location is not None:
        claim["scene_location"] = location
    if scene_name is not None:
        claim["scene_name"] = scene_name
    return MusicKnowledgeDocument(
        id=f"parent-{index}", title=f"审核来源 {index}",
        content=f"背景。{span}更多资料。", source="reviewed-source",
        url=f"https://example.com/scene/{index}",
        metadata={
            "parent_id": f"parent-{index}",
            "chunk_id": f"child-{index}",
            "context_version": "context-v1",
            "retrieval_version": "retrieval-v1",
            "sufficiency_status": "sufficient" if sufficient else "insufficient",
            "supported_claims": [claim] if supported else [],
        },
    )


def expand(documents, *, query: str = "Nirvana 的音乐场景"):
    return SceneExpansionService().expand(query, plan(), graph(), sources(), documents)


def test_explicit_artist_scene_evidence_projects_traceable_graph() -> None:
    doc = document(1, time={"year": 1992}, location="Seattle")
    result = expand([doc])
    assert result.trace.accepted_count == 1
    relation = result.relations[0]
    assert relation.relation_type is SceneRelationType.BELONGS_TO_SCENE
    assert relation.scene.node_id == "scene:seattle_grunge_scene"
    assert relation.location == "Seattle"
    assert (relation.start_year, relation.end_year) == (1992, 1992)
    edge = result.graph.edges[0]
    assert edge.relation is WorldLineRelation.BELONGS_TO_SCENE
    assert edge.evidence_ids == [relation.evidence[0].source_id]
    source = next(item for item in result.sources if item.id == edge.evidence_ids[0])
    assert source.excerpt == doc.metadata["supported_claims"][0]["evidence_span"]
    assert source.metadata["document_id"] == doc.id
    assert source.metadata["child_id"] == "child-1"
    assert source.metadata["claim_id"] == relation.evidence[0].claim_id


@pytest.mark.parametrize("subject,expected_node,name", [
    ("song:teen_spirit", SONG, "Smells Like Teen Spirit"),
    ("album:nevermind", ALBUM, "Nevermind"),
])
def test_song_and_album_membership_require_explicit_scene_evidence(subject, expected_node, name) -> None:
    result = expand([document(
        28 if expected_node == SONG else 29,
        subject=subject,
        span=f"{name} was part of the Seattle grunge scene.",
    )])
    assert len(result.relations) == 1
    assert result.relations[0].source_entity.node_id == expected_node


def test_representative_style_and_emergence_use_closed_types() -> None:
    docs = [
        document(2, predicate="representative_of_scene", time={"year": 1992},
                 span="Nirvana was a representative of the Seattle grunge scene in 1992."),
        document(3, predicate="style_scene_association", subject=GENRE,
                 span="Grunge style defined the Seattle grunge scene."),
        document(4, predicate="emerged_from_scene", subject="album:nevermind",
                 span="Nevermind emerged from the Seattle grunge scene."),
    ]
    result = expand(docs)
    assert {item.relation_type for item in result.relations} == {
        SceneRelationType.REPRESENTATIVE_OF_SCENE,
        SceneRelationType.STYLE_SCENE_ASSOCIATION,
        SceneRelationType.EMERGED_FROM_SCENE,
    }
    representative = next(item for item in result.relations if item.relation_type is SceneRelationType.REPRESENTATIVE_OF_SCENE)
    assert representative.source_entity.node_type is WorldLineNodeType.SCENE
    assert representative.target_entity.node_id == ARTIST
    assert all(edge.evidence_ids for edge in result.graph.edges)


def test_two_verified_memberships_derive_artist_association() -> None:
    docs = [
        document(5),
        document(6, subject="artist:alice_in_chains",
                 span="Alice in Chains was part of the Seattle grunge scene in 1993.",
                 time={"year": 1993}),
    ]
    result = expand(docs)
    association = next(item for item in result.relations if item.relation_type is SceneRelationType.SCENE_ASSOCIATION)
    assert {association.source_entity.node_id, association.target_entity.node_id} == {ARTIST, OTHER_ARTIST}
    assert len(association.evidence) == 2
    assert association.start_year is None and association.end_year is None
    edge = next(item for item in result.graph.edges if item.relation is WorldLineRelation.SCENE_ASSOCIATION)
    assert set(edge.evidence_ids) == {item.source_id for item in association.evidence}


def test_explicit_artist_pair_claim_requires_named_scene_and_both_artists() -> None:
    direct = document(
        22, predicate="scene_association", value=None,
        target="artist:alice_in_chains", scene_name=SCENE_NAME,
        span="Nirvana and Alice in Chains both belonged to the Seattle grunge scene.",
    )
    result = expand([direct])
    assert len(result.relations) == 1
    assert result.relations[0].relation_type is SceneRelationType.SCENE_ASSOCIATION
    assert result.graph.edges[0].evidence_ids == [result.relations[0].evidence[0].source_id]

    weak = direct.model_copy(update={"metadata": {
        **direct.metadata,
        "supported_claims": [{
            **direct.metadata["supported_claims"][0],
            "evidence_span": "Nirvana and Alice in Chains were both from Seattle.",
        }],
        "parent_id": direct.id,
    }, "content": "Nirvana and Alice in Chains were both from Seattle."})
    rejected = expand([weak])
    assert rejected.relations == []


def test_multiple_shared_scenes_do_not_collapse_to_one_artist_pair_edge() -> None:
    docs = [
        document(24),
        document(25, subject="artist:alice_in_chains",
                 span="Alice in Chains was part of the Seattle grunge scene."),
        document(26, value="Manchester scene",
                 span="Nirvana was part of the Manchester scene."),
        document(27, subject="artist:alice_in_chains", value="Manchester scene",
                 span="Alice in Chains was part of the Manchester scene."),
    ]
    result = expand(docs)
    assert len(result.relations) == 4
    assert all(item.relation_type is SceneRelationType.BELONGS_TO_SCENE for item in result.relations)


def test_conflicting_direct_artist_pair_scene_names_are_rejected() -> None:
    docs = [
        document(30, predicate="scene_association", value=None,
                 target="artist:alice_in_chains", scene_name=SCENE_NAME,
                 span="Nirvana and Alice in Chains both belonged to the Seattle grunge scene."),
        document(31, predicate="scene_association", value=None,
                 target="artist:alice_in_chains", scene_name="Manchester scene",
                 span="Nirvana and Alice in Chains both belonged to the Manchester scene."),
    ]
    result = expand(docs)
    assert result.relations == []
    assert result.trace.rejection_reasons["scene_identity_conflict"] == 2


def test_cross_region_and_approximate_periods_remain_separate() -> None:
    docs = [
        document(7, time={"year": 1992, "raw": "around 1992"},
                 span="Nirvana was part of the Seattle grunge scene around 1992."),
        document(8, value="Manchester scene", time={"year": 1989},
                 span="Nirvana was part of the Manchester scene in 1989."),
    ]
    result = expand(docs)
    assert {item.scene.node_id for item in result.relations} == {
        "scene:seattle_grunge_scene", "scene:manchester_scene",
    }
    approximate = next(item for item in result.relations if item.scene.canonical_name == SCENE_NAME)
    assert approximate.temporal_precision is EraTemporalPrecision.APPROXIMATE
    assert approximate.evidence[0].raw_time == "around 1992"


def test_non_ascii_scene_names_keep_distinct_stable_ids() -> None:
    docs = [
        document(20, value="上海 indie scene",
                 span="Nirvana was part of the 上海 indie scene."),
        document(21, value="北京 indie scene",
                 span="Nirvana was part of the 北京 indie scene."),
    ]
    first = expand(docs)
    second = expand(docs)
    ids = {relation.scene.node_id for relation in first.relations}
    assert len(ids) == 2
    assert ids == {relation.scene.node_id for relation in second.relations}


def test_conflicting_years_and_locations_keep_provenance() -> None:
    docs = [
        document(9, time={"year": 1991}, location="Seattle",
                 span="Nirvana was part of the Seattle grunge scene in Seattle in 1991."),
        document(10, time={"year": 1993}, location="Tacoma",
                 span="Nirvana was part of the Seattle grunge scene in Tacoma in 1993."),
    ]
    result = expand(docs)
    assert len(result.relations) == 1
    relation = result.relations[0]
    assert relation.location is None
    assert relation.start_year is None and relation.end_year is None
    assert relation.temporal_precision is EraTemporalPrecision.UNKNOWN
    assert relation.confidence < 0.96
    assert relation.metadata["temporal_conflict"] is True
    assert relation.metadata["location_conflict"] is True
    assert {item["location"] for item in relation.metadata["location_assertions"]} == {"Seattle", "Tacoma"}
    assert len(relation.evidence) == 2


@pytest.mark.parametrize("span", [
    "Nirvana performed in Seattle in 1992.",
    "Nirvana played grunge music in Seattle in 1992.",
    "Nirvana was active in 1992.",
    "Nirvana and the Seattle grunge scene were listed on the same page.",
    "Nirvana was loosely associated with the Seattle grunge scene.",
    "Nirvana was not part of the Seattle grunge scene in 1992.",
])
def test_location_genre_year_co_mention_and_negation_do_not_make_scene(span: str) -> None:
    result = expand([document(11, span=span, time={"year": 1992})])
    assert result.relations == []
    assert result.graph == graph()
    assert "scene_not_in_evidence_span" in result.rejected[0].rejection_reasons


def test_missing_or_insufficient_evidence_and_ordinary_query_do_not_expand() -> None:
    valid = document(12)
    no_claim = expand([document(13, supported=False)])
    insufficient = expand([document(14, sufficient=False)])
    ordinary = expand([valid], query="探索 Nirvana 的世界线")
    era = expand([valid], query="探索 Nirvana 的 1990 年代音乐时期")
    assert no_claim.relations == []
    assert insufficient.relations == []
    assert "insufficient_context" in insufficient.rejected[0].rejection_reasons
    assert ordinary.graph == graph() and ordinary.relations == []
    assert era.graph == graph() and era.relations == []
    assert not is_explicit_scene_query("探索 Nirvana 的 1990 年代音乐时期")
    assert not is_explicit_scene_query("探索歌曲《Scene》的世界线")
    genre_claim = expand([document(
        23, predicate="has_genre", value="Grunge",
        span="Nirvana played grunge music in Seattle.",
    )])
    assert genre_claim.relations == []


def test_unverified_location_and_tampered_time_are_rejected() -> None:
    missing_location = expand([document(15, location="Portland")])
    assert missing_location.relations == []
    assert "location_not_in_evidence_span" in missing_location.rejected[0].rejection_reasons
    doc = document(16, time={"year": 1992})
    service = SceneExpansionService()
    candidate = service.extractor.extract(plan(), graph(), [doc])[0]
    tampered = candidate.model_copy(update={"start_year": 1994, "end_year": 1994})
    relations, decisions = service.validator.validate(graph(), [doc], [tampered])
    assert relations == []
    assert "time_not_in_evidence_span" in decisions[0].rejection_reasons


def test_capacity_skip_keeps_base_graph() -> None:
    full = WorldLineGraph(
        root=ARTIST,
        nodes=[
            graph().nodes[0],
            *[
                WorldLineNode(id=f"netease:song:{index}", type=WorldLineNodeType.SONG, name=f"作品 {index}")
                for index in range(10, 24)
            ],
        ],
    )
    result = SceneExpansionService().expand(
        "Nirvana 的音乐场景", plan(), full, sources(), [document(17)],
    )
    assert result.graph == full
    assert result.relations == []
    assert result.trace.rejection_reasons == {"capacity_reached": 1}


def test_session_relation_merge_preserves_evidence_and_old_payload_compatibility() -> None:
    first = expand([document(18)]).relations
    old = deepcopy(first)
    second = expand([document(19, span="In 1992 Nirvana was part of the Seattle grunge scene.", time={"year": 1992})]).relations
    merged = merge_scene_relations(first, second)
    assert len(merged) == 1
    assert len(merged[0].evidence) == 2
    assert first == old

    from app.agent.foundation.subgraphs.worldline.models import EntityType as CatalogType, ResolvedEntity

    payload = {
        "root_entity": ResolvedEntity(
            entity_id=ARTIST, entity_type=CatalogType.ARTIST,
            name="Nirvana", source="netease",
        ).model_dump(mode="json"),
        "graph": graph().model_dump(mode="json"),
        "narrative": "基于目录事实。", "branches": [],
        "sources": [item.model_dump(mode="json") for item in sources()],
        "citations": [],
    }
    assert WorldLineSessionResult.model_validate(payload).scene_relations == []


def test_scene_dto_rejects_missing_graph_edge_and_uncontrolled_relation() -> None:
    from app.agent.foundation.subgraphs.worldline.models import EntityType as CatalogType, ResolvedEntity

    expanded = expand([document(32)])
    payload = {
        "root_entity": ResolvedEntity(
            entity_id=ARTIST, entity_type=CatalogType.ARTIST,
            name="Nirvana", source="netease",
        ).model_dump(mode="json"),
        "graph": expanded.graph.model_dump(mode="json"),
        "narrative": "基于证据。", "branches": [],
        "sources": [item.model_dump(mode="json") for item in expanded.sources],
        "citations": [],
        "scene_relations": [item.model_dump(mode="json") for item in expanded.relations],
    }
    without_edge = deepcopy(payload)
    without_edge["graph"]["edges"] = []
    with pytest.raises(ValueError, match="relation must reference result graph edge"):
        WorldLineSessionResult.model_validate(without_edge)

    uncontrolled = deepcopy(payload)
    uncontrolled["scene_relations"][0]["relation_type"] = "similar_artist"
    with pytest.raises(ValueError):
        WorldLineSessionResult.model_validate(uncontrolled)
