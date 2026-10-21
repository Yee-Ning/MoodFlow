"""Online Jay uses the offline vocabulary without invoking model disambiguation."""

import pytest

from app.rag.knowledge.entity_linking.linker import (
    EntityLinker, _JAY_ALBUM_CUES, _JAY_ARTIST_CUES,
)
from app.rag.knowledge.entity_linking.models import EntityRegistryDocument
from app.rag.knowledge.entity_linking.registry import EntityRegistry, normalize_alias
from app.rag.query_understanding.entity_resolver import QueryEntityResolver
from app.rag.query_understanding.models import QueryEntityStatus


def jay_mentions(query, resolver=None):
    return [item for item in (resolver or QueryEntityResolver()).resolve(query)
            if normalize_alias(item.surface) == "jay"]


@pytest.mark.parametrize("cue", _JAY_ALBUM_CUES)
def test_online_jay_album_vocabulary_matches_offline(cue):
    query = f"Jay {cue}。"
    item, = jay_mentions(query)
    assert item.knowledge_entity_id == "album:jay_2000"
    assert item.entity_type is EntityLinker._infer_jay_context_type(query)


@pytest.mark.parametrize("cue", (*_JAY_ARTIST_CUES, "他"))
def test_online_jay_artist_vocabulary_matches_offline(cue):
    query = f"Jay {cue}。"
    item, = jay_mentions(query)
    assert item.knowledge_entity_id == "artist:jay_chou"
    assert item.entity_type is EntityLinker._infer_jay_context_type(query)


@pytest.mark.parametrize("query", [
    "Jay 在现场受到欢迎。", "歌手 Jay 的专辑。", "专辑 Jay 是歌手。",
    "专辑 Jay。" + "。" * 70 + "歌手 JAY。", "Jay Chou 的专辑 JAY。",
    "Jay 其他吉他他人。", "Jay thesis singlehandedly recordsman。",
    "Jay" + "。" * 33 + "歌手。", "Jay" + "。" * 33 + "Jay Chou。",
])
def test_online_jay_missing_or_conflicting_context_stays_ambiguous(query):
    item, = jay_mentions(query)
    assert item.knowledge_entity_id is None
    assert item.status is QueryEntityStatus.AMBIGUOUS
    assert set(item.candidate_knowledge_ids) == {"artist:jay_chou", "album:jay_2000"}


@pytest.mark.parametrize("query", [
    "歌手" + "。" * 30 + "Jay", "Jay" + "。" * 30 + "歌手",
])
def test_online_jay_window_boundary(query):
    item, = jay_mentions(query)
    assert item.knowledge_entity_id == "artist:jay_chou"


@pytest.mark.parametrize("query", [
    "Jay 又称 Jay Chou。", "Jay Chou，简称 JAY。", "Jay\nChou released an album.",
])
def test_nearby_full_name_resolves_artist_without_duplicate_identity(query):
    items = QueryEntityResolver().resolve(query)
    assert len(items) == 1
    assert items[0].knowledge_entity_id == "artist:jay_chou"


def test_full_name_alias_and_non_jay_entities_keep_existing_rules():
    resolver = QueryEntityResolver()
    full_name, = resolver.resolve("Jay Chou released an album.")
    assert full_name.knowledge_entity_id == "artist:jay_chou"
    assert not jay_mentions("bluejay 歌手。", resolver)
    song, = resolver.resolve("歌曲《晴天》的创作背景")
    assert song.knowledge_entity_id == "song:qing_tian"


def test_jay_special_case_requires_the_exact_candidate_pair():
    registry = EntityRegistry(EntityRegistryDocument(registry_version="v1", entities=[
        {"entity_id": "artist:another_jay", "entity_type": "artist", "canonical_name": "Jay"},
    ]))
    item, = QueryEntityResolver(registry).resolve("Jay album")
    assert item.knowledge_entity_id == "artist:another_jay"
