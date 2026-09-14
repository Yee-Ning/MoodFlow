"""Versioned policy for the application-side reranker."""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

import yaml
from pydantic import BaseModel, ConfigDict, Field, model_validator


DEFAULT_RERANK_CONFIG_PATH = Path(__file__).with_name("config") / "v1.yaml"


class RerankModelConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    name: str = Field(min_length=1, max_length=300)
    revision: str = Field(pattern=r"^[0-9a-f]{40}$")
    input_token_limit: int = Field(
        default=1_024,
        ge=256,
        le=8_192,
        description=(
            "MoodFlow latency and memory ceiling, not the upstream model limit"
        ),
    )
    query_token_budget: int = Field(default=256, ge=32, le=1_024)
    batch_size: int = Field(default=8, ge=1, le=32)
    device: str = Field(default="auto", min_length=1, max_length=50)
    local_files_only: bool = False
    instruction: str = Field(min_length=20, max_length=300)

    @model_validator(mode="after")
    def validate_budgets(self) -> "RerankModelConfig":
        if self.query_token_budget >= self.input_token_limit:
            raise ValueError("rerank query budget must be below input token limit")
        return self


class RerankConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    rerank_version: str = Field(pattern=r"^v\d+$")
    prompt_version: str = Field(pattern=r"^v\d+$")
    maximum_candidates: int = Field(default=40, ge=1, le=100)
    top_k: int = Field(default=12, ge=10, le=15)
    timeout_seconds: float = Field(default=30, gt=0, le=120)
    maximum_concurrency: int = Field(default=1, ge=1, le=8)
    model: RerankModelConfig

    @model_validator(mode="after")
    def validate_limits(self) -> "RerankConfig":
        if self.top_k > self.maximum_candidates:
            raise ValueError("rerank top-k cannot exceed the candidate pool")
        return self


@lru_cache
def load_rerank_config(
    path: Path = DEFAULT_RERANK_CONFIG_PATH,
) -> RerankConfig:
    try:
        payload = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as exc:
        raise ValueError(f"rerank_config_invalid:{path}") from exc
    if not isinstance(payload, dict):
        raise ValueError(f"rerank_config_invalid:{path}")
    return RerankConfig.model_validate(payload)
