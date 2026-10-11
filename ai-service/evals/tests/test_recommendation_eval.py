"""No label leakage, reproducible holdout and receipt-based conversion contracts."""

from datetime import UTC, datetime, timedelta
import json
from unittest.mock import AsyncMock

from langchain_core.messages import ToolMessage
import pytest

from app.agent.foundation.long_horizon.history_writer import digest
from app.agent.handoff.recommendation import RecommendationWeights, rank_recommendations
from app.schemas.music import MusicTrack
from evals.recommendation_eval_runner import (
    aggregate_offline, load_recommendation_events, offline_replay, replay_existing_ranking, run_online_job, write_report,
    offline_replay_async, replay_curator_read_phase,
)
from evals.recommendation_metrics import (
    EvaluationConfig, LikeReceipt, PlaybackCompletionEvent, exposure_from_event,
    online_conversion, random_holdout, read_journal_like_receipts,
)
from tests import test_write_reconciliation as write_helpers

journal = write_helpers.journal
request = write_helpers.request

NOW = datetime(2026, 10, 1, tzinfo=UTC)


@pytest.mark.parametrize("ratio", [0, 0.2, 1])
def test_holdout_is_disjoint_complete_seeded_and_order_independent(ratio):
    config = EvaluationConfig(holdout_ratio=ratio)
    values = list(range(1, 31))
    training, validation = random_holdout(values, config)
    assert not training & validation and training | validation == set(values)
    assert (training, validation) == random_holdout(list(reversed(values)) + [1], config)
    assert len(validation) == int(30 * ratio)


@pytest.mark.parametrize("changes", [{"holdout_ratio": -1}, {"holdout_ratio": 1.1}, {"seed": True},
                                     {"conversion_window_days": 0}, {"completion_fraction": float("nan")}])
def test_invalid_metric_configuration_rejected(changes):
    with pytest.raises(ValueError):
        EvaluationConfig(**changes)


def test_holdout_hidden_from_preference_callback_and_duplicates_not_hits():
    config = EvaluationConfig()
    training, validation = random_holdout(range(1, 11), config)
    def recommend(actual):
        assert actual == training and not actual & validation
        return [*validation, *validation, 999]
    row = offline_replay(range(1, 11), recommend, config)
    assert row["value"] == 1 and row["sample_size"] == 2


def test_empty_validation_is_na_and_empty_input_is_passed_honestly():
    unused = AsyncMock()
    row = offline_replay([], unused, EvaluationConfig())
    assert row["value"] is None and row["status"] == "empty_validation"
    unused.assert_not_called()
    observed = []
    row = offline_replay([1, 2], lambda train: observed.append(train) or [1], EvaluationConfig(holdout_ratio=1))
    assert observed == [frozenset()] and row["value"] == 0.5


def test_replay_calls_existing_ranker_without_adding_heldout_candidates():
    tracks = [MusicTrack(song_id=i, name=f"track{i}", artists=["artist"], album="rain") for i in range(1, 11)]
    response = {"keyword": "rain", "limit": 50, "songs": [song.model_dump() for song in tracks]}
    training, _ = random_holdout(range(1, 11), EvaluationConfig())
    weights = RecommendationWeights(1, 4, 1)
    actual = replay_existing_ranking(training, catalog_response=response, knowledge_ids=[3, 8],
                                    tags=["rain"], weights=weights, k=3)
    expected = rank_recommendations(tracks, training, {3, 8}, ["rain"], weights=weights)[:3]
    assert actual == [song.song_id for song in expected]
    assert not set(actual) - {song.song_id for song in tracks}


def event(*, identity="rec1", songs=None, status="success", snapshot="available", variant="control", at=NOW):
    songs = songs if songs is not None else [{"song_id": 1, "was_liked_before": False},
                                           {"song_id": 2, "was_liked_before": True}]
    return {"event_id": identity, "graph_mode": "handoff", "thread_key": digest("test-write"), "at_utc": at.isoformat(),
            "experiment_id": "exp", "variant_id": variant, "configuration_digest": "sha256:weights",
            "recommendation_context": {"song_ids": [row["song_id"] for row in songs], "status": status,
                "like_snapshot": {"status": snapshot, "songs": songs}}}


def receipt(*, at=NOW + timedelta(days=1), song=1, liked=True, thread=None, operation="op1"):
    return LikeReceipt(operation_id=operation, graph_mode="handoff", thread_key=thread or digest("test-write"),
                       at_utc=at, song_id=song, liked=liked, confirmation_source="response")


def calculate(events, receipts, *, until=NOW + timedelta(days=8)):
    return online_conversion([exposure_from_event(row) for row in events], receipts,
                             EvaluationConfig(), observed_until=until)


def test_conversion_excludes_preliked_and_deduplicates_ingestion_and_receipts():
    rows = calculate([event(), event()], [receipt(), receipt()])
    assert rows[0]["value"] == 1 and rows[0]["denominator"] == 1
    assert rows[0]["excluded_preliked"] == 1 and rows[0]["recommendation_events"] == 1


@pytest.mark.parametrize("time,hit", [(NOW, 0), (NOW + timedelta(days=7), 1), (NOW + timedelta(days=7, seconds=1), 0)])
def test_exact_window_boundaries(time, hit):
    assert calculate([event()], [receipt(at=time)])[0]["numerator"] == hit


def test_unliked_other_thread_and_late_receipts_never_convert():
    rows = calculate([event()], [receipt(liked=False), receipt(thread="other", operation="op2"),
                               receipt(at=NOW - timedelta(days=1), operation="op3")])
    assert rows[0]["numerator"] == 0


def test_failed_unknown_old_and_unmatured_events_do_not_enter_denominator():
    old = event(identity="old")
    del old["recommendation_context"]["like_snapshot"]
    rows = calculate([event(identity="unknown", snapshot="unavailable"), old,
                      event(identity="failed", status="failed"), event(identity="late", at=NOW + timedelta(days=3))], [receipt()])
    assert rows[0]["denominator"] == 0 and rows[0]["value"] is None
    assert rows[0]["excluded_unknown"] == 4 and rows[0]["excluded_failed"] == 2
    assert rows[0]["excluded_unmatured"] == 1


def test_variants_and_configuration_digests_are_separate_and_conflicts_fail_closed():
    other = event(identity="other", variant="treatment")
    groups = calculate([event(), other], [receipt()])
    assert len(groups) == 2
    changed = event(identity="changed")
    changed["configuration_digest"] = "new-weights"
    assert len(calculate([event(), changed], [])) == 2
    with pytest.raises(ValueError):
        calculate([event(), event(status="failed")], [])
    with pytest.raises(ValueError):
        calculate([event()], [receipt(), receipt(liked=False)])


def test_invalid_snapshot_boolean_and_naive_time_rejected():
    invalid = event(songs=[{"song_id": 1, "was_liked_before": "false"}])
    with pytest.raises(ValueError):
        exposure_from_event(invalid)
    invalid = event(at=NOW.replace(tzinfo=None))
    with pytest.raises(ValueError):
        exposure_from_event(invalid)
    invalid = event()
    invalid["recommendation_context"]["like_snapshot"]["songs"].pop()
    with pytest.raises(ValueError):
        exposure_from_event(invalid)


@pytest.mark.asyncio
async def test_real_journal_adapter_reads_confirmed_artifact_and_scope_only(journal, tmp_path):
    execute = AsyncMock(return_value=ToolMessage(content=json.dumps({"song_id": 1, "liked": True,
            "status": "synced", "message": "confirmed"}), tool_call_id="one"))
    journal.writer.now = lambda: NOW + timedelta(days=1)
    await journal.call(request("set_song_liked_tool", {"song_id": 1, "liked": True}), execute)
    store, identity = journal.writer.store, journal.identity
    scope = journal.accounting.lease.scope
    receipts, excluded = read_journal_like_receipts(store, identity, expected_auth_scope=scope)
    assert len(receipts) == 1 and not excluded and receipts[0].song_id == 1
    assert receipts[0].at_utc == NOW + timedelta(days=1)
    other, excluded = read_journal_like_receipts(store, identity, expected_auth_scope="other")
    assert not other and excluded == {"scope_or_effect_mismatch": 1}
    log = tmp_path / "events.log"
    log.write_text("INFO curation.recommendation " + json.dumps(event()) + "\n", encoding="utf-8")
    report = run_online_job(log, store, identity, expected_auth_scope=scope, observed_until=NOW + timedelta(days=8),
                            output=tmp_path / "report")
    assert report["online_groups"][0]["value"] == 1 and report["play_completion_rate"] is None
    assert scope not in json.dumps(report) and "test-write" not in json.dumps(report)
    execute.assert_awaited_once()  # Evaluation never repeats the business write.


def test_log_loader_report_format_grouping_and_no_overwrite(tmp_path):
    log = tmp_path / "events.log"
    log.write_text(json.dumps(event()) + "\ncuration.recommendation {}\nignored\n", encoding="utf-8")
    events, errors = load_recommendation_events(log)
    assert len(events) == 1 and errors == 1
    row = offline_replay(range(1, 11), lambda train: [1], EvaluationConfig(), experiment_id="exp", variant_id="control")
    assert aggregate_offline([row, row])[0]["sample_size"] == 4
    output = tmp_path / "report"
    result = write_report(output, offline=[row], online=[], config=EvaluationConfig(),
                          dataset_kind="synthetic_contract_fixture", evidence={"case": "synthetic"})
    assert (output / "metrics.json").is_file() and "not_connected" in (output / "report.md").read_text()
    assert result["significance_test"] == "not_performed"
    with pytest.raises(FileExistsError):
        write_report(output, offline=[], online=[], config=EvaluationConfig(), dataset_kind="synthetic", evidence={})


def test_completion_schema_exists_but_no_metric_is_fabricated():
    value = PlaybackCompletionEvent(event_id="event", playback_id="play", graph_mode="handoff",
        thread_key="existing", at_utc=NOW, song_id=1, experiment_id=None, variant_id=None,
        listened_seconds=90, duration_seconds=100)
    assert value.duration_seconds == 100


@pytest.mark.asyncio
async def test_full_curator_read_phase_reuses_model_catalog_and_refuses_all_business_writes(monkeypatch, journal):
    from langgraph.runtime import Runtime
    from app.agent.foundation.routing import RouteTask, RouteKey, RoutingContext
    from app.agent.handoff import curator
    from app.schemas.context import MusicAgentContext
    from app.tools.music import MUSIC_AGENT_TOOLS
    from tests.test_curator_curation_v1 import Catalog, responses
    from tests.test_curator_weights_ab import empty_knowledge

    catalog = Catalog(monkeypatch)
    empty_knowledge(monkeypatch)
    config = EvaluationConfig()
    liked = [song.song_id for song in catalog.songs]
    training, validation = random_holdout(liked, config)
    task = RouteTask(task_id="eval-read-phase", route_key=RouteKey.TOOL_CALLING, input_text="雨天夜跑歌单")
    state = {"messages": [], "current_input": task.input_text, "routing_context": RoutingContext(),
             "task_results": [], "continuity_context": {}}
    runtime = Runtime(context=MusicAgentContext(session_cookie="isolated-fixture"))
    async def recommend(actual):
        assert actual == training and not actual & validation
        return await replay_curator_read_phase(actual, task=task, state=state, runtime=runtime,
            request=curator.curation_request(task.input_text), model=responses(catalog),
            tools=MUSIC_AGENT_TOOLS, operations=journal)
    row = await offline_replay_async(liked, recommend, config)
    assert row["validation_size"] == 3 and row["numerator"] == 3
    assert not catalog.members and not catalog.exists
    assert not [call for call in catalog.calls if call[0] in {"create", "add"}]
    assert [call for call in catalog.calls if call[0] == "search"] == [("search", "夜跑"), ("search", "雨夜")]
    assert all(event.payload["status"] != "success" for event in journal.write_events())


@pytest.mark.asyncio
async def test_confirmed_readback_is_observed_receipt_and_unconfirmed_writes_are_excluded(journal):
    from types import SimpleNamespace
    from app.agent.foundation.long_horizon.operations import OperationJournal
    from app.tools.music import MusicServiceError

    journal.writer.now = lambda: NOW + timedelta(days=2)
    operations = OperationJournal(journal.writer, journal.accounting,
        read_client=SimpleNamespace(get_liked_song_ids=AsyncMock(return_value=[1])))
    execute = AsyncMock(side_effect=MusicServiceError("http_502", "response unknown"))
    await operations.call(request("set_song_liked_tool", {"song_id": 1, "liked": True}), execute)
    rows, excluded = read_journal_like_receipts(journal.writer.store, journal.identity,
                                               expected_auth_scope=journal.accounting.lease.scope)
    assert len(rows) == 1 and rows[0].confirmation_source == "readback" and not excluded
    assert calculate([event()], rows)[0]["numerator"] == 1


def test_unauthorized_journal_identity_is_rejected_without_reads(journal):
    with pytest.raises(ValueError):
        read_journal_like_receipts(journal.writer.store, journal.identity, expected_auth_scope=None)
    invalid = journal.identity.model_copy(update={"conversation_id": "other"})
    with pytest.raises(ValueError, match="ownership"):
        read_journal_like_receipts(journal.writer.store, invalid, expected_auth_scope=journal.accounting.lease.scope)
