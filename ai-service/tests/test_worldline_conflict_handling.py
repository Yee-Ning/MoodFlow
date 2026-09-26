"""P0 conflict boundaries using independent, differently named examples."""

from copy import deepcopy
from hashlib import sha256

import pytest

from tests import test_worldline_era_expansion as era
from tests import test_worldline_influence_expansion as influence
from tests import test_worldline_scene_expansion as scene
from app.agent.foundation.subgraphs.worldline.era_expansion import merge_era_relations
from app.agent.foundation.subgraphs.worldline.influence_expansion import merge_influence_relations
from app.agent.foundation.subgraphs.worldline.scene_expansion import merge_scene_relations
from app.agent.foundation.subgraphs.worldline.models import ResolvedEntity, WorldLineSessionResult
from app.agent.foundation.subgraphs.worldline.merge import WorldLineMergeError


def release(index, year, predicate="belongs_to_era", *, entity="album:test_album", name="测试专辑"):
    return era.document(
        index, predicate=predicate, subject=entity,
        span=f"{name} was released in {year}.",
        value=f"{year // 10 * 10}s" if predicate != "released" else None,
        time={"year": year},
    )


@pytest.mark.parametrize("predicates", [
    ("released", "released"), ("belongs_to_era", "belongs_to_era"),
    ("released", "belongs_to_era"),
])
def test_release_conflict_crosses_decades_without_choosing_a_year(predicates):
    docs = [release(101, 1989, predicates[0]), release(102, 1990, predicates[1])]
    result = era.expand(docs)
    assert {r.era.node_id for r in result.relations} == {"era:1980s", "era:1990s"}
    claims = {d.metadata["supported_claims"][0]["claim_id"] for d in docs}
    for relation in result.relations:
        assert relation.metadata["temporal_conflict"] is True
        assert (relation.start_year, relation.end_year, relation.temporal_precision.value) == (None, None, "unknown")
        assert {e.claim_id for e in relation.evidence} == claims
        assert {e.exact_span for e in relation.evidence} == {d.metadata["supported_claims"][0]["evidence_span"] for d in docs}
    assert len(result.graph.edges) == 2
    assert all(set(edge.evidence_ids) == {s.id for s in result.sources if s.metadata.get("claim_id") in claims}
               for edge in result.graph.edges)
    assert len(result.trace.aggregation_trace[0]["outputs"]) == 2
    merged = merge_era_relations(result.relations, result.relations)
    assert all(r.metadata["temporal_conflict"] and r.start_year is None for r in merged)


def test_release_conflict_does_not_merge_different_works_or_artist_activity():
    result = era.expand([
        release(110, 1989), release(111, 1990, entity="song:test_song", name="测试单曲"),
    ])
    assert len(result.relations) == 2
    assert all(not r.metadata["temporal_conflict"] for r in result.relations)
    result = era.expand([
        release(112, 1989, "released", entity="artist:test_artist", name="测试歌手").model_copy(update={
            "metadata": {**release(112, 1989, "released").metadata, "supported_claims": [{
                **release(112, 1989, "released", entity="artist:test_artist", name="测试歌手").metadata["supported_claims"][0],
                "object_entity_id": "album:test_album",
            }]},
        }),
        release(113, 1990, "released", entity="artist:test_artist", name="测试歌手").model_copy(update={
            "metadata": {**release(113, 1990, "released").metadata, "supported_claims": [{
                **release(113, 1990, "released", entity="artist:test_artist", name="测试歌手").metadata["supported_claims"][0],
                "object_entity_id": "song:test_song",
            }]},
        }),
    ])
    assert len(result.relations) == 2
    assert all(not r.metadata["temporal_conflict"] for r in result.relations)


@pytest.mark.parametrize("span,value,year", [
    ("测试专辑 was recorded in 1989 and released in 1990.", "1980s", 1989),
    ("测试专辑 was released in 1989.", "1990s", 1989),
    ("测试专辑 was released in 1989.", "1980s", 1990),
    ("Other Album was released in 1989.", "1980s", 1989),
    ("测试专辑 was not released in 1989.", "1980s", 1989),
])
def test_normalized_release_requires_work_release_date_and_correct_decade(span, value, year):
    assert not era.expand([era.document(
        120, predicate="belongs_to_era", subject="album:test_album",
        span=span, value=value, time={"year": year},
    )]).relations


def scene_pair():
    support = scene.document(201, value="Neon movement", span="Nirvana was one of the frontrunners of Neon and survived the scene.")
    denial = scene.document(202, value="Neon movement", span="We never really did ‘Neon.’ We’re not part of that.")
    denial = denial.model_copy(update={"title": "Nirvana: ‘We make our own choices’"})
    return support, denial


def test_scene_alias_and_named_first_person_denial_are_preserved_as_conflict():
    docs = scene_pair()
    result = scene.expand(list(docs))
    assert len(result.relations) == 1
    relation = result.relations[0]
    assert relation.scene.node_id == "scene:neon_movement"
    assert relation.metadata["attribution_conflict"] is True
    assert {p["stance"] for p in relation.metadata["provenance"]} == {"support", "contradiction"}
    assert {e.claim_id for e in relation.evidence} == {d.metadata["supported_claims"][0]["claim_id"] for d in docs}
    assert all(e.exact_span in d.content for e, d in zip(relation.evidence, docs))
    assert result.trace.attribution_conflict_count == 1
    assert merge_scene_relations(result.relations, result.relations)[0].metadata["attribution_conflict"] is True
    restored = WorldLineSessionResult.model_validate_json(WorldLineSessionResult(
        graph=result.graph, sources=result.sources, scene_relations=result.relations,
        root_entity=ResolvedEntity(entity_id=scene.ARTIST, entity_type="artist", name="Nirvana", source="test"),
        narrative="Source conflict retained.",
    ).model_dump_json())
    assert restored.scene_relations == result.relations


def test_different_claim_scene_aliases_normalize_only_after_validation():
    support, _ = scene_pair()
    denial = scene.document(205, value="Neon", span="Nirvana was not part of Neon.")
    result = scene.expand([support, denial])
    assert len(result.relations) == 1
    assert result.relations[0].scene.node_id == "scene:neon_movement"
    assert result.relations[0].metadata["attribution_conflict"]
    assert len(result.relations[0].evidence) == 2


@pytest.mark.parametrize("title", ["Nirvana concert history", "Other Band: ‘We disagree’", "Archive interview"])
def test_scene_unbound_pronoun_cannot_supply_opposition(title):
    support, denial = scene_pair()
    result = scene.expand([support, denial.model_copy(update={"title": title})])
    assert len(result.relations) == 1
    assert not result.relations[0].metadata["attribution_conflict"]
    assert len(result.relations[0].evidence) == 1


def test_scene_denial_alone_or_different_scope_does_not_assert_membership():
    _, denial = scene_pair()
    assert not scene.expand([denial]).relations
    different = scene.document(203, value="Neon movement", span="We are not part of Folk, but we play alongside Neon.").model_copy(update={"title": denial.title})
    result = scene.expand([scene_pair()[0], different])
    assert not result.relations[0].metadata["attribution_conflict"]


@pytest.mark.parametrize("span", [
    "Nirvana interviewed Alice in Chains, a frontrunner of Neon, about the scene.",
    "Nirvana interviewed Alice in Chains, who was not part of Neon.",
])
def test_scene_alias_cooccurrence_cannot_reassign_another_artists_stance(span):
    candidate = scene.document(206, value="Neon movement", span=span)
    result = scene.expand([candidate])
    assert result.relations == []
    if "not part" in span:
        result = scene.expand([scene_pair()[0], candidate])
        assert not result.relations[0].metadata["attribution_conflict"]


def test_conflicting_scene_membership_does_not_derive_certain_artist_association():
    support, denial = scene_pair()
    other = scene.document(204, subject="artist:alice_in_chains", value="Neon movement", span="Alice in Chains was part of the Neon movement.")
    result = scene.expand([support, denial, other])
    assert len(result.relations) == 2
    assert all(r.relation_type.value == "belongs_to_scene" for r in result.relations)


def influence_pair(*, with_context=True):
    report = "tried to translate their spirit, along with jazz and A, into radical electronica."
    support = influence.document(301, report).model_copy(update={"content": f"B {report}" if with_context else report})
    reply = "Rowan Vale: direi di no, A era troppo intellettuale per i nostri gusti."
    denial = influence.document(302, reply).model_copy(update={
        "title": "B: interview with Rowan Vale", "content": f"Did A influence your music? {reply}" if with_context else reply,
    })
    return support, denial


def test_contextual_list_and_interview_conflict_preserve_direction_and_original_spans():
    docs = influence_pair()
    result = influence.expand(list(docs))
    assert len(result.relations) == 1
    relation = result.relations[0]
    assert (relation.source_entity.node_id, relation.target_entity.node_id) == ("netease:artist:1", "netease:artist:2")
    assert relation.metadata["attribution_conflict"]
    assert {e.stance.value for e in relation.evidence} == {"support", "contradiction"}
    for source, doc in zip(result.sources, docs):
        assert source.excerpt == doc.metadata["supported_claims"][0]["evidence_span"]
        assert source.metadata["context_document_sha256"] == sha256(doc.content.encode()).hexdigest()
    assert merge_influence_relations(result.relations, result.relations)[0].metadata["attribution_conflict"]


def test_adjacent_sentence_actor_can_bind_a_list_but_joint_actor_cannot():
    support, denial = influence_pair()
    report = support.metadata["supported_claims"][0]["evidence_span"]
    support = support.model_copy(update={"content": f"B made experimental music. They {report}"})
    result = influence.expand([support, denial])
    assert len(result.relations) == 1 and result.relations[0].metadata["attribution_conflict"]
    ambiguous = support.model_copy(update={"content": f"B and A made experimental music. They {report}"})
    assert influence.expand([ambiguous, denial]).relations == []


@pytest.mark.parametrize("mutation", ["missing_context", "reverse_claims", "wrong_speaker", "wrong_question", "insufficient", "weak", "cooccurrence"])
def test_influence_conflict_never_bypasses_context_direction_or_strength(mutation):
    support, denial = influence_pair(with_context=mutation != "missing_context")
    if mutation == "reverse_claims":
        modified = []
        for doc in [support, denial]:
            metadata = deepcopy(doc.metadata)
            claim = metadata["supported_claims"][0]
            claim["subject_entity_id"], claim["object_entity_id"] = claim["object_entity_id"], claim["subject_entity_id"]
            modified.append(doc.model_copy(update={"metadata": metadata}))
        support, denial = modified
    elif mutation == "wrong_speaker":
        denial = denial.model_copy(update={"title": "B: interview with Morgan Lane"})
    elif mutation == "wrong_question":
        denial = denial.model_copy(update={"content": denial.content.replace("Did A influence your music?", "Did A join the scene?")})
    elif mutation == "insufficient":
        support = support.model_copy(update={"metadata": {**support.metadata, "sufficiency_status": "insufficient"}})
    elif mutation in {"weak", "cooccurrence"}:
        text = "B might translate jazz and A into music." if mutation == "weak" else "B and A were in the same scene."
        support = influence.document(301, text)
    result = influence.expand([support, denial])
    if mutation in {"wrong_speaker", "wrong_question"}:
        assert len(result.relations) == 1
        assert not result.relations[0].metadata["attribution_conflict"]
        assert len(result.relations[0].evidence) == 1
    else:
        assert result.relations == [] and result.graph.edges == []


@pytest.mark.parametrize("family", ["scene", "influence"])
def test_evidence_cap_never_silently_discards_opposition(family):
    if family == "scene":
        support, denial = scene_pair()
        positives = [scene.document(210 + i, value="Neon movement", span=support.metadata["supported_claims"][0]["evidence_span"]) for i in range(9)]
        result = scene.expand([*positives, denial])
        relation = result.relations[0]
        assert {p["stance"] for p in relation.metadata["provenance"]} == {"support", "contradiction"}
    else:
        positives = [influence.document(310 + i, "A influenced B.") for i in range(9)]
        denial = influence.document(319, "A did not influence B.")
        result = influence.expand([*positives, denial])
        relation = result.relations[0]
        assert {e.stance.value for e in relation.evidence} == {"support", "contradiction"}
    assert relation.metadata["attribution_conflict"]
    assert len(relation.evidence) == 8 and relation.metadata["evidence_overflow_count"] == 2


@pytest.mark.parametrize("family", ["era", "scene", "influence"])
def test_positive_to_conflict_continuation_preserves_sources_and_disputed_graph_text(family):
    if family == "era":
        from app.agent.foundation.subgraphs.worldline.era_expansion import EraExpansionService
        service, graph, plan = EraExpansionService(), era.graph(), era.plan()
        query, docs, flag = "音乐年代来源冲突", [release(401, 1989), release(402, 1990)], "temporal_conflict"
    elif family == "scene":
        from app.agent.foundation.subgraphs.worldline.scene_expansion import SceneExpansionService
        service, graph, plan = SceneExpansionService(), scene.graph(), scene.plan()
        query, docs, flag = "音乐场景来源冲突", list(scene_pair()), "attribution_conflict"
    else:
        from app.agent.foundation.subgraphs.worldline.influence_expansion import InfluenceExpansionService
        service, graph, plan = InfluenceExpansionService(), influence.graph(), influence.plan()
        query, docs, flag = "音乐影响归因冲突", [influence.document(401, "A influenced B."), influence.document(402, "A did not influence B.")], "attribution_conflict"
    first = service.expand(query, plan, graph, [], docs[:1])
    assert first.relations and all(not r.metadata.get(flag) for r in first.relations)
    second = service.expand(query, plan, first.graph, first.sources, docs)
    assert second.relations and all(r.metadata.get(flag) for r in second.relations)
    assert all("冲突" in e.description or "分歧" in e.description for e in second.graph.edges)
    assert all(source in second.sources for source in first.sources)
    third = service.expand(query, plan, second.graph, second.sources, docs)
    assert third.graph == second.graph and third.relations == second.relations
    merging = {"era": merge_era_relations, "scene": merge_scene_relations, "influence": merge_influence_relations}[family]
    assert merging(second.relations, second.relations) == second.relations


def test_conflict_continuation_still_rejects_changed_source_identity():
    from app.agent.foundation.subgraphs.worldline.era_expansion import EraExpansionService
    doc = release(410, 1989)
    first = era.expand([doc])
    with pytest.raises(WorldLineMergeError, match="source_conflict"):
        EraExpansionService().expand("音乐年代", era.plan(), first.graph, first.sources, [
            doc.model_copy(update={"title": "Changed source identity"}), release(411, 1990),
        ])
