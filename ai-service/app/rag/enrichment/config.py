"""Configuration for Metadata Enrichment V1."""

from __future__ import annotations

from pathlib import Path

import yaml
from pydantic import BaseModel, ConfigDict, Field


DEFAULT_ENRICHMENT_CONFIG_PATH = Path(__file__).with_name("config") / "v1.yaml"


class EnrichmentConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    enrichment_version: str = Field(default="v1", pattern=r"^v\d+$")
    schema_version: str = Field(default="v1", min_length=1, max_length=50)
    prompt_version: str = Field(default="v1", min_length=1, max_length=50)
    llm_enabled: bool = True
    batch_size: int = Field(default=16, ge=1, le=200)
    max_concurrency: int = Field(default=4, ge=1, le=32)
    timeout_seconds: float = Field(default=45, gt=0, le=300)
    retry_count: int = Field(default=2, ge=0, le=5)


def load_enrichment_config(path: Path | None = None) -> EnrichmentConfig:
    selected = (path or DEFAULT_ENRICHMENT_CONFIG_PATH).resolve()
    try:
        payload = yaml.safe_load(selected.read_text(encoding="utf-8"))
        if not isinstance(payload, dict):
            raise ValueError("enrichment config root must be an object")
        return EnrichmentConfig.model_validate(payload)
    except (OSError, ValueError, yaml.YAMLError) as exc:
        raise ValueError(f"enrichment_config_invalid:{selected}") from exc
