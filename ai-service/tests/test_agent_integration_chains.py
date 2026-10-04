"""Real parent nodes, frozen producers/receivers, tools and SQLite receipt authority."""

import json
import re
from datetime import datetime, UTC
from types import SimpleNamespace

import pytest
from langchain_core.messages import AIMessage, ToolMessage
from langgraph.graph import START

from app.agent.foundation.long_horizon.operations import (
    active_operations,
    OperationJournal,
)
from app.agent.foundation.long_horizon.models import HistoryEventType, HistoryPointer
from app.agent.foundation.long_horizon.public_events import (
    PublicAction,
    PublicActionAck,
)
from app.agent.foundation.long_horizon.history_writer import digest
from app.agent.foundation.models import TaskResult, TaskStatus
from app.agent.foundation.routing import RouteKey, RoutingContext
from app.agent.foundation.subgraphs.tool_calling.client_tools import play_song_tool
from app.agent.foundation.subgraphs.worldline.models import (
    MusicEntityFacts,
    MusicFact,
    EvidenceSource,
    EvidenceType,
    WorldLineGraph,
    WorldLineNode,
    WorldLineEdge,
)
from app.agent.handoff.graph import build_handoff_graph
from app.agent.handoff.journey import JourneyTransferPayload
from app.agent.handoff.registry import AgentKey
from app.agent.handoff.state import HandoffRun, RunStatus, StopReason, new_run
from app.schemas.context import MusicAgentContext
from app.schemas.music import MusicSearchResult, MusicTrack
from app.tools.music import MUSIC_AGENT_TOOLS, MusicServiceClient
from tests.test_explorer_journey_v1 import captured_facts, world_from_facts
from tests.test_curator_curation_v1 import Catalog, responses
from tests.test_handoff_runtime import Router, ToolModel, call, input_state
from tests import test_write_reconciliation as write_helpers

journal = write_helpers.journal
G, C, E = AgentKey.GENERAL_CHAT, AgentKey.CURATOR, AgentKey.EXPLORER
W, T, CHAT = RouteKey.WORLDLINE, RouteKey.TOOL_CALLING, RouteKey.GENERAL_CHAT
JOURNEY_TOOLS = [*MUSIC_AGENT_TOOLS, play_song_tool]
CONTEXT = MusicAgentContext(session_cookie="isolated-integration-fixture")


@pytest.fixture(autouse=True)
def isolated_operations(monkeypatch):
    token = active_operations.set(None)
    monkeypatch.setattr(
        "app.agent.handoff.agents.get_base_chat_model",
        lambda: ToolModel(responses=[AIMessage(content="不应用于结构化承接。")]),
    )
    yield
    active_operations.reset(token)


class WorldFixture:
    """Mock only the source provider; Explorer builds its frozen Journey itself."""

    def __init__(self, *, knowledge=True, empty=False, only_knowledge=False):
        original = captured_facts()
        first, second = original.related_entities
        self.facts = MusicEntityFacts(
            entity=first,
            related_entities=[second],
            sources=original.sources,
            facts=[
                MusicFact(
                    id="catalog:link",
                    subject_id=first.entity_id,
                    object_entity_id=second.entity_id,
                    predicate="related_to",
                    value="目录记录的歌曲关联",
                    evidence_ids=[original.sources[0].id],
                )
            ],
        )
        self.world = world_from_facts(self.facts)
        if knowledge:
            source = EvidenceSource(
                id="knowledge:scene",
                title="既有知识夹具",
                source="fixture",
                source_type=EvidenceType.KNOWLEDGE,
                excerpt="资料记录根歌曲与夜间场景的关联。",
            )
            self.world = self.world.model_copy(
                update={
                    "sources": [*self.world.sources, source],
                    "graph": WorldLineGraph(
                        root=first.entity_id,
                        nodes=[
                            *self.world.graph.nodes,
                            WorldLineNode(
                                id="scene:night", type="scene", name="夜间场景"
                            ),
                        ],
                        edges=[
                            *self.world.graph.edges,
                            WorldLineEdge(
                                source=first.entity_id,
                                target="scene:night",
                                relation="belongs_to_scene",
                                description=source.excerpt,
                                evidence_ids=[source.id],
                            ),
                        ],
                    ),
                }
            )
        if empty:
            self.world = self.world.model_copy(update={"sources": []})
        self.private = None if empty or only_knowledge else self.facts
        self.seen = []

    async def ainvoke(self, state, **_):
        self.seen.append(state)
        return {
            "task_result": TaskResult(
                task_id=state["task_id"],
                route_key=W,
                status=TaskStatus.SUCCESS,
                content="已读取来源。",
                data={"worldline_result": self.world.model_dump(mode="json")},
            ),
            "entity_facts": self.private,
        }


def install_journey_catalog(monkeypatch, world, *, unavailable=False, duplicate=False):
    tracks = [
        MusicTrack(
            song_id=int(s.entity_id.split(":")[-1]),
            name=s.name,
            artists=["周杰伦"],
            album="已有目录夹具",
        )
        for s in [world.facts.entity, *world.facts.related_entities]
    ]
    seen = []

    async def search(client, keyword, limit=10):
        seen.append(keyword)
        matches = [track for track in tracks if track.name == keyword]
        if unavailable:
            matches = []
        if duplicate:
            matches *= 2
        return MusicSearchResult(keyword=keyword, limit=limit, songs=matches)

    async def likes(client):
        return []

    monkeypatch.setattr(MusicServiceClient, "search_music", search)
    monkeypatch.setattr(MusicServiceClient, "get_liked_song_ids", likes)
    return tracks, seen


def transfer_model(chain, *, wrong=False):
    return ToolModel(
        responses=[
            call(
                "transfer_to_general_chat_agent"
                if wrong and target is C and index == 3
                else f"transfer_to_{target.value}_agent",
                call_id=f"chain-hop-{index}",
            )
            for index, target in enumerate(chain[1:], 1)
        ]
    )


async def invoke(
    text, routes, chain, *, world=None, journal=None, explorer_entry=False, **kwargs
):
    router = Router(*routes)
    model = kwargs.pop("transfer_model", transfer_model(chain))
    builder = build_handoff_graph(
        intent_router=router, transfer_model=model, worldline_subgraph=world, **kwargs
    )
    state = input_state(text, RoutingContext())
    if explorer_entry:
        # User-authorized integration boundary: a legal run already at Explorer.
        # Use unchanged production agent/final nodes; no runtime entry policy change.
        builder.edges.discard((START, "prepare_context"))
        builder.add_edge(START, "explorer_agent")
        decision = await router.route(text, state["routing_context"])
        run = new_run(
            "integration-explorer-run",
            text,
            decision,
            entry_agent=E,
            started_at=datetime.now(UTC),
        )
        state.update(
            route_decision=decision,
            handoff_run=run.model_dump(mode="json"),
            transfer_envelope=None,
            handoff_trace=[],
            guard_retry=False,
        )
    token = active_operations.set(journal)
    try:
        output = await builder.compile().ainvoke(
            state, context=CONTEXT, config={"recursion_limit": 60}
        )
    finally:
        active_operations.reset(token)
    return output, HandoffRun.model_validate(output["handoff_run"])


def envelopes(output):
    calls = {
        m.tool_calls[0]["id"]: m.tool_calls[0]
        for m in output["messages"]
        if isinstance(m, AIMessage) and m.tool_calls
    }
    accepted = [
        m
        for m in output["messages"]
        if isinstance(m, ToolMessage) and json.loads(m.content).get("accepted")
    ]
    return [calls[m.tool_call_id] for m in accepted]


@pytest.mark.asyncio
async def test_chain1_full_entry_three_nodes_two_ordered_actions_and_knowledge_skip(
    monkeypatch, journal
):
    world = WorldFixture()
    tracks, searches = install_journey_catalog(monkeypatch, world)
    output, run = await invoke(
        "探索歌曲《起点》的世界线；按 Journey 顺序播放整条路径",
        [W, T],
        [G, C, E, C],
        world=world,
        journal=journal,
        tool_tools=JOURNEY_TOOLS,
    )
    assert run.status is RunStatus.COMPLETED, output["final_response"]
    assert run.handoff_chain == (G, C, E, C) and run.handoff_count == 3
    assert len(world.seen) == 1
    source, consumed = run.results
    queue = source.data["journey_queue"]
    assert len(queue) == 3 and sum(row["song_id"] is None for row in queue) == 1
    wire = JourneyTransferPayload.model_validate_json(
        envelopes(output)[-1]["args"]["context_summary"]
    )
    assert list(wire.model_dump(mode="json")["journey_queue"]) == queue
    actions = consumed.data["client_actions"]
    expected = [node["song_id"] for node in queue if node["song_id"] is not None]
    assert [a["arguments"]["song"]["song_id"] for a in actions] == expected
    assert {a["arguments"]["song"]["name"] for a in actions} == {t.name for t in tracks}
    assert len(searches) == 2
    assert consumed.data["journey_consumption"]["knowledge_node_ids"] == [
        node["node_id"] for node in queue if node["song_id"] is None
    ]
    assert (
        "待客户端执行/确认" in consumed.content and "已经在播" not in consumed.content
    )


@pytest.mark.asyncio
async def test_chain2_real_curation_transfer_and_grounded_polished_intro(
    monkeypatch, journal
):
    catalog = Catalog(monkeypatch, count=15)
    output, run = await invoke(
        "创建雨天夜跑歌单；润色刚才歌单的导语",
        [T, CHAT],
        [G, C, G],
        journal=journal,
        curator_model=responses(catalog),
        tool_tools=JOURNEY_TOOLS,
    )
    assert run.status is RunStatus.COMPLETED, output["final_response"]
    assert run.handoff_chain == (G, C, G)
    curation, narrated = run.results
    assert curation.data["curation"]["polish_request"]["songs"]
    assert "polish_request" in envelopes(output)[-1]["args"]["context_summary"]
    assert "雨天夜跑" in narrated.content
    assert set(re.findall(r"《(.*?)》", narrated.content)) <= {
        song.name for song in catalog.songs
    }
    assert len(narrated.data["structured_facts"]["facts"]) == 31
    assert "已有加歌回执" in narrated.content and "[S15]" in narrated.content
    assert "后端成功回执" in narrated.content and "未另行只读核对" in narrated.content
    assert (
        "效果未知" not in narrated.content
        and "已确认：" not in narrated.content
        and "已执行：" not in narrated.content
    )
    assert not narrated.data.get("client_actions")


@pytest.mark.asyncio
async def test_chain3_explorer_entry_two_nodes_four_real_hops_and_each_narration(
    monkeypatch, journal
):
    world = WorldFixture(knowledge=False)
    install_journey_catalog(monkeypatch, world)
    output, run = await invoke(
        "探索歌曲《起点》的世界线；按 Journey 播放第一个节点；讲解刚才节点；继续播放；讲解第二个节点",
        [W, T, CHAT, T, CHAT],
        [E, C, G, C, G],
        world=world,
        journal=journal,
        explorer_entry=True,
        tool_tools=JOURNEY_TOOLS,
    )
    assert run.status is RunStatus.COMPLETED, output["final_response"]
    assert run.handoff_chain == (E, C, G, C, G) and run.handoff_count == 4
    assert all(
        not (hop.source_agent is E and hop.target_agent is G) for hop in run.hops
    )
    assert [hop.completed_count for hop in run.hops] == [1, 2, 3, 4]
    nodes = run.results[0].data["listening_journey"]["nodes"]
    for index, c_index in enumerate((1, 3)):
        curator, chat = run.results[c_index : c_index + 2]
        assert curator.data["journey_consumption"]["node_ids"] == [
            nodes[index]["node_id"]
        ]
        assert len(curator.data["client_actions"]) == 1
        assert nodes[index]["narration"]["statement"] in chat.content
        assert "[S" in chat.content
        assert chat.data["structured_facts"]["sources"]
        assert "已生成指令，待客户端执行/确认" in chat.content
    assert [
        r.data["client_actions"][0]["arguments"]["song"]["song_id"]
        for r in [run.results[1], run.results[3]]
    ] == [node["song_id"] for node in nodes]


@pytest.mark.asyncio
async def test_full_entry_two_node_narration_stops_at_fifth_hop_without_claiming_rest(
    monkeypatch,
):
    world = WorldFixture(knowledge=False)
    install_journey_catalog(monkeypatch, world)
    output, run = await invoke(
        "探索歌曲《起点》的世界线；按 Journey 播放第一个节点；讲解刚才节点；继续播放；讲解第二个节点",
        [W, T, CHAT, T, CHAT],
        [G, C, E, C, G, C, G],
        world=world,
        tool_tools=JOURNEY_TOOLS,
    )
    assert (
        run.status is RunStatus.FAILED and run.stop_reason is StopReason.HANDOFF_LIMIT
    )
    assert run.handoff_count == 5 and len(run.results) == 4
    assert run.pending_tasks[0].route_key is CHAT
    assert (
        "尚未执行" in output["final_response"]
        and "已经全部完成" not in output["final_response"]
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "mode",
    [
        "empty",
        "only_knowledge",
        "missing_catalog",
        "duplicate_catalog",
        "missing_tools",
        "denied_action",
    ],
)
async def test_journey_boundaries_are_clear_and_never_invent_playback(
    monkeypatch, mode
):
    world = WorldFixture(empty=mode == "empty", only_knowledge=mode == "only_knowledge")
    _, seen = install_journey_catalog(
        monkeypatch,
        world,
        unavailable=mode == "missing_catalog",
        duplicate=mode == "duplicate_catalog",
    )
    text = "探索歌曲《起点》的世界线；" + (
        "不要播放 Journey" if mode == "denied_action" else "按 Journey 顺序播放"
    )
    output, run = await invoke(
        text,
        [W, T],
        [G, C, E, C],
        world=world,
        tool_tools=[play_song_tool] if mode == "missing_tools" else JOURNEY_TOOLS,
    )
    result = run.results[-1]
    assert not result.data.get("client_actions")
    if mode == "only_knowledge":
        assert run.status is RunStatus.COMPLETED and not seen
        assert "纯知识节点" in result.content
    elif mode == "empty":
        assert (
            run.status is RunStatus.NEEDS_INPUT
            and result.data["reason"] == "empty_journey"
        )
    else:
        assert run.status is RunStatus.FAILED and result.data["reason"] in {
            "journey_tool_failed",
            "missing_journey_tools",
        }


@pytest.mark.asyncio
async def test_forbidden_explorer_generalchat_transfer_stops_before_actions(
    monkeypatch,
):
    world = WorldFixture()
    _, seen = install_journey_catalog(monkeypatch, world)
    output, run = await invoke(
        "探索歌曲《起点》的世界线；按 Journey 播放",
        [W, T],
        [G, C, E, C],
        world=world,
        transfer_model=transfer_model([G, C, E, C], wrong=True),
        tool_tools=JOURNEY_TOOLS,
    )
    assert (
        run.status is RunStatus.FAILED
        and run.stop_reason is StopReason.INVALID_TRANSFER
    )
    assert run.handoff_chain == (G, C, E) and len(run.results) == 1 and not seen
    assert "转交" in output["final_response"] or "待办" in output["final_response"]


@pytest.mark.asyncio
async def test_uncertain_curation_write_stops_without_generalchat_or_unreceipted_songs(
    monkeypatch, journal
):
    catalog = Catalog(monkeypatch, count=15, mode="write_unknown", fail_at=2)
    output, run = await invoke(
        "创建雨天夜跑歌单；润色歌单导语",
        [T, CHAT],
        [G, C, G],
        journal=journal,
        curator_model=responses(catalog),
        tool_tools=JOURNEY_TOOLS,
    )
    assert run.status is RunStatus.FAILED and run.handoff_chain == (G, C)
    data = run.results[0].data["curation"]
    assert len(data["polish_request"]["songs"]) == 2
    assert {s["song_id"] for s in data["polish_request"]["songs"]} == set(
        data["accepted_song_ids"]
    )
    assert "效果未知" in run.results[0].content


@pytest.mark.asyncio
async def test_readback_curation_effect_is_confirmed_in_real_generalchat_receiver(
    monkeypatch, journal
):
    catalog = Catalog(monkeypatch, count=10, mode="write_readback")
    operations = OperationJournal(
        journal.writer, journal.accounting, read_client=catalog.read_client()
    )
    output, run = await invoke(
        "创建雨天夜跑歌单；润色歌单导语",
        [T, CHAT],
        [G, C, G],
        journal=operations,
        curator_model=responses(catalog),
        tool_tools=JOURNEY_TOOLS,
    )
    assert run.status is RunStatus.COMPLETED, output["final_response"]
    assert "只读核对已确认" in run.results[-1].content
    assert "已执行：" not in run.results[-1].content


@pytest.mark.asyncio
@pytest.mark.parametrize("ack", [None, "applied", "failed"])
async def test_journey_narration_uses_actual_ack_and_never_promotes_generated(
    monkeypatch, journal, ack
):
    from app.agent.handoff.chain_receivers import receive_general_chat

    world = WorldFixture(knowledge=False)
    install_journey_catalog(monkeypatch, world)

    async def client_ack_then_receive(state, run):
        if ack:
            for action in run.results[-1].data["client_actions"]:
                proposal = journal.writer.append_public_event(
                    journal.identity,
                    action["action_id"],
                    HistoryEventType.CLIENT_ACTION,
                    PublicAction(
                        action_id=action["action_id"],
                        name=action["name"],
                        arguments_digest=digest(action["arguments"]),
                    ),
                )
                journal.writer.append_public_event(
                    journal.identity,
                    "ack:" + action["action_id"],
                    HistoryEventType.ACTION_ACK,
                    PublicActionAck(
                        action_id=action["action_id"],
                        status=ack,
                        action_ref=HistoryPointer(
                            kind="event", target_id=proposal.event_id
                        ),
                    ),
                )
        return await receive_general_chat(state, run)

    monkeypatch.setattr(
        "app.agent.handoff.chain_receivers.receive_general_chat",
        client_ack_then_receive,
    )
    output, run = await invoke(
        "探索歌曲《起点》的世界线；按 Journey 顺序播放；讲解这些节点",
        [W, T, CHAT],
        [G, C, E, C, G],
        world=world,
        journal=journal,
        tool_tools=JOURNEY_TOOLS,
    )
    assert run.status is RunStatus.COMPLETED, output["final_response"]
    result = run.results[-1]
    expected = (
        "已生成指令，待客户端执行/确认"
        if ack is None
        else "已执行：客户端 ACK"
        if ack == "applied"
        else "效果未知"
    )
    assert expected in result.content
    assert "已确认：" not in result.content and "已经在播" not in result.content


@pytest.mark.asyncio
@pytest.mark.parametrize("corruption", ["version", "song_id", "node_order"])
async def test_journey_wire_must_equal_frozen_explorer_result(monkeypatch, corruption):
    from app.agent.handoff.journey import attach_journey_transfer

    world = WorldFixture()
    _, seen = install_journey_catalog(monkeypatch, world)

    def corrupt(message, run):
        result = attach_journey_transfer(message, run)
        data = json.loads(result.tool_calls[0]["args"]["context_summary"])
        if corruption == "version":
            data["schema_version"] = 2
        elif corruption == "song_id":
            data["journey_queue"][0]["song_id"] = 999999
        else:
            data["journey_queue"].reverse()
        result.tool_calls[0]["args"]["context_summary"] = json.dumps(
            data, ensure_ascii=False
        )
        return result

    monkeypatch.setattr("app.agent.handoff.journey.attach_journey_transfer", corrupt)
    output, run = await invoke(
        "探索歌曲《起点》的世界线；按 Journey 播放",
        [W, T],
        [G, C, E, C],
        world=world,
        tool_tools=JOURNEY_TOOLS,
    )
    assert run.status is RunStatus.NEEDS_INPUT and not seen
    assert run.results[-1].data["reason"] == "invalid_journey_payload"
    assert not run.results[-1].data["client_actions"]


@pytest.mark.asyncio
async def test_guard_rejected_explorer_curator_edge_does_not_consume_or_play(
    monkeypatch,
):
    world = WorldFixture()
    _, seen = install_journey_catalog(monkeypatch, world)

    class Guard:
        async def review(self, envelope):
            allowed = envelope.source_agent is not E
            return SimpleNamespace(
                allowed=allowed,
                code="accepted" if allowed else "guard_rejected",
                score=1.0 if allowed else 0.0,
            )

    output, run = await invoke(
        "探索歌曲《起点》的世界线；按 Journey 播放",
        [W, T],
        [G, C, E, C, C],
        world=world,
        tool_tools=JOURNEY_TOOLS,
        handoff_guard=Guard(),
    )
    assert run.status is RunStatus.FAILED and run.stop_reason is StopReason.RETRY_LIMIT
    assert run.handoff_chain == (G, C, E) and len(run.results) == 1 and not seen


@pytest.mark.asyncio
async def test_guard_rejected_curator_generalchat_keeps_receipted_playlist(
    monkeypatch, journal
):
    catalog = Catalog(monkeypatch, count=10)

    class Guard:
        async def review(self, envelope):
            allowed = envelope.source_agent is not C
            return SimpleNamespace(
                allowed=allowed,
                code="accepted" if allowed else "guard_rejected",
                score=1.0 if allowed else 0.0,
            )

    output, run = await invoke(
        "创建雨天夜跑歌单；润色歌单导语",
        [T, CHAT],
        [G, C, G, G],
        journal=journal,
        curator_model=responses(catalog),
        tool_tools=JOURNEY_TOOLS,
        handoff_guard=Guard(),
    )
    assert run.status is RunStatus.FAILED and run.stop_reason is StopReason.RETRY_LIMIT
    assert run.handoff_chain == (G, C) and len(run.results) == 1
    assert len(run.results[0].data["curation"]["accepted_song_ids"]) == 10
    assert len(catalog.members) == 10
    assert "尚未执行" in output["final_response"]


@pytest.mark.asyncio
async def test_curation_receiver_does_not_replace_unrelated_companion_chat(
    monkeypatch, journal
):
    catalog = Catalog(monkeypatch, count=10)
    output, run = await invoke(
        "创建雨天夜跑歌单；我有点累，陪我聊聊",
        [T, CHAT],
        [G, C, G],
        journal=journal,
        curator_model=responses(catalog),
        general_model=ToolModel(
            responses=[AIMessage(content="我陪你聊一会儿，今天是什么让你觉得累呢？")]
        ),
        tool_tools=JOURNEY_TOOLS,
    )
    assert run.status is RunStatus.COMPLETED, output["final_response"]
    assert run.results[-1].content == "我陪你聊一会儿，今天是什么让你觉得累呢？"
    assert "structured_facts" not in run.results[-1].data
    assert "polish_request" not in envelopes(output)[-1]["args"]["context_summary"]


@pytest.mark.asyncio
async def test_failed_second_play_preserves_first_generated_action_and_stops(
    monkeypatch, journal
):
    world = WorldFixture(knowledge=False)
    install_journey_catalog(monkeypatch, world)
    calls = []

    async def fail_second(song):
        calls.append(song.song_id)
        if len(calls) == 2:
            raise ValueError("fixture play failure")
        return await play_song_tool.coroutine(song)

    tool = play_song_tool.model_copy(update={"coroutine": fail_second})
    output, run = await invoke(
        "探索歌曲《起点》的世界线；按 Journey 播放",
        [W, T],
        [G, C, E, C],
        world=world,
        journal=journal,
        tool_tools=[*MUSIC_AGENT_TOOLS, tool],
    )
    assert run.status is RunStatus.FAILED
    result = run.results[-1]
    assert len(calls) == 2 and len(result.data["client_actions"]) == 1
    assert result.data["client_actions"][0]["arguments"]["song"]["song_id"] == calls[0]
    assert len(result.data["journey_consumption"]["node_ids"]) == 1
    assert "已有指令仍待客户端执行/确认" in result.content


@pytest.mark.asyncio
async def test_exhausted_journey_does_not_repeat_actions_after_narration(monkeypatch):
    world = WorldFixture(knowledge=False)
    world.facts = world.facts.model_copy(update={"related_entities": [], "facts": []})
    world.private = world.facts
    world.world = world_from_facts(world.facts)
    _, seen = install_journey_catalog(monkeypatch, world)
    output, run = await invoke(
        "探索歌曲《起点》的世界线；按 Journey 播放；讲解刚才节点；继续播放",
        [W, T, CHAT, T],
        [E, C, G, C],
        world=world,
        explorer_entry=True,
        tool_tools=JOURNEY_TOOLS,
    )
    assert run.status is RunStatus.NEEDS_INPUT and len(seen) == 1
    assert run.results[-1].data["reason"] == "journey_exhausted"
    assert len(run.results[1].data["client_actions"]) == 1
    assert not run.results[-1].data["client_actions"]
    assert "没有重复生成播放指令" in run.results[-1].content


@pytest.mark.asyncio
async def test_polish_lead_preserves_structured_page_budget_and_full_catalog_names(
    monkeypatch, journal
):
    catalog = Catalog(monkeypatch, count=15)
    catalog.songs = [
        s.model_copy(
            update={
                "name": s.name + "长" * 270,
                "album": s.album + "长" * 270,
                "artists": ["目录歌手" + "a" * 700],
            }
        )
        if index < 7 or index == 14
        else s
        for index, s in enumerate(catalog.songs)
    ]
    output, run = await invoke(
        "创建雨天夜跑歌单；润色歌单导语",
        [T, CHAT],
        [G, C, G],
        journal=journal,
        curator_model=responses(catalog),
        tool_tools=JOURNEY_TOOLS,
    )
    assert run.status is RunStatus.COMPLETED, (
        output["final_response"],
        run.stop_reason,
        run.usage,
    )
    result = run.results[-1]
    assert len(result.content) <= 10_000 and "下一页" in result.content
    facts = result.data["structured_facts"]["facts"]
    assert len(facts) == 31
    assert all(
        any(song.name in fact["assertion"] for fact in facts) for song in catalog.songs
    )
