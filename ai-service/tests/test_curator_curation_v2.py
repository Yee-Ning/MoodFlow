"""Offline catalog + real WorldLine validators; no invented service decisions."""

import re
from types import SimpleNamespace

import pytest

from app.agent.handoff import curator
from app.agent.handoff.curator import CurationRequest, curation_request
from app.agent.handoff.curator_knowledge import (
    CuratorKnowledgeService,
    MAX_HINTS,
)
from app.agent.foundation.models import TaskStatus
from app.agent.foundation.subgraphs.worldline.models import (
    EntityType as CatalogType,
    ResolvedEntity,
)
from app.agent.foundation.subgraphs.worldline.retrieval import (
    EmptyMusicKnowledgeRetriever,
    HybridMusicKnowledgeRetriever,
    ShadowMusicKnowledgeRetriever,
    WorldLineRetrievalRuntime,
)
from app.rag.knowledge.entity_linking.models import (
    EntityRegistryDocument,
    EntityType,
    RegistryEntity,
)
from app.rag.knowledge.entity_linking.registry import EntityRegistry
from app.schemas.music import MusicSearchResult
from tests.test_curator_curation_v1 import Catalog, curate, responses
from tests import test_write_reconciliation as write_helpers
from tests.test_worldline_era_expansion import document
from tests.test_worldline_semantic_expansion import knowledge_document

journal = write_helpers.journal


def registry_for(songs, *, extra=()):
    return EntityRegistry(
        EntityRegistryDocument(
            registry_version="v99",
            entities=[
                RegistryEntity(
                    entity_id=f"song:track_{song.song_id}",
                    entity_type=EntityType.SONG,
                    canonical_name=song.name,
                )
                for song in songs
            ]
            + list(extra),
        )
    )


def era_docs(songs):
    return [
        document(
            i + 1,
            predicate="released",
            subject=f"song:track_{song.song_id}",
            span=f"{song.name} released in {1990 + i % 10}.",
            value=str(1990 + i % 10),
            time={"year": 1990 + i % 10, "raw": str(1990 + i % 10)},
        )
        for i, song in enumerate(songs)
    ]


def service_for(songs, docs, *, registry=None, music=None):
    async def retrieve(plan):
        return SimpleNamespace(plan=plan)

    adapter = SimpleNamespace(adapt=lambda pool, plan: docs)
    retriever = HybridMusicKnowledgeRetriever(
        SimpleNamespace(retrieve=retrieve), context_adapter=adapter
    )
    return CuratorKnowledgeService(
        runtime=WorldLineRetrievalRuntime(retriever),
        registry=registry or registry_for(songs),
        music=music,
    )


def install_era(monkeypatch, catalog):
    service = service_for(catalog.songs, era_docs(catalog.songs))
    monkeypatch.setattr(curator, "get_curator_knowledge_service", lambda: service)
    return service


class SplitCatalog(Catalog):
    def __init__(self, monkeypatch, **kwargs):
        super().__init__(monkeypatch, **kwargs)
        self.primary = self.songs[:10]
        self.extra = self.songs[10:]

    async def search_music(self, keyword, limit=10):
        self.calls.append(("search", keyword))
        exact = [
            song
            for song in self.songs
            if song.name == keyword or keyword in song.artists
        ]
        return MusicSearchResult(
            keyword=keyword, limit=limit, songs=exact or self.primary
        )


@pytest.mark.asyncio
@pytest.mark.parametrize("count", [10, 15, 20])
async def test_90s_catalog_without_years_reaches_target_with_each_song_evidenced(
    monkeypatch, journal, count
):
    catalog = SplitCatalog(monkeypatch, scene="90年代港乐", count=count)
    catalog.songs = [
        song.model_copy(update={"album": "粤语专辑"}) for song in catalog.songs
    ]
    catalog.primary, catalog.extra = catalog.songs[:10], catalog.songs[10:]
    install_era(monkeypatch, catalog)
    result = await curate(
        f"生成90年代港乐歌单，{count}首",
        catalog,
        journal,
        model=responses(catalog, ids=[song.song_id for song in catalog.songs]),
    )
    assert result.status is TaskStatus.SUCCESS, result.content
    data = result.data["curation"]
    assert len(catalog.members) == count
    assert data["accepted_song_ids"] == catalog.members
    for song in data["selected_songs"]:
        assert song in [row.model_dump() for row in catalog.songs]
        evidence = data["knowledge_sources"][str(song["song_id"])]
        assert any(
            item["kind"] == "era"
            and 1990 <= item["start_year"] <= item["end_year"] <= 1999
            for item in evidence
        )
        for item in evidence:
            assert item["knowledge_id"] == f"song:track_{song['song_id']}"
            for source in item["sources"]:
                assert (
                    source["metadata"]["claim_id"] and source["metadata"]["parent_id"]
                )
                assert song["name"] in source["excerpt"]
    assert set(data["discovered_song_ids"]) == {song.song_id for song in catalog.extra}
    assert "经验证的歌曲年代证据" in result.content
    assert "尚未全部确认" in result.content
    assert "已确认：" not in result.content and "已执行：" not in result.content


@pytest.mark.asyncio
async def test_scene_discovery_changes_playlist_and_intro_only_mentions_actual_tracks(
    monkeypatch, journal
):
    catalog = SplitCatalog(monkeypatch, count=15)
    docs = [
        document(
            i + 1,
            predicate="belongs_to_scene",
            subject=f"song:track_{song.song_id}",
            value="雨天夜跑",
            span=f"{song.name} is part of the 雨天夜跑 music scene.",
        )
        for i, song in enumerate(catalog.extra)
    ]
    service = service_for(catalog.songs, docs)
    monkeypatch.setattr(curator, "get_curator_knowledge_service", lambda: service)
    result = await curate(
        "雨天夜跑歌单，冷门一点",
        catalog,
        journal,
        model=responses(catalog, ids=[song.song_id for song in catalog.songs]),
    )
    assert result.status is TaskStatus.SUCCESS, result.content
    data = result.data["curation"]
    pure_search = {song.song_id for song in catalog.primary}
    assert set(catalog.members) - pure_search == {
        song.song_id for song in catalog.extra
    }
    assert data["selected_discovery_ids"]
    assert {trace["service"] for trace in data["knowledge_traces"]} >= {
        "scene",
        "era",
        "influence",
    }
    for song_id in data["selected_discovery_ids"]:
        assert data["catalog_sources"][str(song_id)]
        source = data["knowledge_sources"][str(song_id)][0]["sources"][0]
        assert source["source_type"] == "knowledge" and source["metadata"]["claim_id"]
    names = {song["name"] for song in data["selected_songs"]}
    assert set(re.findall(r"《(.*?)》", data["introduction"])) <= names
    assert set(data["introduction_song_ids"]) <= set(catalog.members)
    assert "未核实平台热度" in data["introduction"]
    # Liked precedence remains, with the two discovery paths then interleaved.
    assert catalog.members[0] == catalog.songs[-1].song_id
    assert catalog.members[1] not in pure_search and catalog.members[2] in pure_search


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "failure",
    [
        "empty",
        "unavailable",
        "insufficient",
        "low_confidence",
        "missing_span",
        "artist_only",
        "out_of_era",
        "conflict",
        "unknown_identity",
    ],
)
async def test_no_era_evidence_never_pads_or_writes(monkeypatch, journal, failure):
    catalog = Catalog(
        monkeypatch, scene="90年代港乐"
    )  # even title years grant no date authority
    docs = era_docs(catalog.songs)
    registry = registry_for(catalog.songs)
    if failure == "empty":
        docs = []
    elif failure in {"insufficient", "low_confidence", "missing_span"}:
        for doc in docs:
            if failure == "insufficient":
                doc.metadata["sufficiency_status"] = "insufficient"
            elif failure == "low_confidence":
                doc.metadata["supported_claims"][0]["verification_confidence"] = 0.2
            else:
                doc.metadata["supported_claims"][0]["evidence_span"] = "不存在的原文"
    elif failure == "artist_only":
        registry = EntityRegistry(
            EntityRegistryDocument(
                registry_version="v99",
                entities=[
                    RegistryEntity(
                        entity_id="artist:test",
                        entity_type=EntityType.ARTIST,
                        canonical_name="目录歌手",
                    )
                ],
            )
        )
        docs = [
            document(
                1,
                predicate="belongs_to_era",
                subject="artist:test",
                value="1990s",
                time={"year": 1993},
                span="目录歌手在1993年属于1990s音乐时期。",
            )
        ]
    elif failure == "out_of_era":
        docs = [
            document(
                i + 1,
                predicate="released",
                subject=f"song:track_{song.song_id}",
                value="2002",
                time={"year": 2002},
                span=f"{song.name} released in 2002.",
            )
            for i, song in enumerate(catalog.songs)
        ]
    elif failure == "conflict":
        docs += [
            document(
                i + 100,
                predicate="released",
                subject=f"song:track_{song.song_id}",
                value="2002",
                time={"year": 2002},
                span=f"{song.name} released in 2002.",
            )
            for i, song in enumerate(catalog.songs)
        ]
    elif failure == "unknown_identity":
        registry = registry_for([])
    service = service_for(catalog.songs, docs, registry=registry)
    if failure == "unavailable":

        async def fail(*args):
            raise OSError("offline")

        service.discover = fail
    monkeypatch.setattr(curator, "get_curator_knowledge_service", lambda: service)
    result = await curate("90年代港乐", catalog, journal)
    assert result.status is TaskStatus.SUCCESS, result.content
    assert "只找到 0 首" in result.content
    assert catalog.members == [] and journal.write_events() == []


@pytest.mark.asyncio
async def test_partial_era_coverage_creates_only_supported_tracks(monkeypatch, journal):
    catalog = SplitCatalog(monkeypatch, scene="90年代港乐", count=15)
    monkeypatch.setattr(
        curator,
        "get_curator_knowledge_service",
        lambda: service_for(catalog.songs, era_docs(catalog.songs[:3])),
    )
    ids = [song.song_id for song in catalog.songs[:3]]
    result = await curate(
        "90年代港乐", catalog, journal, model=responses(catalog, ids=ids)
    )
    assert set(catalog.members) == set(ids)
    assert "只找到 3 首符合的" in result.content


@pytest.mark.asyncio
async def test_same_title_recordings_are_not_assigned_era_evidence(
    monkeypatch, journal
):
    catalog = Catalog(monkeypatch, scene="90年代港乐", count=2)
    catalog.songs[1] = catalog.songs[1].model_copy(
        update={"name": catalog.songs[0].name, "artists": ["翻唱者"]}
    )
    monkeypatch.setattr(
        curator,
        "get_curator_knowledge_service",
        lambda: service_for(catalog.songs[:1], era_docs(catalog.songs[:1])),
    )
    result = await curate("90年代港乐", catalog, journal)
    assert "只找到 0 首" in result.content and not catalog.members


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["create_unknown", "write_unknown", "write_rejected"])
async def test_discovery_preserves_a_journal_failure_discipline(
    monkeypatch, journal, mode
):
    catalog = Catalog(monkeypatch, scene="90年代港乐", mode=mode, fail_at=1)
    install_era(monkeypatch, catalog)
    result = await curate("90年代港乐", catalog, journal)
    assert result.status is TaskStatus.FAILED
    assert "已确认：" not in result.content and "已执行：" not in result.content
    assert sum(call[0] == "create" for call in catalog.calls) == 1
    assert len([call for call in catalog.calls if call[0] == "add"]) <= 2
    assert all(
        fact["source"] == "operation_journal" for fact in result.data["result_facts"]
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "retriever_type", [EmptyMusicKnowledgeRetriever, ShadowMusicKnowledgeRetriever]
)
async def test_empty_or_shadow_never_exposes_candidates(retriever_type):
    async def fail(*args):
        raise AssertionError("disabled provider should not be used")

    retriever = (
        retriever_type()
        if retriever_type is EmptyMusicKnowledgeRetriever
        else retriever_type(SimpleNamespace(retrieve=fail))
    )
    result = await CuratorKnowledgeService(
        runtime=WorldLineRetrievalRuntime(retriever)
    ).discover(CurationRequest("雨天夜跑", "测试"), [], [])
    assert result.hints == []


@pytest.mark.asyncio
async def test_influence_and_semantic_can_discover_catalog_verified_peer_artists():
    catalog = [SimpleNamespace(artists=["测试歌手"])]
    entities = [
        RegistryEntity(
            entity_id=f"artist:{key}",
            entity_type=EntityType.ARTIST,
            canonical_name=name,
        )
        for key, name in [("test_artist", "测试歌手"), ("peer", "新歌手")]
    ]
    entities.append(
        RegistryEntity(
            entity_id="genre:hip_hop",
            entity_type=EntityType.GENRE,
            canonical_name="Hip-Hop",
        )
    )
    registry = registry_for([], extra=entities)
    docs = [
        knowledge_document(),
        knowledge_document(
            2, subject_id="artist:peer", evidence_span="新歌手的音乐风格属于 Hip-Hop。"
        ),
        document(
            3,
            predicate="influenced_by",
            subject="artist:peer",
            target="artist:test_artist",
            span="新歌手的音乐风格受到测试歌手的影响。",
        ),
    ]
    artist_calls = []

    async def artists(query, expected_entity_type, limit):
        artist_calls.append(query)
        return [
            ResolvedEntity(
                entity_id=f"netease:artist:{42 if query == '测试歌手' else 43}",
                entity_type=CatalogType.ARTIST,
                name=query,
                source="netease",
            )
        ]

    service = service_for(
        [], docs, registry=registry, music=SimpleNamespace(search_entities=artists)
    )
    result = await service.discover(
        CurationRequest("Hip-Hop夜跑", "测试"), ["Hip-Hop"], catalog
    )
    assert {
        hint.kind for hint in result.hints if hint.knowledge_id == "artist:peer"
    } == {"influence", "semantic"}
    assert len(artist_calls) == 2
    assert all(hint.sources for hint in result.hints)
    assert any(
        t["service"] == "semantic" and t["accepted_count"] == 1 for t in result.traces
    )


@pytest.mark.asyncio
async def test_semantic_rejects_unbound_or_unverified_peers():
    registry = registry_for(
        [],
        extra=[
            RegistryEntity(
                entity_id="artist:test_artist",
                entity_type=EntityType.ARTIST,
                canonical_name="测试歌手",
            )
        ],
    )

    async def no_artist(*args, **kwargs):
        return []

    service = service_for(
        [],
        [knowledge_document()],
        registry=registry,
        music=SimpleNamespace(search_entities=no_artist),
    )
    result = await service.discover(
        CurationRequest("Hip-Hop夜跑", "测试"),
        ["Hip-Hop"],
        [SimpleNamespace(artists=["测试歌手"])],
    )
    assert not result.hints


@pytest.mark.asyncio
async def test_semantic_peer_hints_become_only_real_search_song_ids(
    monkeypatch, journal
):
    catalog = SplitCatalog(monkeypatch, count=15)
    catalog.songs = [
        song.model_copy(update={"artists": ["测试歌手" if i < 10 else "新歌手"]})
        for i, song in enumerate(catalog.songs)
    ]
    catalog.primary, catalog.extra = catalog.songs[:10], catalog.songs[10:]
    registry = registry_for(
        [],
        extra=[
            RegistryEntity(
                entity_id="artist:test_artist",
                entity_type=EntityType.ARTIST,
                canonical_name="测试歌手",
            ),
            RegistryEntity(
                entity_id="artist:peer",
                entity_type=EntityType.ARTIST,
                canonical_name="新歌手",
            ),
            RegistryEntity(
                entity_id="genre:hip_hop",
                entity_type=EntityType.GENRE,
                canonical_name="Hip-Hop",
            ),
        ],
    )
    docs = [
        knowledge_document(),
        knowledge_document(
            2, subject_id="artist:peer", evidence_span="新歌手的音乐风格属于 Hip-Hop。"
        ),
    ]

    async def artists(query, expected_entity_type, limit):
        return [
            ResolvedEntity(
                entity_id=f"netease:artist:{42 if query == '测试歌手' else 43}",
                entity_type=CatalogType.ARTIST,
                name=query,
                source="netease",
            )
        ]

    service = service_for(
        [], docs, registry=registry, music=SimpleNamespace(search_entities=artists)
    )
    monkeypatch.setattr(curator, "get_curator_knowledge_service", lambda: service)
    result = await curate(
        "生成Hip-Hop夜跑歌单，小众",
        catalog,
        journal,
        model=responses(catalog, ids=[song.song_id for song in catalog.songs]),
    )
    assert result.status is TaskStatus.SUCCESS, result.content
    assert result.data["curation"]["selected_discovery_ids"] == [
        song.song_id for song in catalog.extra[-1:] + catalog.extra[:-1]
    ]
    for song in catalog.extra:
        evidence = result.data["curation"]["knowledge_sources"][str(song.song_id)]
        assert (
            evidence[0]["kind"] == "semantic"
            and evidence[0]["knowledge_id"] == "artist:peer"
        )
        assert len(evidence[0]["sources"]) == 2
        assert song.song_id in catalog.members


@pytest.mark.asyncio
@pytest.mark.parametrize("unavailable", [True, False])
async def test_knowledge_failure_keeps_v1_scene_and_honest_warnings(
    monkeypatch, journal, unavailable
):
    catalog = Catalog(monkeypatch)
    service = service_for(catalog.songs, [])
    if unavailable:

        async def fail(*args):
            raise OSError("offline")

        service.discover = fail
    monkeypatch.setattr(curator, "get_curator_knowledge_service", lambda: service)
    result = await curate("雨天夜跑歌单，冷门一点", catalog, journal)
    assert result.status is TaskStatus.SUCCESS, result.content
    assert len(catalog.members) == 15
    assert result.data["curation"]["knowledge_sources"] == {}
    assert "未找到初轮搜索之外" in result.content
    assert "已确认：" not in result.content


@pytest.mark.parametrize(
    "text",
    [
        "雨天夜跑歌单，冷门一点",
        "生成90年代港乐歌单，小众一些",
        "深夜加班提神歌单，非热门",
    ],
)
def test_obscure_modifier_keeps_creation_authority(text):
    assert curation_request(text).obscure


@pytest.mark.asyncio
async def test_depth_queries_are_bounded_and_unknown_knowledge_ids_cannot_create_song_ids(
    monkeypatch, journal
):
    catalog = Catalog(monkeypatch, scene="90年代港乐", count=40)
    install_era(monkeypatch, catalog)
    result = await curate(
        "生成90年代港乐歌单，20首",
        catalog,
        journal,
        model=responses(catalog, ids=[song.song_id for song in catalog.songs[:20]]),
    )
    assert result.status is TaskStatus.SUCCESS, result.content
    assert len([call for call in catalog.calls if call[0] == "search"]) <= 3 + MAX_HINTS
    assert result.data["tool_calls"] <= curator.MAX_CURATION_CALLS
    assert len(catalog.members) == 20
