"""Stable allocation, traffic edges, privacy and legacy canary reuse."""

import hashlib
import json
import logging

import pytest

from app.agent.handoff.recommendation import RecommendationWeights
from app.agent.handoff.recommendation_experiments import (
    ExperimentConfig, ExperimentVariant, assign_recommendation_variant, record_recommendation,
)
from app.agent.handoff.rollout import deterministic_bucket, select_chat_graph
from tests.test_handoff_rollout import configuration


def experiment(ratios=(50, 30, 20), identity="curation-v1"):
    return ExperimentConfig(identity, tuple(ExperimentVariant(name, weights, ratio) for name, weights, ratio in zip(
        ("control", "knowledge", "scene"),
        (RecommendationWeights(), RecommendationWeights(1, 4, 1), RecommendationWeights(0, 0, 1)),
        ratios, strict=True)))


def test_existing_canary_hash_and_selection_remain_exact():
    settings = configuration(handoff_canary_enabled=True)
    for index in range(1000):
        identity = f"thread-{index}"
        old = int.from_bytes(hashlib.sha256((settings.handoff_canary_salt + "\0" + identity).encode()).digest()[:8], "big") % 10000
        assert deterministic_bucket(settings.handoff_canary_salt, identity) == old
        assert select_chat_graph(settings, identity) == ("handoff" if old < 1000 else "foundation")


def test_same_user_and_same_config_stay_stable_without_storage():
    config = experiment()
    assignments = [assign_recommendation_variant(config, f"user-{index}", RecommendationWeights()) for index in range(10000)]
    assert assignments == [assign_recommendation_variant(experiment(), f"user-{index}", RecommendationWeights()) for index in range(10000)]
    counts = {name: sum(row.variant_id == name for row in assignments) for name in ("control", "knowledge", "scene")}
    assert 4700 < counts["control"] < 5300 and 2700 < counts["knowledge"] < 3300 and 1700 < counts["scene"] < 2300
    assert {row.weights for row in assignments} == {variant.weights for variant in config.variants}


@pytest.mark.parametrize("ratios,expected", [((0, 0, 100), "scene"), ((100, 0, 0), "control"), ((0, 100, 0), "knowledge")])
def test_zero_traffic_never_assigned(ratios, expected):
    assert {assign_recommendation_variant(experiment(ratios), f"user-{i}", RecommendationWeights()).variant_id
            for i in range(100)} == {expected}


def test_missing_user_falls_back_without_fabricated_identity():
    fallback = RecommendationWeights(3, 1, 1)
    assigned = assign_recommendation_variant(experiment(), None, fallback)
    assert assigned.status == "missing_user_id" and assigned.variant_id is None and assigned.weights == fallback
    assert assign_recommendation_variant(None, "known", fallback).status == "disabled"
    with pytest.raises(ValueError):
        assign_recommendation_variant(experiment(), True, fallback)


@pytest.mark.parametrize("ratios", [(0, 0, 0), (-1, 50, 51), (float("nan"), 50, 50), (True, 1, 1)])
def test_invalid_ratios_rejected(ratios):
    with pytest.raises(ValueError):
        experiment(ratios)


def test_duplicate_variant_and_secret_like_ids_rejected():
    variant = ExperimentVariant("control", RecommendationWeights(), 1)
    with pytest.raises(ValueError):
        ExperimentConfig("duplicate", (variant, variant))
    with pytest.raises(ValueError):
        experiment(identity="sk-ABCDEFGHIJKLMN")


def test_event_reuses_existing_channel_without_new_user_identifier_or_behavior_metrics(caplog):
    caplog.set_level(logging.INFO, logger="app.agent.handoff.observability")
    raw_user = "PRIVATE_USER_IDENTIFIER_DO_NOT_LOG"
    assignment = assign_recommendation_variant(experiment(), raw_user, RecommendationWeights())
    event = record_recommendation(assignment, candidate_count=20, song_ids=[1, 2], tag_count=2,
                                  niche_requested=False, status="success")
    text = json.dumps(event)
    assert raw_user not in text and raw_user not in caplog.text
    assert event["experiment_id"] == "curation-v1" and event["variant_id"] in {"control", "knowledge", "scene"}
    assert event["at_utc"] and event["event_id"] and "curation.recommendation" in caplog.text
    assert "user_id" not in event and "user_key" not in event and "play_count" not in text
    assert event["playback_history_status"] == "not_connected"
