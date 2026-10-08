import asyncio
import unittest
import pytest
from unittest.mock import AsyncMock

from app.agent.foundation.subgraphs.worldline.entity_resolver import (
    parse_entity_query,
    resolve_music_entity,
)


from app.agent.foundation.subgraphs.worldline.models import (
    EntityResolutionStatus,
    EntityType,
    ResolvedEntity,
)
from app.agent.foundation.subgraphs.worldline.music_service import (
    JavaMusicEntityService,
)
from app.schemas.music import MusicEntity, MusicEntityReference


@pytest.mark.parametrize("prefix", ["先探索", "然后探索", "请先探索", "麻烦帮我再探索"])
def test_sequential_request_prefix_is_not_part_of_the_catalog_name(prefix):
    assert parse_entity_query(prefix + "周杰伦的音乐世界线").query == "周杰伦"


@pytest.mark.parametrize("name", ["先知", "然后", "分析师"])
def test_sequential_words_in_quoted_titles_are_preserved(name):
    assert parse_entity_query("探索歌曲《" + name + "》的世界线").query == name


def resolved(
    entity_id: int,
    entity_type: EntityType,
    name: str,
) -> ResolvedEntity:
    return ResolvedEntity(
        entity_id=f"netease:{entity_type.value}:{entity_id}",
        entity_type=entity_type,
        name=name,
        artist="周杰伦" if entity_type is not EntityType.ARTIST else name,
        album="七里香" if entity_type is EntityType.SONG else None,
        source="netease",
    )


class FakeMusicService:
    def __init__(self, candidates: list[ResolvedEntity]) -> None:
        self.candidates = candidates

    async def search_entities(self, query, expected_entity_type, *, limit=5):
        return self.candidates

    async def load_entity_facts(self, entity):
        raise AssertionError("facts should not be loaded during resolution")


class EntityResolverTests(unittest.TestCase):
    def test_extracts_quoted_song_and_expected_type(self) -> None:
        parsed = parse_entity_query("请探索歌曲《七里香》的音乐世界线")

        self.assertEqual(parsed.query, "七里香")
        self.assertEqual(parsed.expected_entity_type, EntityType.SONG)

    def test_resolves_exact_song_from_real_candidates(self) -> None:
        service = FakeMusicService(
            [resolved(1, EntityType.SONG, "晴天"), resolved(2, EntityType.SONG, "晴天 (Live)")]
        )

        result = asyncio.run(resolve_music_entity(service, "歌曲《晴天》的世界线"))

        self.assertEqual(result.status, EntityResolutionStatus.RESOLVED)
        self.assertEqual(result.entity.entity_id, "netease:song:1")

    def test_resolves_exact_artist(self) -> None:
        service = FakeMusicService([resolved(6452, EntityType.ARTIST, "周杰伦")])

        result = asyncio.run(resolve_music_entity(service, "歌手周杰伦的世界线"))

        self.assertEqual(result.status, EntityResolutionStatus.RESOLVED)
        self.assertEqual(result.entity.entity_type, EntityType.ARTIST)

    def test_unquoted_era_question_keeps_artist_identity(self) -> None:
        parsed = parse_entity_query("歌手周杰伦的 2000 年代音乐时期")
        self.assertEqual(parsed.query, "周杰伦")
        self.assertEqual(parsed.expected_entity_type, EntityType.ARTIST)

        parsed_song = parse_entity_query("歌曲晴天的时代")
        self.assertEqual(parsed_song.query, "晴天")
        self.assertEqual(parsed_song.expected_entity_type, EntityType.SONG)

    def test_song_title_containing_era_word_remains_exact_identity(self) -> None:
        service = FakeMusicService([resolved(77, EntityType.SONG, "我们的年代")])
        result = asyncio.run(resolve_music_entity(service, "歌曲我们的年代"))
        self.assertEqual(result.status, EntityResolutionStatus.RESOLVED)
        self.assertEqual(result.entity.entity_id, "netease:song:77")

    def test_unquoted_scene_question_keeps_artist_identity(self) -> None:
        parsed = parse_entity_query("歌手周杰伦的音乐场景")
        self.assertEqual(parsed.query, "周杰伦")
        self.assertEqual(parsed.expected_entity_type, EntityType.ARTIST)

    def test_unquoted_influence_questions_keep_target_identity(self) -> None:
        for query in (
            "歌手周杰伦的音乐影响",
            "歌手周杰伦受谁影响",
            "歌手周杰伦的风格来源",
            "谁影响了歌手周杰伦",
            "Who influenced 歌手周杰伦",
        ):
            parsed = parse_entity_query(query)
            self.assertEqual(parsed.query, "周杰伦")
            self.assertEqual(parsed.expected_entity_type, EntityType.ARTIST)

    def test_song_title_containing_scene_word_remains_exact_identity(self) -> None:
        service = FakeMusicService([resolved(78, EntityType.SONG, "Scene")])
        result = asyncio.run(resolve_music_entity(service, "歌曲 Scene"))
        self.assertEqual(result.status, EntityResolutionStatus.RESOLVED)
        self.assertEqual(result.entity.entity_id, "netease:song:78")

    def test_same_name_song_and_album_is_ambiguous_without_type(self) -> None:
        service = FakeMusicService(
            [
                resolved(1, EntityType.SONG, "七里香"),
                resolved(2, EntityType.ALBUM, "七里香"),
            ]
        )

        result = asyncio.run(resolve_music_entity(service, "七里香"))

        self.assertEqual(result.status, EntityResolutionStatus.AMBIGUOUS)
        self.assertEqual(len(result.candidates), 2)

    def test_no_exact_candidate_is_unresolved(self) -> None:
        service = FakeMusicService([resolved(1, EntityType.SONG, "七里香")])

        result = asyncio.run(resolve_music_entity(service, "不存在的歌"))

        self.assertEqual(result.status, EntityResolutionStatus.UNRESOLVED)

    def test_literal_catalog_identity_precedes_decorated_names(self) -> None:
        service = FakeMusicService([
            resolved(1, EntityType.ARTIST, "Example Artist"),
            resolved(2, EntityType.ARTIST, "Example Artist."),
            resolved(3, EntityType.ARTIST, "Example Artist♚"),
        ])
        result = asyncio.run(resolve_music_entity(service, '歌手《Example Artist》的世界线'))
        self.assertEqual(result.status, EntityResolutionStatus.RESOLVED)
        self.assertEqual(result.entity.entity_id, "netease:artist:1")

    def test_genuinely_identical_names_still_require_disambiguation(self) -> None:
        service = FakeMusicService([
            resolved(1, EntityType.ARTIST, "Example Artist"),
            resolved(2, EntityType.ARTIST, "Example Artist"),
            resolved(3, EntityType.ARTIST, "Example Artist."),
        ])
        result = asyncio.run(resolve_music_entity(service, '歌手《Example Artist》的世界线'))
        self.assertEqual(result.status, EntityResolutionStatus.AMBIGUOUS)
        self.assertEqual(len(result.candidates), 2)

    def test_normalized_fallback_keeps_ambiguous_and_unresolved_boundaries(self) -> None:
        for names, status in [
            (["Example Artist."], EntityResolutionStatus.RESOLVED),
            (["Example Artist.", "Example Artist♚"], EntityResolutionStatus.AMBIGUOUS),
            (["Other Artist"], EntityResolutionStatus.UNRESOLVED),
        ]:
            service = FakeMusicService([resolved(i + 1, EntityType.ARTIST, name) for i, name in enumerate(names)])
            result = asyncio.run(resolve_music_entity(service, '歌手《Example Artist》的世界线'))
            self.assertEqual(result.status, status)

    def test_alternate_catalog_title_keeps_its_literal_identity(self) -> None:
        service = FakeMusicService([
            resolved(1, EntityType.SONG, "Example 2000年代"),
            resolved(2, EntityType.SONG, "Example 2000年代."),
        ])
        result = asyncio.run(resolve_music_entity(service, "歌曲Example 2000年代"))
        self.assertEqual(result.status, EntityResolutionStatus.RESOLVED)
        self.assertEqual(result.entity.entity_id, "netease:song:1")


class JavaMusicEntityServiceTests(unittest.TestCase):
    def test_load_facts_preserves_grounded_relations_and_source(self) -> None:
        client = AsyncMock()
        client.get_entity_detail.return_value = MusicEntity(
            id=185809,
            entityType="song",
            name="晴天",
            artist="周杰伦",
            album="叶惠美",
            coverUrl="https://img.test/qingtian.jpg",
            metadata={"duration": 269000},
            relatedEntities=[
                MusicEntityReference(
                    id=6452,
                    entityType="artist",
                    name="周杰伦",
                    artist="周杰伦",
                ),
                MusicEntityReference(
                    id=18905,
                    entityType="album",
                    name="叶惠美",
                    artist="周杰伦",
                ),
            ],
        )
        service = JavaMusicEntityService(client)

        facts = asyncio.run(
            service.load_entity_facts(resolved(185809, EntityType.SONG, "晴天"))
        )

        self.assertEqual(len(facts.related_entities), 2)
        self.assertEqual(
            [fact.predicate for fact in facts.facts[:2]],
            ["performed_by", "belongs_to_album"],
        )
        self.assertEqual(facts.sources[0].source, "MoodFlow Java Music Service")


if __name__ == "__main__":
    unittest.main()
