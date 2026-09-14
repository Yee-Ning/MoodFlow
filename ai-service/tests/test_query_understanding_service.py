import asyncio
import unittest

from langchain_core.messages import AIMessage, HumanMessage

from app.agent.foundation.routing import RoutingContext
from app.rag.knowledge.entity_linking.models import EntityType
from app.rag.query_understanding import (
    CatalogReference,
    QueryUnderstandingService,
    QueryUnderstandingStatus,
    load_query_understanding_config,
)
from app.rag.query_understanding.analyzer import QueryAnalyzer, QueryTypeOutput
from app.rag.query_understanding.context import QueryContextResolver
from app.rag.query_understanding.entity_resolver import QueryEntityResolver
from app.rag.query_understanding.models import QueryType
from app.schemas.context import SongContext


class FakeSongProvider:
    async def get_song(self, song_id: int) -> SongContext:
        return SongContext(
            songId=song_id,
            name="晴天",
            artist="周杰伦",
            album="叶惠美",
            originalLyrics="",
            translatedLyrics="",
            lyricsStatus="AVAILABLE",
        )


class FailingSongProvider:
    async def get_song(self, song_id: int) -> SongContext:
        raise RuntimeError("unavailable")


class CapturingRuntime:
    def __init__(self) -> None:
        self.calls = 0

    async def analyze(self, query: str) -> QueryTypeOutput:
        self.calls += 1
        return QueryTypeOutput(
            query_types=[QueryType.EXPLORATORY], confidence=0.88
        )


def service(*, provider=None, runtime=None):  # type: ignore[no-untyped-def]
    config = load_query_understanding_config()
    resolver = QueryContextResolver(song_provider=provider or FakeSongProvider())
    return QueryUnderstandingService(
        config=config,
        context_resolver=resolver,
        entity_resolver=QueryEntityResolver(resolver.registry),
        analyzer=QueryAnalyzer(config, runtime=runtime or CapturingRuntime()),
    )


class QueryContextTests(unittest.TestCase):
    def test_current_song_reference_uses_trusted_player_context(self) -> None:
        result = asyncio.run(
            service().understand(
                "这首歌的创作背景是什么",
                routing_context=RoutingContext(
                    has_current_song=True, current_song_id=185809
                ),
            )
        )

        self.assertEqual(result.status, QueryUnderstandingStatus.READY)
        self.assertIn("《晴天》", result.analysis.standalone_query)
        song = next(
            item
            for item in result.analysis.entities
            if item.entity_type is EntityType.SONG
        )
        self.assertEqual(song.knowledge_entity_id, "song:qing_tian")
        self.assertEqual(song.catalog_reference.entity_id, "185809")

    def test_current_song_reference_without_player_state_needs_input(self) -> None:
        result = asyncio.run(service().understand("这首歌的创作背景是什么"))

        self.assertEqual(result.status, QueryUnderstandingStatus.NEEDS_INPUT)
        self.assertIn("选择并播放", result.clarification_question)

    def test_current_song_provider_failure_is_safe(self) -> None:
        result = asyncio.run(
            service(provider=FailingSongProvider()).understand(
                "当前歌曲有什么背景",
                routing_context=RoutingContext(
                    has_current_song=True, current_song_id=185809
                ),
            )
        )

        self.assertEqual(result.status, QueryUnderstandingStatus.NEEDS_INPUT)
        self.assertNotIn("unavailable", result.clarification_question)

    def test_conversation_reference_is_re_resolved_through_registry(self) -> None:
        result = asyncio.run(
            service().understand(
                "他的音乐风格有什么变化",
                messages=[
                    HumanMessage(content="介绍一下周杰伦"),
                    AIMessage(content="周杰伦是一位音乐人。"),
                    HumanMessage(content="他的音乐风格有什么变化"),
                ],
            )
        )

        self.assertEqual(result.status, QueryUnderstandingStatus.READY)
        self.assertIn("歌手《周杰伦》", result.analysis.standalone_query)
        self.assertIn("artist:jay_chou", result.analysis.constraints.entity_ids)


class QueryAnalysisTests(unittest.TestCase):
    def test_timeline_style_query_uses_rules_without_llm(self) -> None:
        runtime = CapturingRuntime()
        result = asyncio.run(
            service(runtime=runtime).understand(
                "周杰伦 2000 到 2005 年的音乐风格如何变化"
            )
        )

        self.assertEqual(result.status, QueryUnderstandingStatus.READY)
        self.assertEqual(runtime.calls, 0)
        self.assertEqual(result.analysis.constraints.start_year, 2000)
        self.assertEqual(result.analysis.constraints.end_year, 2005)
        self.assertIn("artist:jay_chou", result.analysis.constraints.entity_ids)
        self.assertIn(QueryType.TIMELINE, result.analysis.query_types)
        self.assertIn(QueryType.INFLUENCE_STYLE, result.analysis.query_types)

    def test_ambiguous_registry_entity_requests_clarification(self) -> None:
        result = asyncio.run(service().understand("七里香值得了解吗"))

        self.assertEqual(result.status, QueryUnderstandingStatus.NEEDS_INPUT)
        self.assertIn("歌曲、歌手还是专辑", result.clarification_question)

    def test_explicit_entity_type_resolves_same_name(self) -> None:
        result = asyncio.run(service().understand("歌曲《七里香》的创作背景"))

        self.assertEqual(result.status, QueryUnderstandingStatus.READY)
        self.assertIn("song:qi_li_xiang", result.analysis.constraints.entity_ids)

    def test_alias_that_requires_context_does_not_silently_resolve(self) -> None:
        result = asyncio.run(service().understand("霍元甲的创作背景"))

        self.assertEqual(result.status, QueryUnderstandingStatus.NEEDS_INPUT)

    def test_unknown_entity_remains_unfiltered_and_is_flagged(self) -> None:
        result = asyncio.run(service().understand("Taylor Swift 的音乐风格"))

        self.assertEqual(result.status, QueryUnderstandingStatus.READY)
        self.assertEqual(result.analysis.constraints.entity_ids, [])
        self.assertIn("no_resolved_query_entity", result.search_plan.warnings)

    def test_catalog_and_knowledge_id_are_kept_separate(self) -> None:
        result = asyncio.run(
            service().understand(
                "歌曲《晴天》的世界线",
                catalog_references=[
                    CatalogReference(
                        provider="netease",
                        entity_id="185809",
                        entity_type=EntityType.SONG,
                        name="晴天",
                    )
                ],
            )
        )

        self.assertEqual(result.status, QueryUnderstandingStatus.READY)
        entity = result.analysis.entities[0]
        self.assertEqual(entity.knowledge_entity_id, "song:qing_tian")
        self.assertEqual(entity.catalog_reference.entity_id, "185809")

    def test_negative_term_is_not_emitted_as_metadata_filter(self) -> None:
        result = asyncio.run(service().understand("周杰伦的歌曲，不要现场版"))

        self.assertEqual(result.status, QueryUnderstandingStatus.READY)
        self.assertEqual(result.analysis.constraints.negative_terms, ["现场版"])
        self.assertNotIn(
            "现场版", result.search_plan.metadata_filter.model_dump_json()
        )

    def test_unknown_query_uses_at_most_one_llm_call(self) -> None:
        runtime = CapturingRuntime()
        result = asyncio.run(service(runtime=runtime).understand("说点有意思的"))

        self.assertEqual(result.status, QueryUnderstandingStatus.READY)
        self.assertEqual(runtime.calls, 1)


if __name__ == "__main__":
    unittest.main()
