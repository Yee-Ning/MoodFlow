"""Weight/experiment injection changes sorting, never candidate recall or facts."""

import json
import logging

import pytest
from langgraph.runtime import Runtime

from app.agent.foundation.models import TaskStatus
from app.agent.foundation.routing import RouteKey, RouteTask, RoutingContext
from app.agent.foundation.long_horizon.operations import active_operations
from app.agent.handoff import curator
from app.agent.handoff.curator_knowledge import Discovery
from app.agent.handoff.recommendation import RecommendationWeights
from app.agent.handoff.recommendation_experiments import ExperimentConfig, ExperimentVariant
from app.schemas.context import MusicAgentContext
from app.tools.music import MUSIC_AGENT_TOOLS
from tests.test_curator_curation_v1 import Catalog, responses
from tests.test_curator_curation_v2 import SplitCatalog, document, service_for
from tests import test_write_reconciliation as write_helpers

journal = write_helpers.journal


def metadata(caplog):
    return [json.loads(record.getMessage().split(" ", 1)[1]) for record in caplog.records
            if record.name == "app.agent.handoff.observability"
            and record.getMessage().startswith("curation.recommendation ")]


async def run(catalog, journal, *, weights=None, experiment=None, user_id=None, text="雨天夜跑歌单", model=None):
    task = RouteTask(task_id="weight-test-task", route_key=RouteKey.TOOL_CALLING, input_text=text)
    state = {"messages": [], "current_input": text, "routing_context": RoutingContext(),
             "task_results": [], "continuity_context": {}}
    runtime = Runtime(context=MusicAgentContext(session_cookie="isolated-cookie", user_id=user_id))
    token = active_operations.set(journal)
    try:
        return await curator.execute_curation(task, state, runtime, request=curator.curation_request(text),
                                             model=model or responses(catalog), tools=MUSIC_AGENT_TOOLS,
                                             weights=weights, experiment=experiment)
    finally:
        active_operations.reset(token)


def empty_knowledge(monkeypatch):
    async def discover(*args):
        return Discovery()

    class Service:
        pass

    service = Service()
    service.discover = discover
    monkeypatch.setattr(curator, "get_curator_knowledge_service", lambda: service)


def catalog_with_single_scene_hit(monkeypatch):
    catalog = Catalog(monkeypatch)
    catalog.songs = [song.model_copy(update={"name": "雨天夜跑" if index == 0 else f"track{index}", "album": ""})
                     for index, song in enumerate(catalog.songs)]
    return catalog


@pytest.mark.asyncio
async def test_default_runtime_result_order_is_unchanged_and_history_is_explicit(monkeypatch, journal, caplog):
    caplog.set_level(logging.INFO, logger="app.agent.handoff.observability")
    catalog = catalog_with_single_scene_hit(monkeypatch)
    empty_knowledge(monkeypatch)
    result = await run(catalog, journal)
    assert result.status is TaskStatus.SUCCESS
    expected = [catalog.songs[-1].song_id, *[song.song_id for song in catalog.songs[:-1]]]
    assert catalog.members == expected and len(catalog.members) == 15
    events = metadata(caplog)
    assert len(events) == 1 and events[0]["assignment_status"] == "disabled"
    assert events[0]["playback_history_status"] == "not_connected"


@pytest.mark.asyncio
async def test_nondefault_scene_weights_change_order_but_keep_recall_and_tool_contract(monkeypatch, journal, caplog):
    caplog.set_level(logging.INFO, logger="app.agent.handoff.observability")
    catalog = catalog_with_single_scene_hit(monkeypatch)
    empty_knowledge(monkeypatch)
    result = await run(catalog, journal, weights=RecommendationWeights(0, 0, 1))
    assert result.status is TaskStatus.SUCCESS
    assert catalog.members[0] == catalog.songs[0].song_id
    assert set(catalog.members) == {song.song_id for song in catalog.songs}
    assert [call for call in catalog.calls if call[0] == "search"] == [("search", "夜跑"), ("search", "雨夜")]
    assert metadata(caplog)[0]["recommendation_context"]["song_ids"] == catalog.members


@pytest.mark.asyncio
@pytest.mark.parametrize("user_id,expected_assignment", [("PRIVATE_UID_NOT_IN_EVENT", "assigned"), (None, "missing_user_id")])
async def test_injected_experiment_binds_variant_weights_and_records_private_safe_context(monkeypatch, journal, caplog, user_id, expected_assignment):
    caplog.set_level(logging.INFO, logger="app.agent.handoff.observability")
    catalog = catalog_with_single_scene_hit(monkeypatch)
    empty_knowledge(monkeypatch)
    config = ExperimentConfig("curation-test-v1", (
        ExperimentVariant("control", RecommendationWeights(), 0),
        ExperimentVariant("scene", RecommendationWeights(0, 0, 1), 100)))
    result = await run(catalog, journal, experiment=config, user_id=user_id)
    assert result.status is TaskStatus.SUCCESS
    assert catalog.members[0] == (catalog.songs[0].song_id if user_id else catalog.songs[-1].song_id)
    events = metadata(caplog)
    assert len(events) == 1 and events[0]["assignment_status"] == expected_assignment
    assert events[0]["experiment_id"] == "curation-test-v1"
    assert events[0]["variant_id"] == ("scene" if user_id else None)
    assert events[0]["at_utc"] and events[0]["configuration_digest"]
    if user_id:
        assert user_id not in caplog.text


@pytest.mark.asyncio
async def test_custom_weights_cannot_reintroduce_niche_filtered_hot_or_unknown_songs(monkeypatch, journal):
    from unittest.mock import AsyncMock

    catalog = SplitCatalog(monkeypatch, count=15)
    docs = [document(i + 1, predicate="belongs_to_scene", subject=f"song:track_{song.song_id}",
                     value="雨天夜跑", span=f"{song.name} is part of the 雨天夜跑 music scene.")
            for i, song in enumerate(catalog.extra)]
    monkeypatch.setattr(curator, "get_curator_knowledge_service", lambda: service_for(catalog.songs, docs))
    ids = [song.song_id for song in catalog.extra]
    monkeypatch.setattr(curator, "get_song_popularity", AsyncMock(return_value={ids[0]: 500, ids[1]: 50000}))
    result = await run(catalog, journal, weights=RecommendationWeights(0, 100, 0), text="雨天夜跑歌单，冷门一点",
                       model=responses(catalog, ids=[ids[0]]))
    assert result.status is TaskStatus.SUCCESS and catalog.members == [ids[0]]
    assert result.data["curation"]["niche"]["recommendations"][0]["comment_count"] == 500
