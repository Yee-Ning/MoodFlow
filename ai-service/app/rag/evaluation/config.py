"""Versioned policy for offline RAG evaluation."""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

import yaml
from pydantic import BaseModel, ConfigDict, Field, model_validator


DEFAULT_EVALUATION_CONFIG_PATH = Path(__file__).with_name("config") / "v0.yaml"


class JudgeMetricConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    name: str = Field(min_length=1, max_length=100)
    threshold: float = Field(default=0.5, ge=0, le=1)


class EvaluationConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: str = Field(pattern=r"^v\d+$")
    expected_case_count: int = Field(default=50, ge=1, le=10_000)
    minimum_approved_cases: int = Field(default=30, ge=1, le=10_000)
    candidate_top_k: int = Field(default=40, ge=1, le=200)
    rerank_top_k: int = Field(default=12, ge=1, le=15)
    context_top_k: int = Field(default=8, ge=1, le=10)
    citation_id_validity_threshold: float = Field(default=1, ge=0, le=1)
    token_budget_compliance_threshold: float = Field(default=1, ge=0, le=1)
    judge_enabled_by_default: bool = False
    judge_metrics: list[JudgeMetricConfig] = Field(min_length=1, max_length=20)

    @model_validator(mode="after")
    def validate_limits(self) -> "EvaluationConfig":
        if self.minimum_approved_cases > self.expected_case_count:
            raise ValueError("minimum approved cases cannot exceed expected case count")
        if self.rerank_top_k > self.candidate_top_k:
            raise ValueError("rerank top-k cannot exceed candidate top-k")
        if self.context_top_k > self.rerank_top_k:
            raise ValueError("context top-k cannot exceed rerank top-k")
        names = [item.name for item in self.judge_metrics]
        if len(names) != len(set(names)):
            raise ValueError("judge metric names must be unique")
        return self


@lru_cache
def load_evaluation_config(
    path: Path = DEFAULT_EVALUATION_CONFIG_PATH,
) -> EvaluationConfig:
    try:
        payload = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as exc:
        raise ValueError(f"evaluation_config_invalid:{path}") from exc
    if not isinstance(payload, dict):
        raise ValueError(f"evaluation_config_invalid:{path}")
    return EvaluationConfig.model_validate(payload)
