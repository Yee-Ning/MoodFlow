import asyncio
import unittest
from unittest.mock import AsyncMock, patch

from fastapi.testclient import TestClient

from app.agent.foundation.subgraphs.worldline.models import (
    EntityType,
    EvidenceSource,
    EvidenceType,
    ResolvedEntity,
    WorldLineBranch,
    WorldLineBranchTarget,
    WorldLineBranchType,
    WorldLineGraph,
    WorldLineNode,
    WorldLineNodeType,
    WorldLineResult,
    WorldLineSessionResponse,
)
from app.agent.foundation.subgraphs.worldline.service import (
    WorldLineExecutionError,
    generate_worldline,
)
from app.main import app


def result_fixture() -> WorldLineResult:
    root = ResolvedEntity(
        entity_id="netease:song:185809",
        entity_type=EntityType.SONG,
        name="晴天",
        artist="周杰伦",
        album="叶惠美",
        source="netease",
    )
    node = WorldLineNode(
        id=root.entity_id,
        type=WorldLineNodeType.SONG,
        name=root.name,
    )
    source = EvidenceSource(
        id="music-api:song:185809",
        source_type=EvidenceType.MUSIC_API,
        title="晴天目录资料",
        source="MoodFlow Java Music Service",
    )
    return WorldLineResult(
        root_entity=root,
        graph=WorldLineGraph(root=node.id, nodes=[node]),
        narrative="当前内容仅基于音乐目录事实。[music-api:song:185809]",
        branches=[
            WorldLineBranch(
                id=f"branch-{index}",
                title=f"晴天探索线 {index}",
                description="沿当前真实歌曲实体继续探索。",
                branch_type=WorldLineBranchType.RELATED_WORK,
                target_entity=WorldLineBranchTarget(
                    id=node.id,
                    type=node.type,
                    name=node.name,
                ),
            )
            for index in range(1, 4)
        ],
        sources=[source],
        citations=[source],
    )


class WorldLineApiTests(unittest.TestCase):
    def setUp(self) -> None:
        self.client = TestClient(app)

    def test_worldline_returns_structured_json_result(self) -> None:
        generator = AsyncMock(return_value=result_fixture())
        with patch("app.api.worldline.generate_worldline", generator):
            response = self.client.post(
                "/api/ai/worldline",
                json={"query": "歌曲《晴天》的世界线"},
            )

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["root_entity"]["entity_type"], "song")
        self.assertEqual(payload["graph"]["root"], "netease:song:185809")
        self.assertEqual(len(payload["branches"]), 3)
        self.assertEqual(payload["citations"][0]["id"], "music-api:song:185809")
        generator.assert_awaited_once_with("歌曲《晴天》的世界线")

    def test_worldline_ambiguous_entity_returns_conflict(self) -> None:
        generator = AsyncMock(
            side_effect=WorldLineExecutionError(
                "entity_ambiguous",
                "请明确是歌曲还是专辑。",
                409,
            )
        )
        with patch("app.api.worldline.generate_worldline", generator):
            response = self.client.post(
                "/api/ai/worldline",
                json={"query": "七里香"},
            )

        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.json()["detail"]["code"], "entity_ambiguous")

    def test_worldline_rejects_blank_query_before_graph(self) -> None:
        response = self.client.post(
            "/api/ai/worldline",
            json={"query": "   "},
        )

        self.assertEqual(response.status_code, 422)

    def test_service_rejects_graph_without_task_result(self) -> None:
        graph = AsyncMock()
        graph.ainvoke.return_value = {}

        with self.assertRaises(WorldLineExecutionError) as raised:
            asyncio.run(generate_worldline("晴天", graph=graph))

        self.assertEqual(raised.exception.code, "missing_worldline_result")
        self.assertEqual(raised.exception.status_code, 502)

    def test_session_routes_keep_full_result_and_validate_input(self) -> None:
        fixture = result_fixture().model_dump(mode="json")
        session_result = WorldLineSessionResponse.model_validate({
            **fixture,
            "session_id": "session-1", "current_node": fixture["graph"]["root"],
            "visited_nodes": [fixture["graph"]["root"]],
            "available_actions": [{
                "id": "wlb:1:test", "target_node_id": fixture["graph"]["root"],
                "action_type": "explore_node", "enabled": False,
                "disabled_reason": "already_visited",
            }],
            "exploration_depth": 0, "revision": 1,
        })
        service = AsyncMock()
        service.create.return_value = session_result
        service.recover.return_value = session_result
        service.explore.return_value = session_result
        with patch("app.api.worldline.get_worldline_session_service", return_value=service):
            created = self.client.post("/api/ai/worldline/sessions", json={"query": "晴天"})
            recovered = self.client.get("/api/ai/worldline/sessions/session-1")
            explored = self.client.post("/api/ai/worldline/explore", json={
                "session_id": "session-1", "branch_id": "wlb:1:test",
            })
            invalid = self.client.post("/api/ai/worldline/explore", json={"session_id": "session-1"})
        self.assertEqual(created.status_code, 200)
        self.assertEqual(recovered.status_code, 200)
        self.assertEqual(explored.status_code, 200)
        self.assertEqual(invalid.status_code, 422)
        self.assertEqual(created.json()["session_id"], "session-1")
        self.assertEqual(created.json()["citations"], fixture["citations"])
        self.assertEqual(recovered.json()["visited_nodes"], [fixture["graph"]["root"]])
        service.create.assert_awaited_once_with("晴天")
        service.recover.assert_awaited_once_with("session-1")
        service.explore.assert_awaited_once_with("session-1", "wlb:1:test")

    def test_session_errors_return_stable_code(self) -> None:
        service = AsyncMock()
        service.explore.side_effect = WorldLineExecutionError("invalid_branch_id", "分支不存在或已过期。", 404)
        with patch("app.api.worldline.get_worldline_session_service", return_value=service):
            response = self.client.post("/api/ai/worldline/explore", json={
                "session_id": "session-1", "branch_id": "expired",
            })
        self.assertEqual(response.status_code, 404)
        self.assertEqual(response.json()["detail"]["code"], "invalid_branch_id")

    def test_session_recovery_errors_return_stable_code(self) -> None:
        service = AsyncMock()
        service.recover.side_effect = WorldLineExecutionError(
            "session_not_found",
            "WorldLine 会话不存在。",
            404,
        )
        with patch("app.api.worldline.get_worldline_session_service", return_value=service):
            response = self.client.get("/api/ai/worldline/sessions/expired")
        self.assertEqual(response.status_code, 404)
        self.assertEqual(response.json()["detail"]["code"], "session_not_found")
        service.recover.assert_awaited_once_with("expired")


if __name__ == "__main__":
    unittest.main()
