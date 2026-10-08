from langchain_core.messages import AIMessage
import pytest

from app.agent.foundation.subgraphs.tool_calling.assistant import (
    all_tool_calling_tools,
    answer_tool_calling_task,
)
from app.agent.handoff.search_scope import SEARCH_TOOLS, scoped_search_tools
from app.agent.foundation.routing import RoutingContext
from tests.test_handoff_runtime import ToolModel


@pytest.mark.parametrize(
    "text", ["搜索周杰伦的晴天", "搜索周杰伦的晴天，不要播放或收藏", "搜索歌手陈奕迅"]
)
def test_read_only_search_binds_catalog_tools_without_mutation_schemas(text):
    tools = scoped_search_tools(text, all_tool_calling_tools())
    assert {item.name for item in tools} == SEARCH_TOOLS
    assert (
        len(str([item.tool_call_schema.model_json_schema() for item in tools]))
        < len(
            str(
                [
                    item.tool_call_schema.model_json_schema()
                    for item in all_tool_calling_tools()
                ]
            )
        )
        / 3
    )


@pytest.mark.parametrize(
    "text",
    [
        "搜索晴天并播放",
        "搜索晴天，然后收藏",
        "搜索晴天然后查看歌词",
        "不要搜索晴天",
        "分析当前歌曲",
        "搜索晴天；搜索十年",
        "搜索当前这首歌",
        "搜索正在播放的歌曲",
        "搜索这首歌",
    ],
)
def test_mixed_negated_or_non_search_tasks_keep_existing_tool_domain(text):
    original = all_tool_calling_tools()
    assert scoped_search_tools(text, original) == original


def test_unknown_injected_tools_are_not_removed():
    original = [
        tool for tool in all_tool_calling_tools() if tool.name == "get_song_lyrics"
    ]
    assert scoped_search_tools("搜索晴天", original) == original


@pytest.mark.asyncio
async def test_search_prompt_limits_expansion_and_requires_truthful_version_names():
    model = ToolModel(responses=[AIMessage(content="没有准确原曲，以下是目录候选。")])
    await answer_tool_calling_task(
        [], "搜索晴天", RoutingContext(), [], model=model, search_only=True
    )
    text = "\n".join(str(item.content) for item in model.seen_messages[0])
    assert "最多进行一次" in text
    assert "不得冒充" in text
