"""Explorer paths, source trust, real captured catalog IDs and handoff parsing."""

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from pydantic import ValidationError

from app.agent.foundation.models import TaskResult, TaskStatus
from app.agent.foundation.routing import RouteKey, RoutingContext
from app.agent.foundation.subgraphs.tool_calling.client_tools import play_song_tool
from app.agent.foundation.subgraphs.worldline.builder import build_worldline_graph
from app.agent.foundation.subgraphs.worldline.graph import create_worldline_subgraph
from app.agent.foundation.subgraphs.worldline.models import (
    EntityType,
    EvidenceSource,
    EvidenceType,
    MusicEntityFacts,
    MusicFact,
    ResolvedEntity,
    WorldLineEdge,
    WorldLineGraph,
    WorldLineNode,
    WorldLineNodeType,
    WorldLineResult,
)
from app.agent.foundation.subgraphs.worldline.era_expansion import EraExpansionService
from app.agent.foundation.subgraphs.worldline.scene_expansion import (
    SceneExpansionService,
)
from app.agent.foundation.subgraphs.worldline.influence_expansion import (
    InfluenceExpansionService,
)
from app.agent.foundation.subgraphs.worldline.semantic_expansion import (
    SemanticExpansionService,
)
from app.agent.handoff.agents import choose_transfer
from app.agent.handoff.contracts import HandoffEnvelope
from app.agent.handoff.explorer import execute_exploration, journey_query
from app.agent.handoff.journey import (
    EvidenceState,
    JourneyQueueItem,
    JourneyTransferPayload,
    attach_journey_transfer,
    build_listening_journey,
    render_journey,
)
from app.agent.handoff.registry import AgentKey
from app.agent.handoff.state import create_handoff_state, record_task_result
from app.agent.handoff.tools import execute_transfer
from app.schemas.music import MusicTrack
from tests.test_handoff_contracts import START, make_run
from tests.test_handoff_runtime import ToolModel, call
from tests.test_worldline_graph import (
    EmptyRetriever,
    EraDocsRetriever,
    FakeGenerator,
    InfluenceDocsRetriever,
    SceneDocsRetriever,
    SemanticDocsRetriever,
)


def captured_facts():
    # Reuse already committed real Java catalog evidence, not guessed IDs or
    # mutable current search results. Knowledge/time fixtures below are mocks.
    path = Path(__file__).with_name("fixtures") / "explorer_catalog_worldline.json"
    world = WorldLineResult.model_validate(
        json.loads(path.read_text(encoding="utf-8"))
    )
    nodes = [item for item in world.graph.nodes if item.type is WorldLineNodeType.SONG][
        :2
    ]
    years = [
        2004,
        2000,
    ]  # deliberately reversed fixture timestamps, not real release claims
    songs = [
        ResolvedEntity(
            entity_id=node.id,
            entity_type=EntityType.SONG,
            name=node.name,
            artist="周杰伦",
            source="netease",
            metadata={
                "publish_time": int(
                    datetime(year, 1, 1, tzinfo=timezone.utc).timestamp() * 1000
                )
            },
        )
        for node, year in zip(nodes, years, strict=True)
    ]
    source = next(
        item for item in world.sources if item.source_type is EvidenceType.MUSIC_API
    )
    return MusicEntityFacts(
        entity=world.root_entity,
        related_entities=songs,
        sources=[source],
        facts=[
            MusicFact(
                id=f"fact:{index}",
                subject_id=song.entity_id,
                object_entity_id=world.root_entity.entity_id,
                predicate="performed_by",
                value=f"《{song.name}》由周杰伦演唱",
                evidence_ids=[source.id],
            )
            for index, song in enumerate(songs)
        ],
    )


def world_from_facts(facts, *, documents=()):
    graph, sources = build_worldline_graph(facts, list(documents))
    root = facts.entity
    from app.agent.foundation.subgraphs.worldline.models import (
        WorldLineBranch,
        WorldLineBranchTarget,
    )

    return WorldLineResult(
        root_entity=root,
        graph=graph,
        sources=sources,
        narrative="仅使用来源记录的事实。",
        branches=[
            WorldLineBranch(
                id=f"b{index}",
                title="探索根实体",
                description="受控分支。",
                branch_type="artist",
                target_entity=WorldLineBranchTarget(
                    id=root.entity_id, type="artist", name=root.name
                ),
            )
            for index in range(3)
        ],
    )


def knowledge_world():
    facts = captured_facts()
    world = world_from_facts(facts)
    source = EvidenceSource(
        id="knowledge:fixture",
        source_type=EvidenceType.KNOWLEDGE,
        title="受控风格与时代证据",
        source="existing-retriever-fixture",
        excerpt="受控资料确认根歌手与风格节点、时代节点的关联。",
    )
    knowledge_nodes = [
        WorldLineNode(id="genre:fixture", type="genre", name="受控风格"),
        WorldLineNode(id="era:fixture", type="era", name="受控时代"),
        WorldLineNode(id="orphan:fixture", type="scene", name="孤立场景"),
    ]
    graph = WorldLineGraph(
        root=world.graph.root,
        nodes=[*world.graph.nodes, *knowledge_nodes],
        edges=[
            *world.graph.edges,
            WorldLineEdge(
                source=world.graph.root,
                target="genre:fixture",
                relation="belongs_to_genre",
                description="目录歌手与受控风格有证据关联",
                evidence_ids=[source.id],
            ),
            WorldLineEdge(
                source=world.graph.root,
                target="era:fixture",
                relation="belongs_to_era",
                description="受控时代关联",
                evidence_ids=[source.id],
            ),
            WorldLineEdge(
                source="era:fixture",
                target=facts.related_entities[1].entity_id,
                relation="representative_of_era",
                description="资料明确标记的代表歌曲",
                evidence_ids=[source.id],
            ),
        ],
    )
    return world.model_copy(
        update={"graph": graph, "sources": [*world.sources, source]}
    ), facts


def test_captured_song_ids_are_selected_from_catalog_not_guessed_or_generated():
    facts = captured_facts()
    journey = build_listening_journey(world_from_facts(facts), facts)
    actual = {
        int(item.entity_id.split(":")[-1]): item.name for item in facts.related_entities
    }
    assert actual == {210049: "布拉格广场", 5257138: "屋顶"}
    assert all(
        node.song_id in actual and node.song_title == actual[node.song_id]
        for node in journey.nodes
    )
    assert (
        journey.nodes[0].song_id == 210049
    )  # trusted catalog order for artist representative


def test_timeline_root_prelude_then_ascending_dates_with_sources_and_narrative():
    facts = captured_facts()
    journey = build_listening_journey(world_from_facts(facts), facts)
    assert journey.nodes[0].node_id == facts.entity.entity_id
    assert [node.time_year for node in journey.nodes[1:]] == [2000, 2004]
    assert all(
        node.narration.source_ids
        and node.narration.statement
        and node.narration.transition
        and node.narrative
        and node.narration.effect_state is None
        for node in journey.nodes
    )
    assert journey.evidence_state is EvidenceState.CATALOG
    assert "知识证据缺失" in render_journey(journey) and "未执行播放" in render_journey(
        journey
    )


def test_knowledge_only_explicit_representative_and_unreachable_node_exclusion():
    world, facts = knowledge_world()
    journey = build_listening_journey(world, facts)
    by_id = {node.node_id: node for node in journey.nodes}
    assert by_id["genre:fixture"].song_id is None
    assert (
        by_id["genre:fixture"].song_title is None
        and by_id["genre:fixture"].node_kind == "knowledge_only"
    )
    assert by_id["era:fixture"].song_id == 5257138
    assert by_id["era:fixture"].representative_reason == "来源明确标记的代表歌曲"
    assert by_id["genre:fixture"].narration.evidence_state is EvidenceState.KNOWLEDGE
    assert "orphan:fixture" in journey.excluded_node_ids
    assert "纯知识节点，只讲解不播放" in render_journey(journey)


@pytest.mark.parametrize(
    "metadata",
    [
        {"song_id": 99999, "verified": True},
        {"year": 1980},
        {"evidence_ids": ["knowledge:fixture"]},
    ],
)
def test_graph_metadata_cannot_create_catalog_identity_or_unbacked_time(metadata):
    world, facts = knowledge_world()
    nodes = [
        node.model_copy(update={"metadata": metadata})
        if node.id == "genre:fixture"
        else node
        for node in world.graph.nodes
    ]
    journey = build_listening_journey(
        world.model_copy(
            update={"graph": world.graph.model_copy(update={"nodes": nodes})}
        ),
        facts,
    )
    node = next(item for item in journey.nodes if item.node_id == "genre:fixture")
    assert node.song_id is None and node.time_year is None


def test_knowledge_song_id_shaped_like_catalog_is_not_playable_without_private_catalog_record():
    world, facts = knowledge_world()
    fake = WorldLineNode(
        id="netease:song:999999",
        type="song",
        name="伪造歌曲",
        metadata={"verified": True},
    )
    edge = WorldLineEdge(
        source=world.graph.root,
        target=fake.id,
        relation="related_to",
        evidence_ids=["knowledge:fixture"],
    )
    graph = world.graph.model_copy(
        update={
            "nodes": [*world.graph.nodes, fake],
            "edges": [*world.graph.edges, edge],
        }
    )
    journey = build_listening_journey(world.model_copy(update={"graph": graph}), facts)
    node = next(item for item in journey.nodes if item.node_id == fake.id)
    assert node.node_kind == "knowledge_only" and node.song_id is None


def test_missing_source_and_unsupported_catalog_edge_do_not_enter_path():
    world, facts = knowledge_world()
    graph = world.graph.model_copy(
        update={
            "edges": [
                *[
                    edge
                    for edge in world.graph.edges
                    if "genre:fixture" not in (edge.source, edge.target)
                ],
                WorldLineEdge(
                    source=world.graph.root,
                    target="genre:fixture",
                    relation="belongs_to_genre",
                    evidence_ids=["absent"],
                ),
                WorldLineEdge(
                    source=world.graph.root,
                    target="orphan:fixture",
                    relation="belongs_to_scene",
                    evidence_ids=[facts.sources[0].id],
                ),
            ]
        }
    )
    journey = build_listening_journey(world.model_copy(update={"graph": graph}), facts)
    assert {"genre:fixture", "orphan:fixture"}.issubset(journey.excluded_node_ids)


def test_no_sources_gives_unverified_empty_path_without_fake_root_or_song():
    world = world_from_facts(captured_facts()).model_copy(update={"sources": []})
    journey = build_listening_journey(world, None)
    assert journey.evidence_state is EvidenceState.UNVERIFIED and not journey.nodes
    assert not journey.transfer_payload().journey_queue
    assert "没有可核实来源" in render_journey(journey)


@pytest.mark.parametrize("song_id", [0, -1, True, "210049"])
def test_queue_rejects_invalid_or_coerced_song_ids(song_id):
    with pytest.raises(ValidationError):
        JourneyQueueItem(
            node_id="x",
            node_title="节点",
            narrative="事实",
            song_id=song_id,
            song_title="歌曲",
        )


def test_queue_is_unique_and_keeps_null_song_title_for_knowledge_node():
    item = JourneyQueueItem(node_id="x", node_title="节点", narrative="事实")
    assert item.song_id is None and item.song_title is None
    with pytest.raises(ValidationError):
        JourneyQueueItem(
            node_id="x", node_title="节点", narrative="事实", song_title="没有 ID"
        )
    with pytest.raises(ValidationError):
        JourneyTransferPayload(journey_queue=(item, item))


def test_payload_budget_retains_valid_prefix_and_reports_omissions():
    world, facts = knowledge_world()
    # Long legitimate titles stress the actual existing 2000-character envelope.
    graph = world.graph.model_copy(
        update={
            "nodes": [
                node.model_copy(update={"name": "长" * 500})
                if node.id.startswith(("genre:", "era:"))
                else node
                for node in world.graph.nodes
            ]
        }
    )
    journey = build_listening_journey(world.model_copy(update={"graph": graph}), facts)
    assert (
        journey.omitted_node_ids
        and len(journey.transfer_payload().to_summary()) <= 2000
    )
    payload = JourneyTransferPayload.model_validate_json(
        journey.transfer_payload().to_summary()
    )
    assert len(payload.journey_queue) == len(journey.nodes)


class CapturedMusic:
    def __init__(self, *, empty=False):
        self.facts = captured_facts()
        self.empty = empty
        self.searches = []
        self.loads = []

    async def search_entities(self, query, expected_entity_type, *, limit=5):
        self.searches.append((query, expected_entity_type))
        return [] if self.empty else [self.facts.entity]

    async def load_entity_facts(self, entity):
        self.loads.append(entity.entity_id)
        return self.facts


class AllKnowledge:
    async def retrieve(self, entity, plan):
        result = []
        for retriever in (
            EraDocsRetriever(),
            SceneDocsRetriever(),
            InfluenceDocsRetriever(),
            SemanticDocsRetriever(),
        ):
            result.extend(await retriever.retrieve(entity, plan))
        # An existing knowledge identity without a playable catalog ID is valid
        # for influence discussion; it must never be invented as a song number.
        result[2] = result[2].model_copy(
            update={
                "metadata": {
                    **result[2].metadata,
                    "graph_nodes": [
                        {
                            "id": "knowledge:qi_li_xiang",
                            "type": "song",
                            "name": "七里香",
                            "metadata": {
                                "entity_reference": {
                                    "knowledge_entity_id": "song:qi_li_xiang"
                                }
                            },
                        }
                    ],
                }
            }
        )
        return result


async def explore_with(graph, run=None):
    run = run or make_run(
        RouteKey.WORLDLINE, entry=AgentKey.EXPLORER, texts=["探索周杰伦的音乐世界线"]
    )
    view = create_handoff_state(run, routing_context=RoutingContext())
    return await execute_exploration(
        view, run, SimpleNamespace(context=None), graph=graph, context_tool=None
    )


@pytest.mark.asyncio
async def test_programmatic_worldline_expansions_reuse_catalog_and_emit_ordered_path():
    music = CapturedMusic()
    era, scene, influence, semantic = (
        EraExpansionService(),
        SceneExpansionService(),
        InfluenceExpansionService(),
        SemanticExpansionService(),
    )
    for service, method in (
        (era, "expand"),
        (scene, "expand"),
        (influence, "expand"),
        (semantic, "expand_artist_genres"),
    ):
        setattr(service, method, Mock(wraps=getattr(service, method)))
    graph = create_worldline_subgraph(
        music_service=music,
        retriever=AllKnowledge(),
        generator=FakeGenerator(grounded=True),
        era_expansion=era,
        scene_expansion=scene,
        influence_expansion=influence,
        semantic_expansion=semantic,
    )
    result = await explore_with(graph)
    assert result.status is TaskStatus.SUCCESS
    assert len(music.searches) == 1 and music.searches[0][0] == "周杰伦"
    assert music.loads == ["netease:artist:6452"]
    assert all(
        getattr(service, method).call_count == 1
        for service, method in (
            (era, "expand"),
            (scene, "expand"),
            (influence, "expand"),
            (semantic, "expand_artist_genres"),
        )
    )
    assert era.expand.call_args.args[0] == journey_query("探索周杰伦的音乐世界线")
    world = WorldLineResult.model_validate(result.data["worldline_result"])
    assert world.era_relations and world.scene_relations and world.influence_relations
    assert any(node.type is WorldLineNodeType.GENRE for node in world.graph.nodes)
    nodes = result.data["listening_journey"]["nodes"]
    assert all(node["narration"]["source_ids"] and node["narrative"] for node in nodes)
    assert result.data["journey_queue"] and "聆听路径" in result.content


@pytest.mark.asyncio
async def test_unknown_entity_returns_honest_clarification_without_path():
    music = CapturedMusic(empty=True)
    graph = create_worldline_subgraph(
        music_service=music,
        retriever=EmptyRetriever(),
        generator=FakeGenerator(grounded=True),
    )
    run = make_run(
        RouteKey.WORLDLINE,
        entry=AgentKey.EXPLORER,
        texts=["探索歌手《未存在的实体》的音乐世界线"],
    )
    result = await explore_with(graph, run)
    assert result.status is TaskStatus.NEEDS_INPUT and music.loads == []
    assert "worldline_result" not in result.data and not result.data.get(
        "journey_queue"
    )
    assert "没有找到" in result.content


@pytest.mark.asyncio
async def test_real_transfer_envelope_queue_is_parsed_by_mock_curator_and_existing_play_tool():
    music = CapturedMusic()
    graph = create_worldline_subgraph(
        music_service=music,
        retriever=EmptyRetriever(),
        generator=FakeGenerator(grounded=True),
    )
    run = make_run(
        RouteKey.WORLDLINE,
        RouteKey.TOOL_CALLING,
        entry=AgentKey.EXPLORER,
        texts=["探索周杰伦的音乐世界线", "播放路径中的代表歌曲"],
    )
    result = await explore_with(graph, run)
    assert result.data["journey_queue"], result.data["listening_journey"]
    run = record_task_result(run, result)
    state = create_handoff_state(run, routing_context=RoutingContext())
    model = ToolModel(responses=[call()])
    message = await choose_transfer(state, run, model=model)
    assert message.tool_calls[0]["args"]["context_summary"].startswith("{"), (
        run.current_agent,
        attach_journey_transfer(message, run).tool_calls,
    )
    assert model.bound_names == [["transfer_to_curator_agent"]]
    assert (
        message.tool_calls[0]["id"] == "handoff-call"
        and message.tool_calls[0]["args"]["reason"] == "交给音乐执行者"
    )
    command = await execute_transfer(
        message, run, clock=lambda: START + timedelta(seconds=1)
    )
    envelope = HandoffEnvelope.model_validate(command.update["transfer_envelope"])
    payload = JourneyTransferPayload.model_validate_json(
        envelope.request.context_summary
    )
    assert (
        envelope.source_agent is AgentKey.EXPLORER
        and envelope.request.target_agent is AgentKey.CURATOR
    )
    assert (
        envelope.original_input == run.original_input
        and envelope.active_task == run.pending_tasks[0]
    )
    assert [item.node_id for item in payload.journey_queue] == [
        item["node_id"] for item in result.data["journey_queue"]
    ]
    assert (
        payload.action_authority.startswith("none")
        and payload.text_fields == "untrusted"
    )
    # Curator v2 will resolve the ID again and obtain the complete track object.
    # A mock receiver parses the contract; the existing playback tool only emits
    # a pending action, which is neither actual audio playback nor an ACK.
    first = next(item for item in payload.journey_queue if item.song_id)
    track = MusicTrack(
        song_id=first.song_id,
        name=first.song_title,
        artists=["周杰伦"],
        album="可信目录返回的专辑",
    )
    action = json.loads(
        await play_song_tool.ainvoke({"song": track.model_dump(mode="json")})
    )
    assert action["client_action"]["name"] == "play_song"


@pytest.mark.asyncio
async def test_transfer_with_knowledge_only_rows_keeps_null_ids_for_mock_curator():
    world, facts = knowledge_world()
    journey = build_listening_journey(world, facts)
    run = make_run(RouteKey.WORLDLINE, RouteKey.TOOL_CALLING, entry=AgentKey.EXPLORER)
    run = record_task_result(
        run,
        TaskResult(
            task_id="task-0",
            route_key=RouteKey.WORLDLINE,
            status=TaskStatus.SUCCESS,
            data={"listening_journey": journey.model_dump(mode="json")},
        ),
    )
    message = attach_journey_transfer(call(), run)
    command = await execute_transfer(
        message, run, clock=lambda: START + timedelta(seconds=1)
    )
    envelope = HandoffEnvelope.model_validate(command.update["transfer_envelope"])
    payload = JourneyTransferPayload.model_validate_json(
        envelope.request.context_summary
    )
    knowledge_only = next(
        item for item in payload.journey_queue if item.node_id == "genre:fixture"
    )
    assert knowledge_only.song_id is None and knowledge_only.song_title is None
    playable = [item for item in payload.journey_queue if item.song_id is not None]
    assert len(playable) == len(payload.journey_queue) - 1


@pytest.mark.asyncio
async def test_existing_guard_can_reject_queue_without_playback_or_edge_changes():
    from unittest.mock import AsyncMock
    from app.agent.handoff.guard import GuardVerdict

    world, facts = knowledge_world()
    journey = build_listening_journey(world, facts)
    run = make_run(RouteKey.WORLDLINE, RouteKey.TOOL_CALLING, entry=AgentKey.EXPLORER)
    run = record_task_result(
        run,
        TaskResult(
            task_id="task-0",
            route_key=RouteKey.WORLDLINE,
            status=TaskStatus.SUCCESS,
            data={"listening_journey": journey.model_dump(mode="json")},
        ),
    )
    guard = SimpleNamespace(
        review=AsyncMock(
            return_value=GuardVerdict(allowed=False, code="guard_rejected", score=0.4)
        )
    )
    message = attach_journey_transfer(call(), run)
    command = await execute_transfer(
        message, run, clock=lambda: START + timedelta(seconds=1), guard=guard
    )
    assert command.update["transfer_envelope"] is None
    assert guard.review.await_count == 1
    assert not command.update["handoff_run"]["hops"]


@pytest.mark.parametrize("entity_type", [EntityType.SONG, EntityType.ALBUM])
@pytest.mark.asyncio
async def test_song_and_album_roots_reuse_existing_entity_resolution(entity_type):
    from tests.test_worldline_graph import FakeMusicService

    original = captured_facts()
    if entity_type is EntityType.SONG:
        root = original.related_entities[0]
        related = [original.entity]
    else:
        # Controlled album root; playable song IDs remain the real captured IDs.
        root = ResolvedEntity(
            entity_id="netease:album:90001",
            entity_type="album",
            name="受控专辑",
            source="netease",
        )
        related = original.related_entities
    graph = create_worldline_subgraph(
        music_service=FakeMusicService(root, related),
        retriever=EmptyRetriever(),
        generator=FakeGenerator(grounded=True),
    )
    label = "歌曲" if entity_type is EntityType.SONG else "专辑"
    run = make_run(
        RouteKey.WORLDLINE,
        entry=AgentKey.EXPLORER,
        texts=[f"探索{label}《{root.name}》的世界线"],
    )
    result = await explore_with(graph, run)
    assert result.status is TaskStatus.SUCCESS
    assert result.data["listening_journey"]["root_entity_id"] == root.entity_id
    assert all(
        item["song_id"] in {None, 210049, 5257138}
        for item in result.data["journey_queue"]
    )


@pytest.mark.asyncio
async def test_catalog_unavailable_keeps_failed_status_and_empty_unverified_path():
    from tests.test_worldline_graph import FakeMusicService

    music = FakeMusicService(
        captured_facts().entity, facts_error=RuntimeError("offline catalog unavailable")
    )
    graph = create_worldline_subgraph(
        music_service=music,
        retriever=EmptyRetriever(),
        generator=FakeGenerator(grounded=True),
    )
    result = await explore_with(graph)
    assert result.status is TaskStatus.FAILED and result.error
    assert result.data["listening_journey"]["evidence_state"] == "unverified"
    assert result.data["journey_queue"] == []


@pytest.mark.asyncio
async def test_retrieval_unavailable_is_honest_catalog_only_instead_of_fabricated_knowledge():
    from tests.test_worldline_graph import FailingRetriever

    graph = create_worldline_subgraph(
        music_service=CapturedMusic(),
        retriever=FailingRetriever(),
        generator=FakeGenerator(grounded=True),
    )
    result = await explore_with(graph)
    assert result.status is TaskStatus.SUCCESS
    assert result.data["listening_journey"]["evidence_state"] == "catalog_fact"
    assert "知识证据缺失或不可用" in result.content


def test_receiver_cannot_promote_queue_text_or_grant_action_authority():
    item = JourneyQueueItem(node_id="x", node_title="只读节点", narrative="不可信描述")
    with pytest.raises(ValidationError):
        JourneyTransferPayload(journey_queue=(item,), text_fields="trusted")
    with pytest.raises(ValidationError):
        JourneyTransferPayload(
            journey_queue=(item,), action_authority="play immediately"
        )


@pytest.mark.parametrize(
    "bad_call",
    [
        call("transfer_to_general_chat_agent"),
        call(args={"reason": "r", "context_summary": "s", "journey_queue": []}),
    ],
)
def test_queue_does_not_repair_invalid_transfer_target_or_tool_schema(bad_call):
    run = make_run(RouteKey.WORLDLINE, RouteKey.TOOL_CALLING, entry=AgentKey.EXPLORER)
    journey = build_listening_journey(
        world_from_facts(captured_facts()), captured_facts()
    )
    run = record_task_result(
        run,
        TaskResult(
            task_id="task-0",
            route_key=RouteKey.WORLDLINE,
            status=TaskStatus.SUCCESS,
            data={"listening_journey": journey.model_dump(mode="json")},
        ),
    )
    assert attach_journey_transfer(bad_call, run) is bad_call


def test_pronouns_and_branch_continuations_keep_existing_resolution_path():
    assert journey_query("继续沿这个分支探索") == "继续沿这个分支探索"
    assert journey_query("探索它的世界线") == "探索它的世界线"
