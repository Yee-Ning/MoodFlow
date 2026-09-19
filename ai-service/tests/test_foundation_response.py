import asyncio
import json
import unittest

from pydantic import ValidationError

from app.agent.foundation.models import TaskResult, TaskStatus
from app.agent.foundation.response import foundation_response
from app.agent.foundation.routing import RouteKey
from app.agent.foundation.subgraphs.worldline.graph import create_worldline_subgraph
from app.agent.foundation.subgraphs.worldline.models import EntityType
from app.schemas.chat import FoundationResponse, FoundationTaskOutput
from tests.test_worldline_graph import (
    EmptyRetriever,
    FakeGenerator,
    FakeMusicService,
    entity,
    invoke,
)


class FoundationResponseTests(unittest.TestCase):
    def test_public_contract_rejects_empty_success_and_unknown_citation(self) -> None:
        base = {
            "task_id": "worldline-1", "status": "success", "subgraph": "worldline",
            "answer": "已有依据。", "sources": [{"id": "knowledge:known"}],
        }
        with self.assertRaises(ValidationError):
            FoundationTaskOutput.model_validate({**base, "answer": " "})
        with self.assertRaises(ValidationError):
            FoundationTaskOutput.model_validate({
                **base, "citations": [{"id": "knowledge:invented"}],
            })
        with self.assertRaises(ValidationError):
            FoundationResponse.model_validate({
                "status": "success", "message": "完成", "results": [
                    {**base, "status": "failed"},
                ],
            })

    def test_each_subgraph_uses_one_json_contract(self) -> None:
        song = entity(1, EntityType.SONG, "晴天")
        worldline_graph = create_worldline_subgraph(
            music_service=FakeMusicService(song),
            retriever=EmptyRetriever(),
            generator=FakeGenerator(grounded=True),
        )
        worldline = asyncio.run(invoke(worldline_graph, "歌曲《晴天》的世界线"))[
            "task_result"
        ]
        tasks = [
            TaskResult(
                task_id="song", route_key=RouteKey.CURRENT_SONG,
                status=TaskStatus.SUCCESS, content="从歌词来看很怀旧。",
                data={"rag_used": False},
            ),
            TaskResult(
                task_id="tool", route_key=RouteKey.TOOL_CALLING,
                status=TaskStatus.SUCCESS, content="已读取歌单。",
                data={
                    "tool_calls": 1,
                    "tool_call_details": [{"name": "get_user_playlists", "status": "success"}],
                },
            ),
            TaskResult(
                task_id="chat", route_key=RouteKey.GENERAL_CHAT,
                status=TaskStatus.SUCCESS, content="你好。",
            ),
            worldline,
        ]

        response = foundation_response("全部完成", tasks)
        payload = json.loads(response.model_dump_json())

        self.assertEqual(payload["status"], "success")
        self.assertEqual(
            [item["subgraph"] for item in payload["results"]],
            ["current_song", "tool_calling", "general_chat", "worldline"],
        )
        for item in payload["results"]:
            self.assertEqual(
                set(item),
                {"task_id", "status", "subgraph", "answer", "citations",
                 "sources", "tool_calls", "metadata"},
            )
        self.assertEqual(payload["results"][1]["tool_calls"][0]["name"], "get_user_playlists")
        self.assertEqual(payload["results"][3]["citations"][0]["id"], "music-api:song:1")
        self.assertEqual(payload["results"][3]["sources"][0]["id"], "music-api:song:1")

    def test_failed_and_input_states_remain_distinct(self) -> None:
        needs_input = TaskResult(
            task_id="song", route_key=RouteKey.CURRENT_SONG,
            status=TaskStatus.NEEDS_INPUT, content="请先播放歌曲。",
        )
        failed = TaskResult(
            task_id="tool", route_key=RouteKey.TOOL_CALLING,
            status=TaskStatus.FAILED, content="音乐服务不可用。",
            error="音乐服务不可用。",
        )

        self.assertEqual(
            foundation_response("请先播放歌曲。", [needs_input]).status,
            TaskStatus.NEEDS_INPUT,
        )
        self.assertEqual(
            foundation_response("任务失败。", [needs_input, failed]).status,
            TaskStatus.FAILED,
        )
        self.assertEqual(
            foundation_response("请说明需求。", [], needs_clarification=True).status,
            TaskStatus.NEEDS_INPUT,
        )
