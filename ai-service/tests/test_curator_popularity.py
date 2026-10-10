"""Catalog + verified graph edges + bounded popularity precede all write calls."""

from unittest.mock import AsyncMock

import pytest

from app.agent.handoff import curator
from app.agent.foundation.models import TaskStatus
from tests.test_curator_curation_v1 import curate, responses
from tests.test_curator_curation_v2 import SplitCatalog, document, service_for
from tests import test_write_reconciliation as write_helpers

journal = write_helpers.journal


def install(monkeypatch, catalog):
    docs = [document(i + 1, predicate="belongs_to_scene", subject=f"song:track_{song.song_id}",
                     value="雨天夜跑", span=f"{song.name} is part of the 雨天夜跑 music scene.")
            for i, song in enumerate(catalog.extra)]
    monkeypatch.setattr(curator, "get_curator_knowledge_service",
                        lambda: service_for(catalog.songs, docs))


@pytest.mark.asyncio
async def test_heat_filter_excludes_hot_noise_unknown_and_returns_ranked_graph_evidence(monkeypatch, journal):
    catalog = SplitCatalog(monkeypatch, count=15)
    install(monkeypatch, catalog)
    ids = [song.song_id for song in catalog.extra]
    read = AsyncMock(return_value={ids[0]: 10000, ids[1]: 100, ids[2]: 99, ids[4]: 10001})
    monkeypatch.setattr(curator, "get_song_popularity", read)
    result = await curate("雨天夜跑歌单，冷门一点", catalog, journal,
                          model=responses(catalog, ids=[ids[0], ids[1]]))
    assert result.status is TaskStatus.SUCCESS, result.content
    assert catalog.members == [ids[1], ids[0]]
    assert set(read.call_args.args[0]) == set(ids)
    data = result.data["curation"]["niche"]
    assert data["unknown_song_ids"] == [ids[3]]
    assert [row["comment_count"] for row in data["recommendations"]] == [100, 10000]
    for row in data["recommendations"]:
        edge = row["association_path"][0]
        assert edge["source_id"] == f"song:track_{row['song_id']}"
        assert edge["relation"] == "belongs_to_scene" and edge["target_name"] == "雨天夜跑"
        assert edge["claim_ids"] and "知识关联" in row["explanation"]
    assert "评论数不等于播放量" in result.content
    assert not {ids[2], ids[3], ids[4]} & set(catalog.members)


@pytest.mark.asyncio
@pytest.mark.parametrize("count", [10001, 99, None])
async def test_no_qualified_niche_returns_empty_without_seed_fallback_or_writes(monkeypatch, journal, count):
    catalog = SplitCatalog(monkeypatch, count=15)
    install(monkeypatch, catalog)
    monkeypatch.setattr(curator, "get_song_popularity", AsyncMock(
        return_value={} if count is None else {song.song_id: count for song in catalog.extra}))
    model = responses(catalog)
    result = await curate("雨天夜跑歌单，冷门一点", catalog, journal, model=model)
    assert result.status is TaskStatus.SUCCESS and result.data["reason"] == "no_niche_candidates"
    assert result.data["curation"]["niche"]["recommendations"] == []
    assert not catalog.members and not catalog.exists and journal.write_events() == []
    assert "不放宽阈值" in result.content


@pytest.mark.asyncio
async def test_model_cannot_reintroduce_filtered_catalog_id_before_write(monkeypatch, journal):
    catalog = SplitCatalog(monkeypatch, count=15)
    install(monkeypatch, catalog)
    ids = [song.song_id for song in catalog.extra]
    monkeypatch.setattr(curator, "get_song_popularity", AsyncMock(return_value={ids[0]: 500, ids[1]: 50000}))
    result = await curate("雨天夜跑歌单，冷门一点", catalog, journal,
                          model=responses(catalog, ids=[ids[1]]))
    assert result.status is TaskStatus.FAILED
    assert not catalog.exists and journal.write_events() == []
    assert result.data["curation"]["niche"]["recommendations"] == []


@pytest.mark.asyncio
async def test_ordinary_curation_does_not_read_popularity_or_change_candidate_behavior(monkeypatch, journal):
    catalog = SplitCatalog(monkeypatch, count=15)
    install(monkeypatch, catalog)
    read = AsyncMock(side_effect=AssertionError("ordinary curation must not fetch comment totals"))
    monkeypatch.setattr(curator, "get_song_popularity", read)
    result = await curate("雨天夜跑歌单", catalog, journal,
                          model=responses(catalog, ids=[song.song_id for song in catalog.songs]))
    assert result.status is TaskStatus.SUCCESS and len(catalog.members) == 15
    assert "niche" not in result.data["curation"]
    read.assert_not_awaited()
