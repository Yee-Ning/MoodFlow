"""Versioned policy configuration for query understanding."""

from functools import lru_cache
from pathlib import Path

import yaml
from pydantic import BaseModel, ConfigDict, Field


DEFAULT_QUERY_UNDERSTANDING_CONFIG_PATH = (
    Path(__file__).with_name("config") / "v1.yaml"
)


class QueryUnderstandingConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    analysis_version: str = Field(pattern=r"^v\d+$")
    prompt_version: str = Field(pattern=r"^v\d+$")
    max_query_variants: int = Field(default=3, ge=1, le=10)
    candidate_top_k: int = Field(default=40, ge=1, le=200)
    final_top_k: int = Field(default=10, ge=1, le=50)
    enable_hyde: bool = False
    llm_fallback_enabled: bool = True
    llm_confidence_threshold: float = Field(default=0.72, ge=0, le=1)


@lru_cache
def load_query_understanding_config(
    path: Path = DEFAULT_QUERY_UNDERSTANDING_CONFIG_PATH,
) -> QueryUnderstandingConfig:
    try:
        payload = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as exc:
        raise ValueError(f"query_understanding_config_invalid:{path}") from exc
    if not isinstance(payload, dict):
        raise ValueError(f"query_understanding_config_invalid:{path}")
    return QueryUnderstandingConfig.model_validate(payload)
