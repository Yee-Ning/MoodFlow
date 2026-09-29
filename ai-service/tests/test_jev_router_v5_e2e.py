"""Controlled V5 → MainGraph → real Subgraph integration without live side effects."""

from __future__ import annotations

from langchain_core.messages import AIMessage
import pytest

from app.agent.decision.jev.router_v5 import JevRouterV5
from app.agent.foundation.main_graph import build_main_graph
from app.agent.foundation.models import TaskStatus
from app.agent.foundation.routing import RouteKey, RoutingContext
from app.agent.foundation.state.main import create_main_state
from app.agent.foundation.subgraphs.current_song.graph import create_current_song_subgraph
from app.agent.foundation.subgraphs.general_chat.graph import create_general_chat_subgraph
from app.agent.foundation.subgraphs.tool_calling.graph import create_tool_calling_subgraph
from app.agent.foundation.subgraphs.worldline.graph import create_worldline_subgraph
from app.agent.foundation.subgraphs.worldline.models import EntityType
from tests.test_foundation_e2e_acceptance import KnowledgeRetriever, SongProvider
from tests.test_jev_router_v5 import Client
from tests.test_worldline_graph import FakeGenerator, FakeMusicService, entity

pytestmark = pytest.mark.asyncio


async def _graph(query, routes, *, context=None):
    seen_tool: list[tuple[str, int]] = []
    seen_song: list[int] = []
    seen_chat: list[str] = []

    async def song_assistant(messages, current_input, song_context, routing_context):
        seen_song.append(song_context.song_id)
        return AIMessage(content="歌曲分析完成")

    async def tool_assistant(messages, current_input, routing_context, prior_entities):
        seen_tool.append((current_input, len(prior_entities)))
        return AIMessage(content=f"已处理:{current_input}")

    async def chat_assistant(messages, current_input, context, prior_results):
        seen_chat.append(current_input)
        return AIMessage(content="知识解释完成")

    graph = build_main_graph(
        intent_router=JevRouterV5(Client(routes)),
        current_song_subgraph=create_current_song_subgraph(
            provider=SongProvider(), assistant=song_assistant,
        ),
        tool_calling_subgraph=create_tool_calling_subgraph(assistant=tool_assistant),
        general_chat_subgraph=create_general_chat_subgraph(assistant=chat_assistant),
        worldline_subgraph=create_worldline_subgraph(
            music_service=FakeMusicService(entity(1, EntityType.SONG, "晴天")),
            retriever=KnowledgeRetriever(), generator=FakeGenerator(grounded=True),
        ),
    ).compile()
    result = await graph.ainvoke(create_main_state(query, routing_context=context))
    return result, seen_tool, seen_song, seen_chat


async def test_v5_current_song_and_missing_context_end_to_end():
    result, _, song, _ = await _graph(
        "分析正在听的这首歌。", ["current_song"],
        context=RoutingContext(has_current_song=True, current_song_id=101),
    )
    assert song == [101]
    assert result["task_results"][0].status is TaskStatus.SUCCESS
    missing, _, song, _ = await _graph("分析正在听的这首歌。", ["current_song"])
    assert not song
    assert missing["route_decision"].needs_clarification
    assert not missing["task_results"]


async def test_v5_general_chat_and_worldline_end_to_end():
    chat, _, _, seen_chat = await _graph("解释什么是后摇。", ["general_chat"])
    assert seen_chat == ["解释什么是后摇"]
    assert chat["task_results"][0].route_key == RouteKey.GENERAL_CHAT
    world, _, _, _ = await _graph("探索《晴天》的影响关系证据。", ["worldline"])
    assert world["task_results"][0].route_key == RouteKey.WORLDLINE
    assert world["task_results"][0].status is TaskStatus.SUCCESS


async def test_v5_two_tool_tasks_dispatch_once_each_in_order():
    query = "先搜索《Creep》，然后播放搜索到的版本。"
    result, tools, _, _ = await _graph(query, ["tool_calling", "tool_calling"])
    assert [item[0] for item in tools] == ["先搜索《Creep》", "播放搜索到的版本"]
    tasks = result["route_decision"].tasks
    assert len(tasks) == 2
    assert tasks[1].depends_on == [tasks[0].task_id]
    assert [item.status for item in result["task_results"]] == [
        TaskStatus.SUCCESS, TaskStatus.SUCCESS,
    ]


async def test_v5_ambiguous_entity_clarifies_before_worldline_dispatch():
    result, tools, song, chat = await _graph(
        "这个人和哪些乐队有证据支持的关系？", ["worldline"],
    )
    assert result["route_decision"].needs_clarification
    assert not result["task_results"]
    assert not tools and not song and not chat
