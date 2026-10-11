"""Emission-time observation must not affect ranking, writes, or outcome."""

import json
import logging
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.agent.foundation.models import TaskStatus
from app.agent.handoff.recommendation_experiments import snapshot_recommendation_likes
from app.tools.music import MusicServiceClient, MusicServiceError
from tests.test_curator_curation_v1 import Catalog
from tests.test_curator_weights_ab import run, empty_knowledge, metadata
from tests import test_write_reconciliation as write_helpers

journal = write_helpers.journal


@pytest.mark.asyncio
async def test_snapshot_only_logs_recommended_membership_from_one_batch_read():
    read = AsyncMock(return_value=[2, 99999])
    value = await snapshot_recommendation_likes([1, 2, 1], SimpleNamespace(get_liked_song_ids=read))
    read.assert_awaited_once_with()
    assert value["status"] == "available"
    assert value["songs"] == [{"song_id": 1, "was_liked_before": False}, {"song_id": 2, "was_liked_before": True}]
    assert "99999" not in json.dumps(value)
    assert value["read_started_at_utc"] <= value["observed_at_utc"]


@pytest.mark.asyncio
@pytest.mark.parametrize("value", [[True], [0], ["1"]])
async def test_invalid_snapshot_data_is_unknown_not_false(value):
    result = await snapshot_recommendation_likes([1], SimpleNamespace(get_liked_song_ids=AsyncMock(return_value=value)))
    assert result["status"] == "unavailable" and result["songs"][0]["was_liked_before"] is None


@pytest.mark.asyncio
async def test_read_error_has_no_secret_text_and_empty_selection_needs_no_request():
    read = AsyncMock(side_effect=RuntimeError("PRIVATE_ERROR_DO_NOT_LOG"))
    result = await snapshot_recommendation_likes([1], SimpleNamespace(get_liked_song_ids=read))
    assert result["status"] == "unavailable" and "PRIVATE_ERROR" not in json.dumps(result)
    read.reset_mock()
    result = await snapshot_recommendation_likes([], SimpleNamespace(get_liked_song_ids=read))
    assert result["status"] == "empty" and result["songs"] == []
    read.assert_not_called()


@pytest.mark.asyncio
async def test_fresh_emission_snapshot_does_not_replace_ranking_preference(monkeypatch, journal, caplog):
    caplog.set_level(logging.INFO, logger="app.agent.handoff.observability")
    catalog = Catalog(monkeypatch)
    empty_knowledge(monkeypatch)
    likes = AsyncMock(side_effect=[[catalog.songs[-1].song_id], [catalog.songs[0].song_id]])
    async def read(client):
        return await likes()
    monkeypatch.setattr(MusicServiceClient, "get_liked_song_ids", read)
    result = await run(catalog, journal)
    assert result.status is TaskStatus.SUCCESS and catalog.members[0] == catalog.songs[-1].song_id
    snapshot = metadata(caplog)[0]["recommendation_context"]["like_snapshot"]
    before = {row["song_id"]: row["was_liked_before"] for row in snapshot["songs"]}
    assert before[catalog.songs[0].song_id] is True and before[catalog.songs[-1].song_id] is False
    assert likes.await_count == 2  # One preference read plus exactly one instrumentation read.
    assert len([call for call in catalog.calls if call[0] == "create"]) == 1
    assert len([call for call in catalog.calls if call[0] == "add"]) == 15


@pytest.mark.asyncio
async def test_instrumentation_failure_preserves_success_and_selected_ids(monkeypatch, journal, caplog):
    caplog.set_level(logging.INFO, logger="app.agent.handoff.observability")
    catalog = Catalog(monkeypatch)
    empty_knowledge(monkeypatch)
    likes = AsyncMock(side_effect=[[catalog.songs[-1].song_id], MusicServiceError("timeout", "unavailable")])
    async def read(client):
        return await likes()
    monkeypatch.setattr(MusicServiceClient, "get_liked_song_ids", read)
    result = await run(catalog, journal)
    assert result.status is TaskStatus.SUCCESS and len(catalog.members) == 15
    context = metadata(caplog)[0]["recommendation_context"]
    assert context["song_ids"] == catalog.members and context["like_snapshot"]["status"] == "unavailable"
    assert all(row["was_liked_before"] is None for row in context["like_snapshot"]["songs"])
