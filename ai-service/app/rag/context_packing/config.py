"""Versioned configuration for the post-rerank context pipeline."""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

import yaml
from pydantic import BaseModel, ConfigDict, Field, model_validator


DEFAULT_CONTEXT_PACKING_CONFIG_PATH = Path(__file__).with_name("config") / "v1.yaml"


class SufficiencyGateConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    require_decomposed_query_coverage: bool = True
    require_comparison_entity_coverage: bool = True


class EvidenceCompressionConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    max_evidence_tokens: int = Field(default=400, ge=50, le=2_000)
    max_item_tokens: int = Field(default=1_200, ge=100, le=8_000)
    neighbor_window: int = Field(default=1, ge=0, le=3)


class ContextSelectionConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    max_items: int = Field(default=8, ge=1, le=10)
    token_budget: int = Field(default=6_000, ge=500, le=30_000)
    source_cap: int = Field(default=3, ge=1, le=10)
    near_duplicate_threshold: float = Field(default=0.92, ge=0.8, le=1)


class ContextPackingConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    context_version: str = Field(pattern=r"^v\d+$")
    gate: SufficiencyGateConfig = Field(default_factory=SufficiencyGateConfig)
    compression: EvidenceCompressionConfig = Field(
        default_factory=EvidenceCompressionConfig
    )
    selection: ContextSelectionConfig = Field(default_factory=ContextSelectionConfig)

    @model_validator(mode="after")
    def validate_budgets(self) -> "ContextPackingConfig":
        if self.compression.max_item_tokens > self.selection.token_budget:
            raise ValueError("context item budget cannot exceed total token budget")
        return self


@lru_cache
def load_context_packing_config(
    path: Path = DEFAULT_CONTEXT_PACKING_CONFIG_PATH,
) -> ContextPackingConfig:
    try:
        payload = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as exc:
        raise ValueError(f"context_packing_config_invalid:{path}") from exc
    if not isinstance(payload, dict):
        raise ValueError(f"context_packing_config_invalid:{path}")
    return ContextPackingConfig.model_validate(payload)
