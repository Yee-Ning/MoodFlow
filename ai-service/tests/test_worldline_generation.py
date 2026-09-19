import asyncio
import json
import unittest

from app.agent.foundation.subgraphs.worldline.generation import (
    StructuredWorldLineGenerator,
    WorldLineGenerationError,
)
from app.agent.foundation.subgraphs.worldline.models import (
    EntityType,
    EvidenceSource,
    EvidenceType,
    MusicEntityFacts,
    MusicKnowledgeDocument,
    ResolvedEntity,
    WorldLineGraph,
    WorldLineNode,
    WorldLineNodeType,
)


class ScriptedRuntime:
    def __init__(self, responses: list[str]) -> None:
        self.responses = list(responses)
        self.calls: list[dict] = []

    async def generate(self, payload, **kwargs):
        self.calls.append({"payload": payload, **kwargs})
        if not self.responses:
            raise AssertionError("runtime called more times than expected")
        return self.responses.pop(0)


class IncompleteRuntime(ScriptedRuntime):
    async def generate(self, payload, **kwargs):
        if not self.calls:
            self.calls.append({"payload": payload, **kwargs})
            raise WorldLineGenerationError("incomplete structured response")
        return await super().generate(payload, **kwargs)


def entity() -> ResolvedEntity:
    return ResolvedEntity(
        entity_id="netease:song:1",
        entity_type=EntityType.SONG,
        name="晴天",
        artist="周杰伦",
        album="叶惠美",
        source="netease",
    )


def graph() -> WorldLineGraph:
    return WorldLineGraph(
        root="netease:song:1",
        nodes=[
            WorldLineNode(
                id="netease:song:1",
                type=WorldLineNodeType.SONG,
                name="晴天",
            ),
            WorldLineNode(
                id="netease:artist:2",
                type=WorldLineNodeType.ARTIST,
                name="周杰伦",
            ),
            WorldLineNode(
                id="netease:album:3",
                type=WorldLineNodeType.ALBUM,
                name="叶惠美",
            ),
        ],
    )


def valid_branches() -> str:
    return json.dumps(
        {
            "branches": [
                {
                    "id": "artist-line",
                    "title": "周杰伦作品线",
                    "description": "从演唱者继续探索。",
                    "branch_type": "artist",
                    "target_entity": {
                        "id": "netease:artist:2",
                        "type": "artist",
                        "name": "周杰伦",
                    },
                },
                {
                    "id": "album-line",
                    "title": "叶惠美专辑线",
                    "description": "从所属专辑继续探索。",
                    "branch_type": "album",
                    "target_entity": {
                        "id": "netease:album:3",
                        "type": "album",
                        "name": "叶惠美",
                    },
                },
                {
                    "id": "song-line",
                    "title": "晴天作品线",
                    "description": "继续查看这首作品的关系。",
                    "branch_type": "related_work",
                    "target_entity": {
                        "id": "netease:song:1",
                        "type": "song",
                        "name": "晴天",
                    },
                },
            ]
        },
        ensure_ascii=False,
    )


class WorldLineGenerationTests(unittest.TestCase):
    def test_narrative_retries_invalid_structured_output_and_marks_empty_rag(self) -> None:
        runtime = ScriptedRuntime(
            [
                "not-json",
                '{"narrative":"《晴天》连接了歌曲、歌手与专辑。",'
                '"citation_ids":[]}',
            ]
        )
        generator = StructuredWorldLineGenerator(runtime)

        narrative = asyncio.run(
            generator.generate_narrative(
                entity(),
                MusicEntityFacts(entity=entity()),
                [],
                graph(),
                [],
            )
        )

        self.assertTrue(narrative.narrative.startswith("当前未检索到补充音乐知识"))
        self.assertEqual([call["retry"] for call in runtime.calls], [False, True])

    def test_narrative_retries_unknown_citation_then_returns_grounded_ids(self) -> None:
        evidence = EvidenceSource(
            id="knowledge:parent-1",
            source_type=EvidenceType.KNOWLEDGE,
            title="晴天资料",
            source="测试知识库",
            excerpt="《晴天》收录于《叶惠美》。",
        )
        document = MusicKnowledgeDocument(
            id="parent-1",
            title="晴天资料",
            content="《晴天》收录于《叶惠美》。",
            source="测试知识库",
        )
        runtime = ScriptedRuntime(
            [
                '{"narrative":"《晴天》收录于《叶惠美》。[knowledge:missing]",'
                '"citation_ids":["knowledge:missing"]}',
                '{"narrative":"《晴天》收录于《叶惠美》。[knowledge:parent-1]",'
                '"citation_ids":["knowledge:parent-1"]}',
            ]
        )

        output = asyncio.run(
            StructuredWorldLineGenerator(runtime).generate_narrative(
                entity(),
                MusicEntityFacts(entity=entity()),
                [document],
                graph(),
                [evidence],
            )
        )

        self.assertEqual(output.citation_ids, ["knowledge:parent-1"])
        self.assertEqual([call["retry"] for call in runtime.calls], [False, True])

    def test_narrative_derives_citation_order_from_valid_inline_sources(self) -> None:
        first = EvidenceSource(
            id="knowledge:first",
            source_type=EvidenceType.KNOWLEDGE,
            title="第一份资料",
            source="测试知识库",
            excerpt="第一份资料。",
        )
        second = EvidenceSource(
            id="knowledge:second",
            source_type=EvidenceType.KNOWLEDGE,
            title="第二份资料",
            source="测试知识库",
            excerpt="第二份资料。",
        )
        runtime = ScriptedRuntime([
            json.dumps({
                "narrative": "第一条事实。[knowledge:second] 第二条事实。[knowledge:first]",
                "citation_ids": ["knowledge:first", "knowledge:second"],
            }, ensure_ascii=False),
        ])

        output = asyncio.run(
            StructuredWorldLineGenerator(runtime).generate_narrative(
                entity(), MusicEntityFacts(entity=entity()),
                [MusicKnowledgeDocument(
                    id="parent-1", title="资料", content="资料内容", source="测试知识库",
                )], graph(), [first, second],
            )
        )

        self.assertEqual(output.citation_ids, ["knowledge:second", "knowledge:first"])
        self.assertEqual(len(runtime.calls), 1)

    def test_narrative_retries_incomplete_structured_response(self) -> None:
        runtime = IncompleteRuntime([
            '{"narrative":"《晴天》连接了歌曲、歌手与专辑。","citation_ids":[]}'
        ])

        output = asyncio.run(
            StructuredWorldLineGenerator(runtime).generate_narrative(
                entity(), MusicEntityFacts(entity=entity()), [], graph(), [],
            )
        )

        self.assertIn("当前未检索到补充音乐知识", output.narrative)
        self.assertEqual([call["retry"] for call in runtime.calls], [False, True])

    def test_branches_retry_target_not_in_graph(self) -> None:
        invalid = json.loads(valid_branches())
        invalid["branches"][0]["target_entity"]["id"] = "artist:invented"
        runtime = ScriptedRuntime(
            [json.dumps(invalid, ensure_ascii=False), valid_branches()]
        )
        generator = StructuredWorldLineGenerator(runtime)

        branches = asyncio.run(generator.generate_branches(entity(), graph(), []))

        self.assertEqual(len(branches), 3)
        self.assertEqual([call["retry"] for call in runtime.calls], [False, True])

    def test_branch_validation_failure_is_explicit_after_retry(self) -> None:
        runtime = ScriptedRuntime(['{"branches":[]}', '{"branches":[]}'])
        generator = StructuredWorldLineGenerator(runtime)

        with self.assertRaises(WorldLineGenerationError):
            asyncio.run(generator.generate_branches(entity(), graph(), []))


if __name__ == "__main__":
    unittest.main()
