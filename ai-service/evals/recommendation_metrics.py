"""Restricted recommendation metrics: snapshots and confirmed receipts, not causality."""

from dataclasses import dataclass
from datetime import datetime, timedelta
import math
import random
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, StrictBool, StrictInt, field_validator

from app.agent.foundation.long_horizon.history_writer import digest

PLAY_COMPLETION_STATUS = "not_connected"
METRIC_DEFINITIONS = {
    "historical_like_hit_rate": {
        "numerator": "distinct held-out liked song IDs recommended",
        "denominator": "distinct held-out liked song IDs",
        "window": "current snapshot; seeded random holdout, no chronological window",
        "source": "get_liked_songs current cloud snapshot",
    },
    "like_conversion_rate": {
        "numerator": "eligible exposures with a confirmed liked=true receipt within the configured window",
        "denominator": "matured recommended song exposures with a successful snapshot and was_liked_before=false",
        "window": "(recommendation at_utc, at_utc + conversion_window_days]",
        "source": "curation.recommendation emission snapshot + verified OperationJournal receipts",
    },
    "play_completion_rate": {
        "numerator": "plays reaching the configured completion fraction",
        "denominator": "eligible started plays",
        "window": "configured observation window",
        "source": "not_connected; PlaybackCompletionEvent is a contract only",
    },
}


@dataclass(frozen=True)
class EvaluationConfig:
    holdout_ratio: float = 0.2
    seed: int = 20261011
    conversion_window_days: float = 7.0
    completion_fraction: float = 0.9

    def __post_init__(self):
        for name in ("holdout_ratio", "conversion_window_days", "completion_fraction"):
            value = getattr(self, name)
            if type(value) not in (int, float) or not math.isfinite(value):
                raise ValueError(f"{name} must be finite")
        if not 0 <= self.holdout_ratio <= 1 or self.conversion_window_days <= 0:
            raise ValueError("invalid holdout ratio or conversion window")
        if not 0 < self.completion_fraction <= 1 or type(self.seed) is not int:
            raise ValueError("invalid completion fraction or seed")

    @property
    def window(self):
        return timedelta(days=self.conversion_window_days)


def song_ids(values):
    values = tuple(values)
    if any(type(value) is not int or value <= 0 for value in values):
        raise ValueError("song IDs must be positive integers")
    return set(values)


def random_holdout(liked_ids, config):
    # Sort BEFORE sampling: sets/API order must not change a seeded split.
    ordered = sorted(song_ids(liked_ids))
    count = math.floor(len(ordered) * config.holdout_ratio)
    validation = set(random.Random(config.seed).sample(ordered, count))
    return frozenset(set(ordered) - validation), frozenset(validation)


def metric(numerator, denominator):
    return {"numerator": numerator, "denominator": denominator,
            "sample_size": denominator, "value": numerator / denominator if denominator else None}


def historical_like_hit_rate(validation, recommended):
    validation, recommended = song_ids(validation), song_ids(recommended)
    return metric(len(validation & recommended), len(validation))


class TimedContract(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    @field_validator("at_utc", check_fields=False)
    @classmethod
    def aware_time(cls, value):
        if value.utcoffset() is None:
            raise ValueError("timezone-aware timestamp required")
        return value


class SongSnapshot(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    song_id: StrictInt = Field(gt=0)
    was_liked_before: StrictBool | None


class RecommendationExposure(TimedContract):
    event_id: str = Field(min_length=1)
    graph_mode: str = Field(min_length=1)
    thread_key: str = Field(min_length=1)
    at_utc: datetime
    experiment_id: str | None
    variant_id: str | None
    configuration_digest: str = Field(min_length=1)
    status: str
    snapshot_status: Literal["available", "unavailable", "empty"]
    songs: tuple[SongSnapshot, ...]

    @property
    def group(self):
        # Same variant with changed weights must never be silently pooled.
        return self.experiment_id, self.variant_id, self.configuration_digest


class LikeReceipt(TimedContract):
    operation_id: str = Field(min_length=1)
    graph_mode: str = Field(min_length=1)
    thread_key: str = Field(min_length=1)
    at_utc: datetime
    song_id: StrictInt = Field(gt=0)
    liked: StrictBool
    confirmation_source: Literal["response", "readback"]


class PlaybackCompletionEvent(TimedContract):
    """Future data contract only. No collector or completion calculation exists."""
    event_id: str = Field(min_length=1)
    playback_id: str = Field(min_length=1)
    graph_mode: str = Field(min_length=1)
    thread_key: str = Field(min_length=1)
    at_utc: datetime
    song_id: StrictInt = Field(gt=0)
    experiment_id: str | None
    variant_id: str | None
    listened_seconds: float = Field(ge=0, allow_inf_nan=False)
    duration_seconds: float = Field(gt=0, allow_inf_nan=False)


def exposure_from_event(event):
    context = event["recommendation_context"]
    snapshot = context.get("like_snapshot", {})
    ids = song_ids(context["song_ids"])
    rows = snapshot.get("songs", [])
    if len(rows) != len({row["song_id"] for row in rows}):
        raise ValueError("duplicate snapshot songs")
    if snapshot.get("status") == "available" and {row["song_id"] for row in rows} != ids:
        raise ValueError("snapshot must cover exactly the recommended songs")
    # Old events are unmeasured, never retrospectively interpreted as unliked.
    if snapshot.get("status") != "available":
        rows = [{"song_id": value, "was_liked_before": None} for value in sorted(ids)]
    return RecommendationExposure(
        event_id=event["event_id"], graph_mode=event["graph_mode"], thread_key=event["thread_key"],
        at_utc=event["at_utc"], experiment_id=event.get("experiment_id"), variant_id=event.get("variant_id"),
        configuration_digest=event["configuration_digest"], status=context["status"],
        snapshot_status=snapshot.get("status", "unavailable"), songs=tuple(rows))


def _deduplicate(rows, key):
    unique = {}
    for row in rows:
        identity = key(row)
        if identity in unique and unique[identity] != row:
            raise ValueError("conflicting duplicate evaluation evidence")
        unique[identity] = row
    return tuple(unique.values())


def online_conversion(exposures, receipts, config, *, observed_until):
    if observed_until.utcoffset() is None:
        raise ValueError("timezone-aware observation cutoff required")
    receipts = _deduplicate(receipts, lambda row: (row.graph_mode, row.thread_key, row.operation_id))
    indexed = {}
    for row in receipts:
        if row.liked:
            indexed.setdefault((row.graph_mode, row.thread_key, row.song_id), []).append(row.at_utc)
    groups = {}
    for row in _deduplicate(exposures, lambda row: row.event_id):
        group = groups.setdefault(row.group, {"numerator": 0, "denominator": 0, "recommendation_events": 0,
                                             "excluded_preliked": 0, "excluded_unknown": 0,
                                             "excluded_unmatured": 0, "excluded_failed": 0})
        group["recommendation_events"] += 1
        for song in row.songs:
            if row.status != "success":
                group["excluded_failed"] += 1
            elif row.snapshot_status != "available" or song.was_liked_before is None:
                group["excluded_unknown"] += 1
            elif song.was_liked_before:
                group["excluded_preliked"] += 1
            elif row.at_utc + config.window > observed_until:
                group["excluded_unmatured"] += 1
            else:
                group["denominator"] += 1
                group["numerator"] += int(any(row.at_utc < time <= row.at_utc + config.window
                    for time in indexed.get((row.graph_mode, row.thread_key, song.song_id), ())))
    return [{"experiment_id": key[0], "variant_id": key[1], "configuration_digest": key[2],
             **values, **metric(values["numerator"], values["denominator"])}
            for key, values in sorted(groups.items(), key=lambda item: str(item[0]))]


def read_journal_like_receipts(store, identity, *, expected_auth_scope):
    """Only read an already-authorized conversation; never emit account IDs/scopes.

    Caller supplies the existing authenticated scope, NOT one inferred from logs.
    Use exactly the existing ACK conversation normalization and digest scheme.
    """
    if not expected_auth_scope:
        raise ValueError("authenticated journal scope required")
    task = store.get_task(identity.continuity_task_id)
    session = store.get_session(identity.session_id)
    if (task is None or task.conversation_id != identity.conversation_id or session is None
        or session.task_id != identity.continuity_task_id or session.conversation_id != identity.conversation_id):
        raise ValueError("journal conversation ownership mismatch")
    thread = identity.conversation_id
    if identity.graph_mode == "handoff":
        thread = thread.removeprefix("handoff:v1:")
    receipts, excluded, cursor = [], {}, None
    while True:
        rows = store.query_task_history(identity.continuity_task_id,
            event_type="tool_result", after_seq=cursor, limit=100)
        if not rows:
            break
        cursor = rows[-1].event_seq
        for event in rows:
            body = event.payload or {}
            if body.get("name") != "set_song_liked_tool" or body.get("status") != "success":
                continue
            reason = None
            ref = body.get("result_ref") or {}
            artifact = store.get_artifact(ref.get("target_id")) if ref.get("target_id") else None
            if body.get("effect") != "write" or body.get("auth_scope") != expected_auth_scope:
                reason = "scope_or_effect_mismatch"
            elif body.get("confirmation_source") not in {"response", "readback"}:
                reason = "unconfirmed"
            elif (artifact is None or ref.get("kind") != "artifact" or not isinstance(artifact.payload, dict)
                  or artifact.task_id != identity.continuity_task_id
                  or digest(artifact.payload) != artifact.content_hash):
                reason = "missing_or_invalid_artifact"
            else:
                result = artifact.payload.get("result", {})
                args = body.get("parameters", {})
                if (artifact.payload.get("name") != "set_song_liked_tool"
                    or result.get("status") != "synced" or type(result.get("liked")) is not bool
                    or result.get("liked") != args.get("liked") or result.get("song_id") != args.get("song_id")):
                    reason = "receipt_result_mismatch"
            if reason:
                excluded[reason] = excluded.get(reason, 0) + 1
                continue
            receipts.append(LikeReceipt(operation_id=body["operation_id"], graph_mode=identity.graph_mode,
                thread_key=digest(thread), at_utc=event.timestamp, song_id=result["song_id"],
                liked=result["liked"], confirmation_source=body["confirmation_source"]))
    return receipts, excluded
