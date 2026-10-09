"""Merged Pack network at real graph, source, SDK and durable receipt boundaries."""

from copy import deepcopy
from types import SimpleNamespace
import json
import logging

import pytest
from langchain_core.messages import AIMessage, ToolMessage

from app.agent.checkpointing import create_memory_checkpointer
from app.agent.foundation.context_contracts import CallManifest
from app.agent.foundation.hop_sources import build_hop_pack
from app.agent.foundation.long_horizon.coordinator import ContinuityCoordinator, ContinuityCheckpointer
from app.agent.foundation.long_horizon.history_writer import HistoryWriter
from app.agent.foundation.long_horizon.journal_contracts import JournalReader
from app.agent.foundation.long_horizon.models import HistoryEventType
from app.agent.foundation.long_horizon.runner import invoke
from app.agent.foundation.long_horizon.service import LongHorizonContextService
from app.agent.foundation.long_horizon.store import SQLiteContinuityStore
from app.agent.foundation.routing import RouteKey
from app.agent.foundation.subgraphs.worldline.generation import StructuredWorldLineGenerator
from app.agent.foundation.subgraphs.worldline.models import EntityType
from app.agent.foundation.subgraphs.worldline import stage_pack
from app.agent.handoff.continuity import AgentTransferJournal
from app.agent.handoff.graph import create_handoff_graph
from app.agent.handoff.hop_context import hop_source_scope
from app.agent.handoff.integrity import payload_digest
from app.agent.handoff.registry import AgentKey
from app.llm.runtime import RequestLedger, request_scope
from app.schemas.music import MusicTrack
from tests.test_agent_pack_impl_b import edge_harness, invocation, state
from tests.test_context_rag_source import source
from tests.test_handoff_network import search_tool, transfers
from tests.test_handoff_runtime import Router, ToolModel, call, player_context
from tests.test_llm_client import fake_settings
from tests.test_worldline_graph import FakeMusicService, entity


TEXT = "陪我聊聊；搜索晴天；深入了解这首歌；继续聊天"
G, C, E = AgentKey.GENERAL_CHAT, AgentKey.CURATOR, AgentKey.EXPLORER


def manifests(caplog):
    prefix = "context_engineering.call_manifest "
    return [CallManifest.model_validate(json.loads(record.getMessage()[len(prefix):]))
            for record in caplog.records if record.name == "app.llm.runtime"
            and record.getMessage().startswith(prefix)]


@pytest.mark.asyncio
@pytest.mark.parametrize("rag_mode", ["empty", "hybrid", "unavailable"])
async def test_three_agent_four_edge_network_uses_actual_packs_and_replays_without_calls(
    tmp_path, monkeypatch, caplog, rag_mode,
):
    caplog.set_level(logging.INFO, logger="app.llm.runtime")
    providers, sdk_calls, searches, errors = [], [], [], []
    from app.agent.handoff import graph as handoff_graph
    execute = handoff_graph.execute_owned_task

    async def capture_errors(*args, **kwargs):
        try:
            return await execute(*args, **kwargs)
        except Exception as error:
            errors.append((type(error).__name__, str(error)))
            raise

    monkeypatch.setattr(handoff_graph, "execute_owned_task", capture_errors)

    def rag_source():
        value = source(count=1, mode="empty" if rag_mode == "empty" else "hybrid")
        if rag_mode == "unavailable":
            from tests.test_retrieval_service import FakeBackend
            value.backend = FakeBackend(search_error=RuntimeError("offline unavailable"))
        providers.append(value)
        return value

    class OfflineSDK:
        def __init__(self, **kwargs):
            self.responses = self

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return None

        async def create(self, **request):
            sdk_calls.append(deepcopy(request))
            name = request["text"]["format"]["name"]
            payload = json.loads(request["input"][0]["content"])
            if name == "query_type_analysis_v1":
                answer = {"query_types": ["exploratory"], "confidence": 0.94}
            elif name == "worldline_narrative":
                citation = next(row["id"] for row in payload["sources"]
                                if row["id"].startswith("knowledge:rag:")) if rag_mode == "hybrid" else payload["sources"][0]["id"]
                prefix = "补充音乐知识与目录事实连接了歌曲与歌手。" if rag_mode == "hybrid" else "当前未检索到补充音乐知识，目录事实连接了歌曲与歌手。"
                answer = {"narrative": f"{prefix}[{citation}]"}
            else:
                assert name == "worldline_branches"
                kinds = {"artist": "artist", "album": "album", "song": "related_work"}
                answer = {"branches": [{"title": row["name"], "description": "沿目录事实继续了解。",
                    "branch_type": kinds[row["type"]], "target_node_id": row["id"]}
                    for row in payload["worldline_graph"]["nodes"] if row["type"] in kinds]}
            return SimpleNamespace(output_text=json.dumps(answer, ensure_ascii=False), status="completed",
                incomplete_details=None, output=[], usage=SimpleNamespace(input_tokens=80, output_tokens=20, total_tokens=100))

    monkeypatch.setattr(stage_pack, "RagSource", rag_source)
    monkeypatch.setattr("app.llm.client.AsyncOpenAI", OfflineSDK)
    settings = fake_settings()
    settings.max_output_tokens = 4096
    monkeypatch.setattr("app.llm.client.get_settings", lambda: settings)
    root = entity(31, EntityType.SONG, "晴天")
    service = FakeMusicService(root, [entity(2, EntityType.ARTIST, "周杰伦"), entity(3, EntityType.ALBUM, "叶惠美")])
    track = MusicTrack(song_id=31, name="晴天", artists=["周杰伦"], album="叶惠美")
    general = ToolModel(responses=[AIMessage(content="我在这里。"), AIMessage(content="搜索和探索已完成。")])
    curator = ToolModel(responses=[call("search_music", args={"keyword": "晴天", "limit": 1}, call_id="search:e"),
                                  AIMessage(content="目录中已找到晴天。")])
    transfer = transfers((G, C, E, C, G))
    path = tmp_path / "network.db"
    store = SQLiteContinuityStore(path)
    coordinator = ContinuityCoordinator(LongHorizonContextService(store))
    # Use the real query fallback rather than the older network fixture's fake analyzer.
    from app.rag.query_understanding.analyzer import QueryAnalyzer
    from app.rag.query_understanding.config import load_query_understanding_config
    from app.rag.query_understanding.service import QueryUnderstandingService
    from app.agent.foundation.subgraphs.worldline.graph import create_worldline_subgraph
    from tests.test_worldline_graph import EmptyRetriever
    workflow = create_worldline_subgraph(music_service=service, retriever=EmptyRetriever(),
        generator=StructuredWorldLineGenerator(), query_understanding=QueryUnderstandingService(
            analyzer=QueryAnalyzer(load_query_understanding_config())))
    graph = create_handoff_graph(checkpointer=ContinuityCheckpointer(create_memory_checkpointer(), coordinator),
        transfer_journal=coordinator.journal, intent_router=Router(
            RouteKey.GENERAL_CHAT, RouteKey.TOOL_CALLING, RouteKey.WORLDLINE, RouteKey.GENERAL_CHAT),
        transfer_model=transfer, general_model=general, curator_model=curator,
        tool_tools=[search_tool(track, searches)], worldline_subgraph=workflow)
    options = dict(message=TEXT, thread_id="pack-e:" + rag_mode, routing_context=player_context(),
                   graph_mode="handoff", coordinator=coordinator, graph=graph, request_id="e:one")
    response = await invoke(**options)
    assert str(response.status) == "success", {"errors": errors, "response": response.model_dump(),
        "sdk_phases": [row["text"]["format"]["name"] for row in sdk_calls]}
    assert searches == [("晴天", 1)]
    assert len(general.seen_messages) == len(curator.seen_messages) == 2
    assert len(transfer.seen_messages) == 4
    assert all(all(name.startswith("transfer_to_") for name in names) for names in transfer.bound_names)
    calls = manifests(caplog)
    business = [row for row in calls if row.agent_context is not None and row.agent_context.phase != "relay"]
    relay = [row for row in calls if row.agent_context is not None and row.agent_context.phase == "relay"]
    assert len(relay) == len(transfer.seen_messages) == 4
    assert [(row.agent_context.owner_agent, row.agent_context.route_key) for row in relay] == [
        ("general_chat", "tool_calling"), ("curator", "worldline"),
        ("explorer", "general_chat"), ("curator", "general_chat")]
    for row in relay:
        audit = row.agent_context
        assert (audit.recipe_id, audit.recipe_version) == ("handoff", "v1")
        assert {item.source for item in audit.source_selection} <= {"system", "user_query", "received_envelope"}
        assert [prompt.prompt_id for prompt in audit.prompts] == ["handoff-transfer"]
        if audit.envelope is not None:
            assert audit.envelope.selected_fields == ("route_path",)
            assert row.sources["received_envelope"].final_tokens <= 256
        assert all(prompt.content_digest.startswith("sha256:") for prompt in audit.prompts)
    assert [(row.agent_context.owner_agent, row.agent_context.phase) for row in business] == [
        ("general_chat", "answer"), ("curator", "tool_step"), ("curator", "tool_observation"),
        ("explorer", "query_analysis"), ("explorer", "narrative"), ("explorer", "branches"),
        ("general_chat", "answer")]
    assert business[0].agent_context.envelope is None
    assert [row["text"]["format"]["name"] for row in sdk_calls] == [
        "query_type_analysis_v1", "worldline_narrative", "worldline_branches"]
    assert len(providers) == 2  # Query analysis must not collect formal RAG.
    assert not any(isinstance(message, ToolMessage) for message in general.seen_messages[-1])
    assert any(isinstance(message, ToolMessage) and message.tool_call_id == "search:e"
               for message in curator.seen_messages[-1])
    rendered = json.dumps(sdk_calls, ensure_ascii=False) + str(general.seen_messages)
    assert "private control reasoning" not in rendered
    assert all(not result.metadata.get("client_actions") for result in response.results)
    task = store.active_task("handoff:v1:pack-e:" + rag_mode)
    events = store.query_task_history(task.task_id, limit=1000)
    committed = [row for row in events if row.event_type == HistoryEventType.AGENT_TRANSFER
                 and row.payload["phase"] == "committed"]
    assert len(committed) == 4
    reader = JournalReader(store)
    for row in calls:
        assert row.usage_ref is not None
        assert reader.resolve(row.usage_ref).payload["tokens"] == row.total_tokens
    for row in business[1:]:
        audit = row.agent_context
        assert audit.envelope is not None and audit.envelope.commit_ref is not None
        assert reader.resolve(audit.envelope.commit_ref).payload["phase"] == "committed"
        assert audit.envelope.selected_fields
        assert audit.session is not None
        assert audit.budget_snapshot.owner_remaining <= 64000
    assert any(row.source == "received_envelope" for row in business[-1].agent_context.source_selection)
    for row in business:
        assert all(prompt.content_digest.startswith("sha256:") for prompt in row.agent_context.prompts)
    count_before = (len(calls), len(events), len(sdk_calls), len(providers))
    reopened = SQLiteContinuityStore(path)
    options["coordinator"] = ContinuityCoordinator(LongHorizonContextService(reopened))
    replay = await invoke(**options)
    assert replay == response
    assert count_before == (len(manifests(caplog)), len(reopened.query_task_history(task.task_id, limit=1000)),
                            len(sdk_calls), len(providers))


@pytest.mark.asyncio
@pytest.mark.parametrize("edge", [("general_chat", "curator"), ("curator", "explorer"),
                                  ("explorer", "curator"), ("curator", "general_chat")])
async def test_four_edges_reopen_the_same_committed_envelope_without_reassembly_charges(tmp_path, edge):
    h = await edge_harness(tmp_path / "edge.db", edge)
    original = h.store.query_task_history(h.session.task_id, limit=1000)
    h.store = SQLiteContinuityStore(h.store.path)
    h.journal = AgentTransferJournal(HistoryWriter(h.store))
    ledger = RequestLedger(h.run.usage, h.run.current_agent, h.run.limits, deadline=30, run_id=h.run.run_id)
    before = deepcopy(ledger.usage)
    with request_scope(ledger):
        async with hop_source_scope(state(h), h.run, journal=h.journal, identity=h.identity):
            first = build_hop_pack(invocation(h))
            second = build_hop_pack(invocation(h))
    left, right = first.pack.manifest["agent_context"], second.pack.manifest["agent_context"]
    assert left == right and left["envelope"]["commit_ref"]
    assert left["envelope"]["payload_digest"] == payload_digest(h.envelope)
    assert first.source_status["received_envelope"] == "available"
    assert left["envelope"]["selected_fields"]
    assert ledger.usage == before and not ledger.call_manifests and ledger.reserved == 0
    assert h.store.query_task_history(h.session.task_id, limit=1000) == original
