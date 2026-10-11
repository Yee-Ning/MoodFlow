"""Default compatibility, injected weights and disconnected history boundaries."""

from itertools import product
from types import SimpleNamespace

import pytest

from app.agent.handoff.recommendation import (
    PLAYBACK_HISTORY_STATUS, RecommendationWeights, order_recommendation_selection,
    rank_recommendations, recommendation_score, user_preference_factor, uses_baseline_order,
)


def track(identity, *, name="track", album="", artists=()):
    return SimpleNamespace(song_id=identity, name=name, album=album, artists=list(artists))


def test_default_weights_match_legacy_tuple_for_all_factor_combinations():
    tags = ["rain", "run", "night", "warm", "calm", "fast"]
    songs, liked, knowledge = [], set(), set()
    for identity, (preferred, discovered, hits) in enumerate(product((False, True), (False, True), range(7)), 1):
        songs.append(track(identity, name=" ".join(tags[:hits])))
        if preferred:
            liked.add(identity)
        if discovered:
            knowledge.add(identity)
    before = list(songs)
    expected = sorted(songs, key=lambda song: (song.song_id not in liked, song.song_id not in knowledge,
                                               -sum(tag in song.name for tag in tags)))
    assert rank_recommendations(songs, liked, knowledge, tags) == expected
    assert songs == before and set(row.song_id for row in expected) == set(row.song_id for row in songs)


def test_nondefault_weights_reorder_only_existing_candidates_and_keep_stable_ties():
    songs = [track(1), track(2, name="rain"), track(3, name="rain"), track(4)]
    assert [row.song_id for row in rank_recommendations(songs, {1}, {4}, ["rain"])] == [1, 4, 2, 3]
    assert [row.song_id for row in rank_recommendations(songs, {1}, {4}, ["rain"],
                                                     weights=RecommendationWeights(0, 0, 1))] == [2, 3, 1, 4]
    assert rank_recommendations([], set(), set(), []) == []


def test_default_post_model_interleave_and_niche_order_are_preserved():
    songs = [track(i) for i in (1, 2, 3, 4, 5, 6)]
    assert [row.song_id for row in order_recommendation_selection(songs, {3, 6}, {2, 4}, [])] == [3, 6, 2, 1, 4, 5]
    niche = {1: 5, 2: 4, 3: 3, 4: 2, 5: 1, 6: 0}
    assert [row.song_id for row in order_recommendation_selection(songs, {3}, {2}, [], niche_order=niche)] == [6, 5, 4, 3, 2, 1]
    assert uses_baseline_order(RecommendationWeights(8, 4, 2))


@pytest.mark.parametrize("history", [None, [1], [{"song_id": 1, "played_at": "2026-10-10T10:00:00Z"}],
                                     [{"song_id": 1, "count": 999999}]])
def test_timestamped_or_untimestamped_unverified_history_cannot_fill_preference(history):
    assert PLAYBACK_HISTORY_STATUS == "not_connected"
    assert user_preference_factor(1, set(), playback_history=history) == 0
    assert user_preference_factor(1, {1}, playback_history=history) == 1
    assert recommendation_score(track(1), set(), set(), [], playback_history=history) == 0


@pytest.mark.parametrize("values", [(-1, 1, 1), (0, 0, 0), (True, 1, 1), ("1", 1, 1),
                                    (float("nan"), 1, 1), (float("inf"), 1, 1)])
def test_invalid_weight_config_is_rejected(values):
    with pytest.raises(ValueError):
        RecommendationWeights(*values)


def test_weight_scale_does_not_change_order_or_overflow():
    songs = [track(1), track(2, name="rain")]
    small = RecommendationWeights(1e-300, 1e-300, 1e-300)
    large = RecommendationWeights(1e300, 1e300, 1e300)
    assert rank_recommendations(songs, set(), set(), ["rain"], weights=small) == [songs[1], songs[0]]
    assert rank_recommendations(songs, set(), set(), ["rain"], weights=small) == rank_recommendations(
        songs, set(), set(), ["rain"], weights=large)
