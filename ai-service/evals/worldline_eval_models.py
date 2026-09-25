"""Typed contracts for the offline WorldLine evaluation harness."""

from __future__ import annotations

from enum import StrEnum
from hashlib import sha256
import json
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


SCHEMA_VERSION = "worldline-eval-v0"


class WorldLineEvalTier(StrEnum):
    COMPONENT = "component_replay"
    SESSION = "session_replay"
    POSTGRES = "postgres_restart"


class WorldLineEvalTask(StrEnum):
    SEMANTIC_EXPANSION = "semantic_expansion"
    GENRE_EXPLORATION = "genre_exploration"
    ENTITY_RESOLUTION = "entity_resolution"
    GRAPH_MERGE = "graph_merge"
    GROUNDING = "grounding"
    CHECKPOINT_RECOVERY = "checkpoint_recovery"


class LabelProvenance(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    source: Literal[
        "synthetic_contract_fixture",
        "frozen_supported_claim",
        "manual_review",
        "real_gold_fixture",
    ]
    review_status: Literal["approved", "pending", "rejected"]
    reviewer: str | None = Field(default=None, max_length=200)
    notes: str = Field(default="", max_length=2_000)


class WorldLineEvalCase(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal[SCHEMA_VERSION]
    dataset_version: Literal["v0"]
    case_id: str = Field(pattern=r"^wl-[a-z0-9][a-z0-9-]*$")
    tier: WorldLineEvalTier
    task: WorldLineEvalTask
    tags: list[str] = Field(min_length=1, max_length=20)
    input: dict[str, Any]
    fixtures: dict[str, Any]
    expected: dict[str, Any]
    label_provenance: LabelProvenance

    @model_validator(mode="after")
    def validate_contract(self) -> "WorldLineEvalCase":
        if len(self.tags) != len(set(self.tags)):
            raise ValueError("worldline eval tags must be unique")
        if (
            self.tier is WorldLineEvalTier.POSTGRES
            and self.task is not WorldLineEvalTask.CHECKPOINT_RECOVERY
        ):
            raise ValueError("postgres tier only supports checkpoint recovery")
        if (
            self.task is WorldLineEvalTask.CHECKPOINT_RECOVERY
            and self.tier is WorldLineEvalTier.COMPONENT
        ):
            raise ValueError("checkpoint recovery requires session or postgres tier")
        if "positive" in self.tags and "negative" in self.tags:
            raise ValueError("case cannot be both positive and negative")
        outcome = self.expected.get("outcome")
        if not isinstance(outcome, str) or not outcome:
            raise ValueError("expected outcome is required")
        if self.task is WorldLineEvalTask.SEMANTIC_EXPANSION:
            self._validate_semantic_case(outcome)
        if self.task is WorldLineEvalTask.GENRE_EXPLORATION:
            self._validate_genre_case(outcome)
        return self

    def _validate_semantic_case(self, outcome: str) -> None:
        if outcome != "accepted":
            return
        relations = self.expected.get("relations")
        documents = self.fixtures.get("documents")
        if not isinstance(relations, list) or not relations:
            raise ValueError("accepted semantic case requires relations")
        if not isinstance(documents, list) or not documents:
            raise ValueError("accepted semantic case requires documents")
        contents = [
            item.get("content")
            for item in documents
            if isinstance(item, dict) and isinstance(item.get("content"), str)
        ]
        for relation in relations:
            if not isinstance(relation, dict):
                raise ValueError("semantic relation must be an object")
            span = relation.get("exact_span")
            if not isinstance(span, str) or not any(
                span in content for content in contents
            ):
                raise ValueError("semantic exact span must exist in fixture")

    def _validate_genre_case(self, outcome: str) -> None:
        genre_id = self.input.get("target_genre_id")
        if not isinstance(genre_id, str) or not genre_id.startswith("genre:"):
            raise ValueError("genre exploration requires target genre")
        if outcome == "accepted":
            artists = self.expected.get("artists")
            if not isinstance(artists, list) or not artists:
                raise ValueError("accepted genre case requires artists")


class WorldLineEvalResult(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    case_id: str
    tier: WorldLineEvalTier
    task: WorldLineEvalTask
    passed: bool
    status: Literal["completed", "skipped", "error"]
    expected: dict[str, Any]
    actual: dict[str, Any] = Field(default_factory=dict)
    trace: dict[str, Any] = Field(default_factory=dict)
    failures: list[str] = Field(default_factory=list)
    duration_ms: float = Field(ge=0)


class WorldLineEvalRun(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal[SCHEMA_VERSION]
    dataset_version: str = Field(pattern=r"^v\d+(?:\.\d+)?$")
    dataset_kind: Literal[
        "synthetic_contract_fixture",
        "real_gold_fixture",
    ] = "synthetic_contract_fixture"
    dataset_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    source_ledger_sha256: str | None = Field(
        default=None,
        pattern=r"^[0-9a-f]{64}$",
    )
    git_commit: str
    evaluation_timestamp: str
    results: list[WorldLineEvalResult]


def load_worldline_dataset(path: Path) -> list[WorldLineEvalCase]:
    cases: list[WorldLineEvalCase] = []
    seen: set[str] = set()
    for line_number, line in enumerate(
        path.read_text(encoding="utf-8").splitlines(),
        start=1,
    ):
        if not line.strip():
            continue
        try:
            payload = json.loads(line)
            case = WorldLineEvalCase.model_validate(payload)
        except (json.JSONDecodeError, ValueError) as exc:
            raise ValueError(
                f"worldline_eval_dataset_invalid:{path}:{line_number}"
            ) from exc
        if case.case_id in seen:
            raise ValueError(f"worldline_eval_duplicate_case:{case.case_id}")
        seen.add(case.case_id)
        cases.append(case)
    if not cases:
        raise ValueError(f"worldline_eval_dataset_empty:{path}")
    return cases


def approved_cases(cases: list[WorldLineEvalCase]) -> list[WorldLineEvalCase]:
    return [
        case
        for case in cases
        if case.label_provenance.review_status == "approved"
    ]


def dataset_sha256(path: Path) -> str:
    return sha256(path.read_bytes()).hexdigest()
