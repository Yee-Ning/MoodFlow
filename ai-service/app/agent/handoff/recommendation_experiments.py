"""Multi-variant weight allocation on existing hash and metadata event channels."""

from dataclasses import asdict, dataclass
from fractions import Fraction
import json
import math
import re

from app.agent.foundation.long_horizon.history_writer import _check_public_text
from app.agent.handoff.observability import digest, emit
from app.agent.handoff.recommendation import PLAYBACK_HISTORY_STATUS, RecommendationWeights
from app.agent.handoff.rollout import deterministic_bucket


def _public_id(value):
    if not isinstance(value, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}", value):
        raise ValueError("experiment/variant IDs must be bounded public identifiers")
    _check_public_text(value)


@dataclass(frozen=True)
class ExperimentVariant:
    variant_id: str
    weights: RecommendationWeights
    traffic_ratio: float

    def __post_init__(self):
        _public_id(self.variant_id)
        if not isinstance(self.weights, RecommendationWeights):
            raise ValueError("variant must bind a validated weight configuration")
        try:
            valid = type(self.traffic_ratio) in (int, float) and math.isfinite(self.traffic_ratio) and self.traffic_ratio >= 0
        except OverflowError:
            valid = False
        if not valid:
            raise ValueError("traffic ratio must be a finite nonnegative number")


@dataclass(frozen=True)
class ExperimentConfig:
    experiment_id: str
    variants: tuple[ExperimentVariant, ...]

    def __post_init__(self):
        _public_id(self.experiment_id)
        object.__setattr__(self, "variants", tuple(self.variants))
        if (len(self.variants) < 2 or any(not isinstance(variant, ExperimentVariant) for variant in self.variants)
                or len({variant.variant_id for variant in self.variants}) != len(self.variants)
                or not any(variant.traffic_ratio for variant in self.variants)):
            raise ValueError("experiment needs unique variants and positive total traffic")

    @property
    def configuration_digest(self):
        return digest(json.dumps(asdict(self), sort_keys=True, separators=(",", ":")))


@dataclass(frozen=True)
class ExperimentAssignment:
    experiment_id: str | None
    variant_id: str | None
    weights: RecommendationWeights
    status: str
    configuration_digest: str | None = None


def get_recommendation_experiment() -> ExperimentConfig | None:
    # No deployment/runtime switch is changed. Trusted callers inject a config.
    return None


def assign_recommendation_variant(config, user_id, fallback_weights):
    if not isinstance(fallback_weights, RecommendationWeights):
        raise ValueError("fallback weights must be a validated configuration")
    if config is None:
        return ExperimentAssignment(None, None, fallback_weights, "disabled")
    if not isinstance(config, ExperimentConfig):
        raise ValueError("experiment config must be validated")
    if user_id is None or user_id == "":
        # A thread ID, cookie, fabricated guest ID or model value is not a user ID.
        return ExperimentAssignment(config.experiment_id, None, fallback_weights, "missing_user_id", config.configuration_digest)
    if not isinstance(user_id, str) or not user_id.strip() or len(user_id) > 160:
        raise ValueError("user ID must come from the existing authenticated runtime")
    bucket = deterministic_bucket("curation-experiment:v1:" + config.experiment_id, user_id)
    ratios = [Fraction(str(variant.traffic_ratio)) for variant in config.variants]
    point, cumulative = Fraction(bucket, 10_000) * sum(ratios), Fraction(0)
    for variant, ratio in zip(config.variants, ratios, strict=True):
        cumulative += ratio
        if point < cumulative:
            return ExperimentAssignment(config.experiment_id, variant.variant_id, variant.weights,
                                        "assigned", config.configuration_digest)
    raise AssertionError("validated traffic must cover all hash buckets")


def record_recommendation(assignment, *, candidate_count, song_ids, tag_count, niche_requested, status):
    # Existing request/thread keys are reused. Do not create/log a raw or hashed
    # user identifier, raw query, history, liked IDs or playback measurements.
    fields = {"experiment_id": assignment.experiment_id, "variant_id": assignment.variant_id,
              "assignment_status": assignment.status, "configuration_digest": assignment.configuration_digest,
              "weights": asdict(assignment.weights), "playback_history_status": PLAYBACK_HISTORY_STATUS,
              "recommendation_context": {"candidate_count": candidate_count, "song_ids": song_ids,
                                         "scene_tag_count": tag_count, "niche_requested": niche_requested,
                                         "status": str(status)}}
    _check_public_text(fields)
    return emit("curation.recommendation", fields)
