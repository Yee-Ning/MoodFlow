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


if __name__ == "__main__":
    unittest.main()
