"""Niche thresholds, unknown values and ranking without fallback."""

from copy import deepcopy

import pytest

from app.agent.handoff.niche import NicheConfig, is_niche, rank_niche


@pytest.mark.parametrize("count,expected", [(99, False), (100, True), (101, True),
                                           (9999, True), (10000, True), (10001, False),
                                           (None, False), (True, False), ("100", False),
                                           (100.0, False), (-1, False)])
def test_double_cutoff_is_inclusive_and_does_not_coerce_unknown(count, expected):
    assert is_niche(count, NicheConfig(100, 10000)) is expected


def test_config_values_control_both_classifier_and_rank_without_mutation():
    candidates = [{"song_id": 1, "comment_count": 900}, {"song_id": 2, "comment_count": 500},
                  {"song_id": 3, "comment_count": 300}, {"song_id": 4, "comment_count": 500},
                  {"song_id": 5}, {"song_id": 6, "comment_count": 999}]
    before = deepcopy(candidates)
    config = NicheConfig(300, 900)
    assert [row["song_id"] for row in rank_niche(candidates, 3, config=config)] == [3, 2, 4]
    assert [row["song_id"] for row in rank_niche(candidates, 20, config=config)] == [3, 2, 4, 1]
    assert not is_niche(299, config) and not is_niche(901, config)
    assert candidates == before


@pytest.mark.parametrize("candidates", [[], [{"song_id": 1, "comment_count": 10001}],
                                       [{"song_id": 1, "comment_count": 99}],
                                       [{"song_id": 1}, {"song_id": 2, "comment_count": None}]])
def test_empty_all_hot_all_noise_or_all_unknown_never_widen(candidates):
    assert rank_niche(candidates, 10, config=NicheConfig(100, 10000)) == []


def test_zero_k_and_single_value_interval():
    candidates = [{"song_id": 1, "comment_count": 500}, {"song_id": 2, "comment_count": 501}]
    assert rank_niche(candidates, 0, config=NicheConfig(500, 500)) == []
    assert rank_niche(candidates, 10, config=NicheConfig(500, 500)) == [candidates[0]]


@pytest.mark.parametrize("bounds", [(-1, 100), (100, 99), (True, 100), (100, "200")])
def test_invalid_config_is_rejected(bounds):
    with pytest.raises(ValueError):
        NicheConfig(*bounds)
