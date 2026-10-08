"""Semantic domain routing can reuse fully covered player-command rules."""

from unittest.mock import AsyncMock

import pytest
from langchain_core.messages import AIMessage

from app.agent.foundation.routing import (
    IntentItem,
    IntentSource,
    IntentType,
    RoutingContext,
)
from app.agent.foundation.subgraphs.tool_calling import create_tool_calling_subgraph
from tests.test_tool_calling_subgraph import invoke


def semantic_intent(text):
    return IntentItem(
        intent=IntentType.TOOL_CALLING,
        sub_intent=None,
        confidence=1,
        source=IntentSource.LLM,
        evidence=text,
        start=0,
        end=len(text),
        order=0,
    )


@pytest.mark.asyncio
async def test_semantic_pause_uses_existing_direct_path_without_model_call():
    assistant = AsyncMock(side_effect=AssertionError("unexpected model call"))
    graph = create_tool_calling_subgraph(assistant=assistant)
    result = await invoke(
        graph,
        text="暂停播放",
        intents=[semantic_intent("暂停播放")],
        context=RoutingContext(has_current_song=True, current_song_id=186016),
    )
    task = result["task_result"]
    assert task.status.value == "success"
    assert [action["name"] for action in task.data["client_actions"]] == [
        "pause_player"
    ]
    assert task.data["client_actions"][0]["task_id"] == "tool-task"
    assistant.assert_not_awaited()


@pytest.mark.asyncio
async def test_semantic_pause_keeps_existing_missing_player_validation():
    assistant = AsyncMock(side_effect=AssertionError("unexpected model call"))
    result = await invoke(
        create_tool_calling_subgraph(assistant=assistant),
        text="暂停播放",
        intents=[semantic_intent("暂停播放")],
    )
    assert result["task_result"].status.value == "needs_input"
    assert result["task_result"].data["client_actions"] == []
    assistant.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "text", ["不要暂停播放", "搜索暂停播放", "暂停播放并搜索晴天", "如果合适就暂停播放"]
)
async def test_uncovered_or_negated_clause_is_not_promoted_to_direct_action(text):
    assistant = AsyncMock(return_value=AIMessage(content="没有执行播放器操作。"))
    result = await invoke(
        create_tool_calling_subgraph(assistant=assistant),
        text=text,
        intents=[semantic_intent(text)],
        context=RoutingContext(has_current_song=True, current_song_id=186016),
    )
    assert result["task_result"].data["client_actions"] == []
    assistant.assert_awaited_once()
