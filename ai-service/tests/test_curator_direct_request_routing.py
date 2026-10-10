"""Named playlist operations keep the original execution and receipt chain."""

import pytest
from langchain_core.messages import AIMessage

from app.agent.foundation.models import TaskStatus
from app.agent.handoff.curator import curation_request
from tests.test_curator_curation_v1 import Catalog, curate
from tests.test_handoff_runtime import ToolModel
from tests import test_write_reconciliation as write_helpers

journal = write_helpers.journal


@pytest.mark.parametrize("text", [
    "新建私密歌单 周末收藏",
    "创建公开歌单 MorningDrive",
    "新建一个歌单 春日夜跑",
    "把晴天加入歌单 周末收藏",
    "从歌单 深夜加班移除晴天",
    "删除歌单 放松一下",
])
def test_named_direct_playlist_operations_do_not_enter_curation(text):
    assert curation_request(text) is None


@pytest.mark.parametrize("text", [
    "创建雨天夜跑歌单，20首",
    "生成火星殖民地音乐歌单",
    "策展一份爵士歌单",
    "生成90年代港乐歌单，小众一些",
])
def test_scene_or_explicit_curation_requests_keep_curation(text):
    assert curation_request(text) is not None


@pytest.mark.asyncio
async def test_named_private_creation_uses_original_tools_without_preference_or_catalog_reads(monkeypatch, journal):
    catalog = Catalog(monkeypatch, count=0)
    model = ToolModel(responses=[
        AIMessage(content="", tool_calls=[{
            "name": "create_playlist_tool", "args": {"name": "周末收藏", "privacy": 10},
            "id": "named-create", "type": "tool_call",
        }]),
        AIMessage(content="已收到创建歌单的成功回执。"),
    ])
    result = await curate("新建私密歌单 周末收藏", catalog, journal, model=model, owned=True)
    assert result.status is TaskStatus.SUCCESS, result.content
    assert catalog.calls == [("create", "周末收藏", 10)]
    assert catalog.exists and catalog.name == "周末收藏"
    assert "curation" not in result.data
