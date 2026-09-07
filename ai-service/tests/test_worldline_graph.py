import asyncio
import unittest

from langchain_core.messages import HumanMessage

from app.agent.foundation import (
    RouteKey,
    build_main_graph,
    create_main_state,
)
from app.agent.foundation.models import TaskStatus
from app.agent.foundation.subgraphs.worldline.graph import (
    create_worldline_subgraph,
)
from app.agent.foundation.subgraphs.worldline.models import (
    EntityType,
    EvidenceSource,
    EvidenceType,
    MusicEntityFacts,
    MusicFact,
    ResolvedEntity,
    WorldLineBranch,
    WorldLineBranchTarget,
    WorldLineBranchType,
)


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


class FakeGenerator:
    def __init__(self, *, fail_branches: bool = False) -> None:
        self.fail_branches = fail_branches

    async def generate_narrative(self, entity, facts, documents, graph, sources):
        scope = "仅基于目录事实" if not documents else "基于目录与知识资料"
        return f"{scope}，{entity.name}连接了图中的真实音乐实体。"

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


def initial_state(query: str) -> dict:
    return {
        "messages": [HumanMessage(content=query)],
        "current_input": query,
        "task_id": "worldline-test",
        "user_query": query,
        "entity_query": None,
        "expected_entity_type": None,
        "candidate_entities": [],
        "entity_resolution": None,
        "resolved_entity": None,
        "entity_facts": None,
        "retrieved_docs": [],
        "worldline_graph": None,
        "narrative": None,
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
                "resolve_entity",
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
