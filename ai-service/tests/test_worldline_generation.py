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
from app.agent.structured_output import ModelOutput


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
            return ModelOutput(
                "", status="incomplete", incomplete_reason="max_output_tokens"
            )
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


def valid_branch_drafts() -> str:
    return json.dumps(
        {
            "branches": [
                {
                    "title": "周杰伦作品线",
                    "description": "从演唱者继续探索。",
                    "branch_type": "artist",
                    "target_node_id": "netease:artist:2",
                },
                {
                    "title": "叶惠美专辑线",
                    "description": "从所属专辑继续探索。",
                    "branch_type": "album",
                    "target_node_id": "netease:album:3",
                },
                {
                    "title": "晴天作品线",
                    "description": "继续查看这首作品的关系。",
                    "branch_type": "related_work",
                    "target_node_id": "netease:song:1",
                },
            ]
        },
        ensure_ascii=False,
    )


class WorldLineGenerationTests(unittest.TestCase):
    def test_exploration_branches_allow_fewer_than_three_and_reject_visited_target(self) -> None:
        runtime = ScriptedRuntime([
            json.dumps({"branches": [{
                "title": "重复歌曲", "description": "重复", "branch_type": "related_work",
                "target_node_id": "netease:song:1",
            }]}),
            json.dumps({"branches": [{
                "title": "探索周杰伦", "description": "继续探索", "branch_type": "artist",
                "target_node_id": "netease:artist:2",
            }]}),
        ])
        branches = asyncio.run(StructuredWorldLineGenerator(runtime).generate_exploration_branches(
            entity(), graph(), [], visited_nodes=["netease:song:1"],
        ))
        self.assertEqual([item.target_entity.id for item in branches], ["netease:artist:2"])
        self.assertEqual(len(runtime.calls), 2)
        self.assertEqual(runtime.calls[0]["payload"]["eligible_target_node_ids"],
                         ["netease:album:3", "netease:artist:2"])

    def test_exploration_with_no_targets_skips_llm(self) -> None:
        runtime = ScriptedRuntime([])
        worldline = graph()
        worldline = worldline.model_copy(
            update={
                "nodes": [
                    *worldline.nodes,
                    WorldLineNode(
                        id="genre:hip_hop",
                        type=WorldLineNodeType.GENRE,
                        name="Hip-Hop",
                    ),
                ]
            }
        )
        branches = asyncio.run(StructuredWorldLineGenerator(runtime).generate_exploration_branches(
            entity(), worldline, [],
            visited_nodes=["netease:song:1", "netease:artist:2", "netease:album:3"],
        ))
        self.assertEqual(branches, [])
        self.assertEqual(runtime.calls, [])

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

    def test_narrative_draft_uses_one_generated_field_and_derived_citation(self) -> None:
        evidence = EvidenceSource(
            id="knowledge:parent-1", source_type=EvidenceType.KNOWLEDGE,
            title="晴天资料", source="测试知识库", excerpt="歌曲资料。",
        )
        runtime = ScriptedRuntime([
            '{"narrative":"《晴天》有一份补充资料。[knowledge:parent-1]"}'
        ])

        output = asyncio.run(
            StructuredWorldLineGenerator(runtime).generate_narrative(
                entity(), MusicEntityFacts(entity=entity()),
                [MusicKnowledgeDocument(
                    id="parent-1", title="晴天资料", content="歌曲资料。",
                    source="测试知识库",
                )], graph(), [evidence],
            )
        )

        self.assertEqual(output.citation_ids, ["knowledge:parent-1"])
        schema = runtime.calls[0]["schema"]
        self.assertEqual(schema["required"], ["narrative"])
        self.assertNotIn("retrieved_knowledge", runtime.calls[0]["payload"])
        self.assertEqual(runtime.calls[0]["payload"]["sources"][0]["excerpt"], "歌曲资料。")

    def test_narrative_missing_inline_citation_fails_closed_after_retry(self) -> None:
        evidence = EvidenceSource(
            id="knowledge:parent-1", source_type=EvidenceType.KNOWLEDGE,
            title="晴天资料", source="测试知识库", excerpt="歌曲资料。",
        )
        runtime = ScriptedRuntime([
            '{"narrative":"这是一段没有引用的具体音乐事实。"}',
            '{"narrative":"这仍是一段没有引用的具体音乐事实。"}',
        ])

        with self.assertRaises(WorldLineGenerationError):
            asyncio.run(
                StructuredWorldLineGenerator(runtime).generate_narrative(
                    entity(), MusicEntityFacts(entity=entity()),
                    [MusicKnowledgeDocument(
                        id="parent-1", title="晴天资料", content="歌曲资料。",
                        source="测试知识库",
                    )], graph(), [evidence],
                )
            )
        self.assertIn("missing_citation", runtime.calls[1]["instructions"])

    def test_branch_draft_maps_existing_nodes_without_model_generated_ids(self) -> None:
        runtime = ScriptedRuntime([valid_branch_drafts()])

        branches = asyncio.run(
            StructuredWorldLineGenerator(runtime).generate_branches(
                entity(), graph(), [],
            )
        )

        self.assertEqual([item.id for item in branches], ["branch:1", "branch:2", "branch:3"])
        self.assertEqual(branches[0].target_entity.name, "周杰伦")
        properties = runtime.calls[0]["schema"]["$defs"]["BranchDraft"]["properties"]
        self.assertIn("target_node_id", properties)
        self.assertNotIn("target_entity", properties)

    def test_branch_draft_duplicate_target_retries_without_guessing(self) -> None:
        invalid = json.loads(valid_branch_drafts())
        invalid["branches"][1]["target_node_id"] = "netease:artist:2"
        runtime = ScriptedRuntime([
            json.dumps(invalid, ensure_ascii=False), valid_branch_drafts(),
        ])

        branches = asyncio.run(
            StructuredWorldLineGenerator(runtime).generate_branches(
                entity(), graph(), [],
            )
        )

        self.assertEqual(len(branches), 3)
        self.assertIn("duplicate_branch_target", runtime.calls[1]["instructions"])

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
