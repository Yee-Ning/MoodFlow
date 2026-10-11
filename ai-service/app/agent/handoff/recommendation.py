"""Configured sorting over existing candidates; no additional preference source."""

from dataclasses import dataclass
from fractions import Fraction
import math

PLAYBACK_HISTORY_STATUS = "not_connected"


@dataclass(frozen=True)
class RecommendationWeights:
    user_preference: float = 4.0
    knowledge_association: float = 2.0
    scene_tags: float = 1.0

    def __post_init__(self):
        values = (self.user_preference, self.knowledge_association, self.scene_tags)
        try:
            valid = all(type(value) in (int, float) and math.isfinite(value) and value >= 0 for value in values)
        except OverflowError:
            valid = False
        if not valid or not any(values):
            raise ValueError("weights must be finite, nonnegative numbers with a positive total")

    def normalized(self):
        values = tuple(Fraction(str(value)) for value in (
            self.user_preference, self.knowledge_association, self.scene_tags))
        total = sum(values)
        return tuple(value / total for value in values)


# Scene matching is normalized to [0, 1]. 4 > 2 + 1 and 2 > 1 preserve
# baseline liked > discovered > tag-hit order exactly, including stable ties.
DEFAULT_RECOMMENDATION_WEIGHTS = RecommendationWeights()


def get_recommendation_weights():
    return DEFAULT_RECOMMENDATION_WEIGHTS


def uses_baseline_order(weights):
    return weights.normalized() == DEFAULT_RECOMMENDATION_WEIGHTS.normalized()


def user_preference_factor(song_id, liked_ids, *, playback_history=None):
    """Likes only: timestamped/untimestamped inputs are NOT verified play facts.

    Gate 0 found no connected playback-history schema/update/retention contract.
    Do not reinterpret profile updated_at, queue state or action ACK as listening.
    The opaque optional input is deliberately ignored until a verified adapter exists.
    """
    del playback_history
    return Fraction(int(song_id in liked_ids))


def recommendation_factors(song, liked_ids, knowledge_ids, tags, *, playback_history=None):
    text = (song.name + " " + song.album + " " + " ".join(song.artists)).casefold()
    hits = sum(tag.casefold() in text for tag in tags)
    return (user_preference_factor(song.song_id, liked_ids, playback_history=playback_history),
            Fraction(int(song.song_id in knowledge_ids)),
            Fraction(hits, len(tags)) if tags else Fraction(0))


def recommendation_score(song, liked_ids, knowledge_ids, tags, *, weights=None, playback_history=None):
    weights = weights if weights is not None else get_recommendation_weights()
    factors = recommendation_factors(song, liked_ids, knowledge_ids, tags, playback_history=playback_history)
    # Exact rational comparison avoids rounding ties and overflowing large weights.
    return sum(weight * factor for weight, factor in zip(weights.normalized(), factors, strict=True))


def rank_recommendations(songs, liked_ids, knowledge_ids, tags, *, weights=None, playback_history=None):
    """Stable sorting only; no recall, padding, source reads or candidate truncation."""
    return sorted(songs, key=lambda song: -recommendation_score(
        song, liked_ids, knowledge_ids, tags, weights=weights, playback_history=playback_history))


def recommendation_metadata(song, liked_ids, knowledge_ids, tags, weights):
    return {"score": float(recommendation_score(song, liked_ids, knowledge_ids, tags, weights=weights)),
            "factors": [float(value) for value in recommendation_factors(song, liked_ids, knowledge_ids, tags)],
            "playback_history_status": PLAYBACK_HISTORY_STATUS}


def order_recommendation_selection(songs, liked_ids, knowledge_ids, tags, *, weights=None, niche_order=None):
    weights = weights if weights is not None else get_recommendation_weights()
    if not uses_baseline_order(weights):
        return rank_recommendations(songs, liked_ids, knowledge_ids, tags, weights=weights)
    # Preserve the baseline's post-model order as well as its pre-model sorting.
    ordered = sorted(songs, key=lambda song: song.song_id not in liked_ids)
    if niche_order is not None:
        return sorted(ordered, key=lambda song: niche_order[song.song_id])
    preferred = [song for song in ordered if song.song_id in liked_ids]
    knowledge = [song for song in ordered if song.song_id in knowledge_ids and song.song_id not in liked_ids]
    original = [song for song in ordered if song.song_id not in knowledge_ids and song.song_id not in liked_ids]
    while knowledge or original:
        if knowledge:
            preferred.append(knowledge.pop(0))
        if original:
            preferred.append(original.pop(0))
    return preferred
