import asyncio
import unittest

from langchain_core.messages import HumanMessage

from app.agent.checkpointing import create_memory_checkpointer
from app.agent.foundation import (
    RouteKey,
    build_main_graph,
    create_main_state,
)
from app.agent.foundation.models import TaskStatus
from app.agent.foundation.routing import RoutingContext
from app.agent.foundation.subgraphs.worldline.graph import (
    create_worldline_subgraph,
)
from app.agent.foundation.subgraphs.worldline.models import (
    EntityType,
    EvidenceSource,
    EvidenceType,
    MusicEntityFacts,
    MusicFact,
    MusicKnowledgeDocument,
    NarrativeOutput,
    ResolvedEntity,
    WorldLineBranch,
    WorldLineBranchTarget,
    WorldLineBranchType,
)
from app.agent.foundation.subgraphs.worldline.session import WorldLineSessionService


def entity(entity_id: int, entity_type: EntityType, name: str) -> ResolvedEntity:
    return ResolvedEntity(
        entity_id=f"netease:{entity_type.value}:{entity_id}",
        entity_type=entity_type,
        name=name,
        artist=name if entity_type is EntityType.ARTIST else "测试歌手",
        album="测试专辑" if entity_type is EntityType.SONG else None,
        source="netease",
    )


class FakeMusicService:
    def __init__(
        self,
        root: ResolvedEntity | None,
        related: list[ResolvedEntity] | None = None,
        *,
        search_error: Exception | None = None,
        facts_error: Exception | None = None,
    ) -> None:
        self.root = root
        self.related = related or []
        self.search_error = search_error
        self.facts_error = facts_error

    async def search_entities(self, query, expected_entity_type, *, limit=5):
        if self.search_error is not None:
            raise self.search_error
        return [self.root] if self.root is not None else []

    async def load_entity_facts(self, root):
        if self.facts_error is not None:
            raise self.facts_error
        source = EvidenceSource(
            id=f"music-api:{root.entity_type.value}:1",
            source_type=EvidenceType.MUSIC_API,
            title="测试目录事实",
            source="MoodFlow Java Music Service",
        )
        facts = []
        for index, related in enumerate(self.related):
            if root.entity_type is EntityType.SONG:
                relation = (
                    "performed_by"
                    if related.entity_type is EntityType.ARTIST
                    else "belongs_to_album"
                )
                subject, target = root, related
            else:
                relation = "performed_by"
                subject, target = related, root
            facts.append(
                MusicFact(
                    id=f"fact:{index}",
                    subject_id=subject.entity_id,
                    predicate=relation,
                    value=f"{subject.name} {relation} {target.name}",
                    object_entity_id=target.entity_id,
                    evidence_ids=[source.id],
                )
            )
        return MusicEntityFacts(
            entity=root,
            related_entities=self.related,
            facts=facts,
            sources=[source],
        )


class EmptyRetriever:
    async def retrieve(self, entity, query):
        return []


class FailingRetriever:
    async def retrieve(self, entity, query):
        raise RuntimeError("test retriever unavailable")


class ManyDocsRetriever:
    async def retrieve(self, entity, query):
        return [
            MusicKnowledgeDocument(
                id=f"doc-{index}",
                title=f"资料 {index}",
                content="测试背景资料。",
                source="test-index",
            )
            for index in range(25)
        ]


class CapturingRetriever:
    def __init__(self) -> None:
        self.plan = None

    async def retrieve(self, entity, plan):
        self.plan = plan
        return []


class SemanticDocsRetriever:
    async def retrieve(self, entity, plan):
        evidence = "周杰伦的音乐风格属于 Hip-Hop。"
        return [
            MusicKnowledgeDocument(
                id="parent-semantic-1",
                title="周杰伦风格资料",
                content=f"背景。{evidence}更多资料。",
                source="test-knowledge",
                url="https://example.com/jay-chou-style",
                metadata={
                    "parent_id": "parent-semantic-1",
                    "chunk_id": "child-semantic-1",
                    "context_version": "context-v1",
                    "retrieval_version": "retrieval-v1",
                    "sufficiency_status": "sufficient",
                    "supported_claims": [
                        {
                            "claim_id": "claim:000000000000000000000001",
                            "statement": "周杰伦具有 Hip-Hop 风格",
                            "subject_entity_id": "artist:jay_chou",
                            "predicate": "has_genre",
                            "object_entity_id": "genre:hip_hop",
                            "source_chunk_id": "child-semantic-1",
                            "evidence_span": evidence,
                            "extraction_confidence": 0.94,
                            "verification_confidence": 0.96,
                        }
                    ],
                },
            )
        ]


class EraDocsRetriever:
    async def retrieve(self, entity, plan):
        evidence = "周杰伦在 2004 年属于 2000s 华语音乐时期。"
        return [MusicKnowledgeDocument(
            id="parent-era-1",
            title="周杰伦时代资料",
            content=f"背景。{evidence}更多资料。",
            source="test-knowledge",
            url="https://example.com/jay-chou-era",
            metadata={
                "parent_id": "parent-era-1",
                "chunk_id": "child-era-1",
                "context_version": "context-v1",
                "retrieval_version": "retrieval-v1",
                "sufficiency_status": "sufficient",
                "supported_claims": [{
                    "claim_id": "claim:000000000000000000000099",
                    "subject_entity_id": "artist:jay_chou",
                    "predicate": "belongs_to_era",
                    "object_value": "2000s",
                    "time": {"year": 2004},
                    "source_chunk_id": "child-era-1",
                    "evidence_span": evidence,
                    "extraction_confidence": 0.96,
                    "verification_confidence": 0.96,
                }],
            },
        )]


class SceneDocsRetriever:
    async def retrieve(self, entity, plan):
        evidence = "周杰伦在 2004 年属于 Taipei indie scene 音乐场景。"
        return [MusicKnowledgeDocument(
            id="parent-scene-1",
            title="周杰伦场景资料",
            content=f"背景。{evidence}更多资料。",
            source="test-knowledge",
            url="https://example.com/jay-chou-scene",
            metadata={
                "parent_id": "parent-scene-1",
                "chunk_id": "child-scene-1",
                "context_version": "context-v1",
                "retrieval_version": "retrieval-v1",
                "sufficiency_status": "sufficient",
                "supported_claims": [{
                    "claim_id": "claim:000000000000000000000098",
                    "subject_entity_id": "artist:jay_chou",
                    "predicate": "belongs_to_scene",
                    "object_value": "Taipei indie scene",
                    "scene_location": "Taipei",
                    "time": {"year": 2004},
                    "source_chunk_id": "child-scene-1",
                    "evidence_span": evidence,
                    "extraction_confidence": 0.96,
                    "verification_confidence": 0.96,
                }],
            },
        )]


class FakeGenerator:
    def __init__(
        self,
        *,
        fail_branches: bool = False,
        grounded: bool = False,
    ) -> None:
        self.fail_branches = fail_branches
        self.grounded = grounded

    async def generate_narrative(self, entity, facts, documents, graph, sources):
        scope = "仅基于目录事实" if not documents else "基于目录与知识资料"
        narrative = f"{scope}，{entity.name}连接了图中的真实音乐实体。"
        if not self.grounded:
            return narrative
        citation_id = sources[0].id
        return NarrativeOutput(
            narrative=f"{narrative}[{citation_id}]",
            citation_ids=[citation_id],
        )

    async def generate_branches(self, entity, graph, sources):
        if self.fail_branches:
            raise RuntimeError("invalid structured branches")
        nodes = graph.nodes
        chosen = [nodes[index % len(nodes)] for index in range(3)]
        branches = []
        for index, node in enumerate(chosen, start=1):
            if node.type.value == "artist":
                branch_type = WorldLineBranchType.ARTIST
            elif node.type.value == "album":
                branch_type = WorldLineBranchType.ALBUM
            else:
                branch_type = WorldLineBranchType.RELATED_WORK
            branches.append(
                WorldLineBranch(
                    id=f"branch-{index}",
                    title=f"探索 {node.name}",
                    description=f"沿图中 {node.name} 节点继续探索。",
                    branch_type=branch_type,
                    target_entity=WorldLineBranchTarget(
                        id=node.id,
                        type=node.type,
                        name=node.name,
                    ),
                )
            )
        return branches


class SemanticCitationGenerator(FakeGenerator):
    async def generate_narrative(self, entity, facts, documents, graph, sources):
        source = next(item for item in sources if item.id.startswith("knowledge:rel:"))
        return NarrativeOutput(
            narrative=f"周杰伦与 Hip-Hop 风格相关联。[{source.id}]",
            citation_ids=[source.id],
        )


def initial_state(query: str) -> dict:
    return {
        "messages": [HumanMessage(content=query)],
        "current_input": query,
        "task_id": "worldline-test",
        "routing_context": RoutingContext(),
        "user_query": query,
        "query_context": None,
        "query_analysis": None,
        "search_plan": None,
        "entity_query": None,
        "expected_entity_type": None,
        "candidate_entities": [],
        "entity_resolution": None,
        "resolved_entity": None,
        "entity_facts": None,
        "retrieved_docs": [],
        "worldline_graph": None,
        "narrative": None,
        "citation_ids": [],
        "branches": [],
        "sources": [],
        "error_code": None,
        "error": None,
        "answer": None,
        "task_result": None,
    }


async def invoke(graph, query: str) -> dict:
    return await graph.ainvoke(
        initial_state(query),
        {"configurable": {"thread_id": "worldline-thread"}},
    )


class WorldLineGraphTests(unittest.TestCase):
    def test_scene_relation_reaches_runtime_graph_only_for_scene_query(self) -> None:
        root = entity(2, EntityType.ARTIST, "周杰伦")
        graph = create_worldline_subgraph(
            music_service=FakeMusicService(root),
            retriever=SceneDocsRetriever(),
            generator=FakeGenerator(),
        )
        scene_output = asyncio.run(invoke(graph, "歌手周杰伦的音乐场景"))
        scene_result = scene_output["task_result"].data["worldline_result"]
        self.assertEqual(len(scene_result["scene_relations"]), 1)
        relation = scene_result["scene_relations"][0]
        self.assertEqual(relation["relation_type"], "belongs_to_scene")
        self.assertEqual(relation["scene"]["node_id"], "scene:taipei_indie_scene")
        self.assertEqual(relation["location"], "Taipei")
        edge = next(item for item in scene_result["graph"]["edges"] if item["relation"] == "belongs_to_scene")
        self.assertEqual(edge["evidence_ids"], [relation["evidence"][0]["source_id"]])

        ordinary = asyncio.run(invoke(graph, "探索歌手周杰伦的世界线"))
        ordinary_result = ordinary["task_result"].data["worldline_result"]
        self.assertEqual(ordinary_result["scene_relations"], [])
        self.assertNotIn("scene:taipei_indie_scene", {item["id"] for item in ordinary_result["graph"]["nodes"]})

    def test_scene_relation_survives_checkpointed_non_scene_exploration(self) -> None:
        root = entity(2, EntityType.ARTIST, "周杰伦")
        song = entity(7, EntityType.SONG, "七里香")

        class SceneCatalog(FakeMusicService):
            async def load_entity_facts(self, target):
                if target.entity_id == song.entity_id:
                    return MusicEntityFacts(
                        entity=target,
                        sources=[EvidenceSource(
                            id="music-api:song:7",
                            source_type=EvidenceType.MUSIC_API,
                            title="七里香目录", source="test-catalog",
                        )],
                    )
                return await super().load_entity_facts(target)

        graph = create_worldline_subgraph(
            music_service=SceneCatalog(root, [song]),
            retriever=SceneDocsRetriever(),
            generator=FakeGenerator(),
        )
        saver = create_memory_checkpointer()

        async def run():
            service = WorldLineSessionService(saver, turn_graph=graph)
            first = await service.create("歌手周杰伦的音乐场景")
            restored = await WorldLineSessionService(saver, turn_graph=graph).recover(first.session_id)
            action = next(
                item for item in restored.available_actions
                if item.target_node_id == song.entity_id and item.enabled
            )
            second = await service.explore(first.session_id, action.id)
            return first, restored, second

        first, restored, second = asyncio.run(run())
        self.assertEqual(restored.scene_relations, first.scene_relations)
        self.assertEqual(second.scene_relations, first.scene_relations)
        self.assertIn("scene:taipei_indie_scene", {item.id for item in second.graph.nodes})
        self.assertIn(
            first.scene_relations[0].evidence[0].source_id,
            {item.id for item in second.sources},
        )
        self.assertEqual(second.revision, 2)

    def test_era_relation_reaches_runtime_graph_and_dto_only_for_era_query(self) -> None:
        root = entity(2, EntityType.ARTIST, "周杰伦")
        graph = create_worldline_subgraph(
            music_service=FakeMusicService(root),
            retriever=EraDocsRetriever(),
            generator=FakeGenerator(),
        )

        era_output = asyncio.run(invoke(graph, "歌手周杰伦的 2000 年代音乐时期"))
        era_result = era_output["task_result"].data["worldline_result"]
        self.assertEqual(len(era_result["era_relations"]), 1)
        relation = era_result["era_relations"][0]
        self.assertEqual(relation["relation_type"], "belongs_to_era")
        self.assertEqual(relation["era"]["node_id"], "era:2000s")
        self.assertEqual(relation["start_year"], 2004)
        edge = next(item for item in era_result["graph"]["edges"] if item["relation"] == "belongs_to_era")
        self.assertEqual(edge["evidence_ids"], [relation["evidence"][0]["source_id"]])

        ordinary_output = asyncio.run(invoke(graph, "探索歌手周杰伦的世界线"))
        ordinary_result = ordinary_output["task_result"].data["worldline_result"]
        self.assertEqual(ordinary_result["era_relations"], [])
        self.assertNotIn("era:2000s", {item["id"] for item in ordinary_result["graph"]["nodes"]})

    def test_era_relation_survives_checkpointed_non_era_exploration(self) -> None:
        root = entity(2, EntityType.ARTIST, "周杰伦")
        song = entity(7, EntityType.SONG, "七里香")

        class EraCatalog(FakeMusicService):
            async def load_entity_facts(self, target):
                if target.entity_id == song.entity_id:
                    return MusicEntityFacts(
                        entity=target,
                        sources=[EvidenceSource(
                            id="music-api:song:7",
                            source_type=EvidenceType.MUSIC_API,
                            title="七里香目录", source="test-catalog",
                        )],
                    )
                return await super().load_entity_facts(target)

        graph = create_worldline_subgraph(
            music_service=EraCatalog(root, [song]),
            retriever=EraDocsRetriever(),
            generator=FakeGenerator(),
        )
        saver = create_memory_checkpointer()

        async def run():
            service = WorldLineSessionService(saver, turn_graph=graph)
            first = await service.create("歌手周杰伦的 2000 年代音乐时期")
            restored = await WorldLineSessionService(saver, turn_graph=graph).recover(first.session_id)
            action = next(
                item for item in restored.available_actions
                if item.target_node_id == song.entity_id and item.enabled
            )
            second = await service.explore(first.session_id, action.id)
            return first, restored, second

        first, restored, second = asyncio.run(run())
        self.assertEqual(restored.era_relations, first.era_relations)
        self.assertEqual(second.era_relations, first.era_relations)
        self.assertIn("era:2000s", {item.id for item in second.graph.nodes})
        self.assertIn(
            first.era_relations[0].evidence[0].source_id,
            {item.id for item in second.sources},
        )
        self.assertEqual(second.revision, 2)

    def test_artist_semantic_relation_reaches_graph_and_citations(self) -> None:
        root = entity(2, EntityType.ARTIST, "周杰伦")
        graph = create_worldline_subgraph(
            music_service=FakeMusicService(root),
            retriever=SemanticDocsRetriever(),
            generator=SemanticCitationGenerator(),
        )

        output = asyncio.run(invoke(graph, "探索歌手周杰伦的世界线"))

        result = output["task_result"].data["worldline_result"]
        semantic_edge = next(
            edge
            for edge in result["graph"]["edges"]
            if edge["relation"] == "associated_with"
        )
        self.assertEqual(semantic_edge["source"], root.entity_id)
        self.assertEqual(semantic_edge["target"], "genre:hip_hop")
        source_id = semantic_edge["evidence_ids"][0]
        source = next(item for item in result["sources"] if item["id"] == source_id)
        self.assertEqual(source["excerpt"], "周杰伦的音乐风格属于 Hip-Hop。")
        self.assertEqual(result["citations"], [source])
        self.assertIn(f"[{source_id}]", result["narrative"])

    def test_grounded_narrative_citations_reach_worldline_result(self) -> None:
        song = entity(1, EntityType.SONG, "晴天")
        graph = create_worldline_subgraph(
            music_service=FakeMusicService(song),
            retriever=EmptyRetriever(),
            generator=FakeGenerator(grounded=True),
        )

        output = asyncio.run(invoke(graph, "歌曲《晴天》的世界线"))
        result = output["task_result"].data["worldline_result"]

        self.assertEqual(
            result["citations"][0]["id"],
            "music-api:song:1",
        )
        self.assertIn("[music-api:song:1]", result["narrative"])

    def test_main_graph_receives_only_worldline_result_payload(self) -> None:
        song = entity(1, EntityType.SONG, "晴天")
        subgraph = create_worldline_subgraph(
            music_service=FakeMusicService(
                song,
                [
                    entity(2, EntityType.ARTIST, "周杰伦"),
                    entity(3, EntityType.ALBUM, "叶惠美"),
                ],
            ),
            retriever=EmptyRetriever(),
            generator=FakeGenerator(),
        )
        main_graph = build_main_graph(
            development_routes=(RouteKey.WORLDLINE,),
            worldline_subgraph=subgraph,
        ).compile()
        query = "歌曲《晴天》的世界线"

        output = asyncio.run(
            main_graph.ainvoke(
                create_main_state(
                    query,
                    messages=[HumanMessage(content=query)],
                ),
                {"configurable": {"thread_id": "main-worldline"}},
            )
        )

        task_id = output["route_decision"].tasks[0].task_id
        task_result = next(
            item for item in output["task_results"] if item.task_id == task_id
        )
        self.assertEqual(set(task_result.data), {"worldline_result"})
        self.assertEqual(
            task_result.data["worldline_result"]["root_entity"]["name"],
            "晴天",
        )
        self.assertTrue(
            {
                "candidate_entities",
                "entity_facts",
                "retrieved_docs",
                "worldline_graph",
                "branches",
            }.isdisjoint(output)
        )

    def test_song_worldline_completes_with_structured_result(self) -> None:
        song = entity(1, EntityType.SONG, "晴天")
        service = FakeMusicService(
            song,
            [
                entity(2, EntityType.ARTIST, "周杰伦"),
                entity(3, EntityType.ALBUM, "叶惠美"),
            ],
        )
        graph = create_worldline_subgraph(
            music_service=service,
            retriever=EmptyRetriever(),
            generator=FakeGenerator(),
        )

        output = asyncio.run(invoke(graph, "歌曲《晴天》的世界线"))

        self.assertEqual(set(output), {"task_result"})
        result = output["task_result"]
        self.assertEqual(result.status, TaskStatus.SUCCESS)
        worldline = result.data["worldline_result"]
        self.assertEqual(worldline["root_entity"]["entity_type"], "song")
        self.assertEqual(len(worldline["branches"]), 3)
        self.assertEqual(len(worldline["graph"]["edges"]), 2)

    def test_artist_worldline_uses_related_real_songs(self) -> None:
        artist = entity(2, EntityType.ARTIST, "周杰伦")
        service = FakeMusicService(
            artist,
            [entity(index, EntityType.SONG, f"歌曲 {index}") for index in range(3, 6)],
        )
        graph = create_worldline_subgraph(
            music_service=service,
            retriever=EmptyRetriever(),
            generator=FakeGenerator(),
        )

        output = asyncio.run(invoke(graph, "歌手周杰伦的世界线"))

        worldline = output["task_result"].data["worldline_result"]
        self.assertEqual(worldline["root_entity"]["entity_type"], "artist")
        self.assertEqual(len(worldline["graph"]["nodes"]), 4)
        self.assertTrue(
            all(edge["relation"] == "performed_by" for edge in worldline["graph"]["edges"])
        )

    def test_entity_search_failure_stops_graph(self) -> None:
        graph = create_worldline_subgraph(
            music_service=FakeMusicService(
                None,
                search_error=RuntimeError("private upstream detail"),
            ),
            retriever=EmptyRetriever(),
            generator=FakeGenerator(),
        )

        output = asyncio.run(invoke(graph, "歌曲《不存在》的世界线"))

        result = output["task_result"]
        self.assertEqual(result.status, TaskStatus.FAILED)
        self.assertEqual(result.data, {"reason": "entity_search_failed"})
        self.assertNotIn("private upstream detail", result.content)

    def test_entity_not_found_requests_more_input(self) -> None:
        graph = create_worldline_subgraph(
            music_service=FakeMusicService(None),
            retriever=EmptyRetriever(),
            generator=FakeGenerator(),
        )

        output = asyncio.run(invoke(graph, "歌曲《不存在》的世界线"))

        self.assertEqual(output["task_result"].status, TaskStatus.NEEDS_INPUT)
        self.assertEqual(output["task_result"].data["reason"], "entity_not_found")

    def test_retriever_failure_degrades_to_fact_only_worldline(self) -> None:
        song = entity(1, EntityType.SONG, "晴天")
        graph = create_worldline_subgraph(
            music_service=FakeMusicService(song),
            retriever=FailingRetriever(),
            generator=FakeGenerator(),
        )

        output = asyncio.run(invoke(graph, "歌曲《晴天》的世界线"))

        result = output["task_result"]
        self.assertEqual(result.status, TaskStatus.SUCCESS)
        self.assertIn("仅基于目录事实", result.data["worldline_result"]["narrative"])

    def test_retrieval_context_and_sources_are_bounded(self) -> None:
        song = entity(1, EntityType.SONG, "晴天")
        graph = create_worldline_subgraph(
            music_service=FakeMusicService(song),
            retriever=ManyDocsRetriever(),
            generator=FakeGenerator(),
        )

        output = asyncio.run(invoke(graph, "歌曲《晴天》的世界线"))

        sources = output["task_result"].data["worldline_result"]["sources"]
        self.assertEqual(len(sources), 11)

    def test_retriever_receives_typed_plan_with_knowledge_entity_id(self) -> None:
        song = entity(185809, EntityType.SONG, "晴天")
        retriever = CapturingRetriever()
        graph = create_worldline_subgraph(
            music_service=FakeMusicService(song),
            retriever=retriever,
            generator=FakeGenerator(),
        )

        output = asyncio.run(invoke(graph, "歌曲《晴天》的世界线"))

        self.assertEqual(output["task_result"].status, TaskStatus.SUCCESS)
        self.assertIsNotNone(retriever.plan)
        self.assertEqual(
            retriever.plan.entities[0].knowledge_entity_id,
            "song:qing_tian",
        )
        self.assertEqual(
            retriever.plan.entities[0].catalog_reference.entity_id,
            "185809",
        )

    def test_branch_failure_returns_safe_failed_result(self) -> None:
        song = entity(1, EntityType.SONG, "晴天")
        graph = create_worldline_subgraph(
            music_service=FakeMusicService(song),
            retriever=EmptyRetriever(),
            generator=FakeGenerator(fail_branches=True),
        )

        output = asyncio.run(invoke(graph, "歌曲《晴天》的世界线"))

        self.assertEqual(output["task_result"].status, TaskStatus.FAILED)
        self.assertEqual(
            output["task_result"].data["reason"],
            "branch_generation_failed",
        )

    def test_subgraph_exposes_expected_node_flow(self) -> None:
        graph = create_worldline_subgraph(
            music_service=FakeMusicService(entity(1, EntityType.SONG, "晴天")),
            retriever=EmptyRetriever(),
            generator=FakeGenerator(),
        ).get_graph()

        self.assertEqual(
            set(graph.nodes),
            {
                "__start__",
                "prepare_query_context",
                "resolve_entity",
                "understand_query",
                "load_entity_facts",
                "retrieve_music_knowledge",
                "build_worldline_graph",
                "generate_narrative",
                "generate_branches",
                "__end__",
            },
        )


if __name__ == "__main__":
    unittest.main()
