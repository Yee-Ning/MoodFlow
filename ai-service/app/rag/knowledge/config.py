"""Configuration for the offline knowledge-structuring pipeline."""

from __future__ import annotations

from pathlib import Path

import yaml
from pydantic import BaseModel, ConfigDict, Field


DEFAULT_KNOWLEDGE_CONFIG_PATH = Path(__file__).with_name("config") / "v9.yaml"


class KnowledgeConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    entity_linking_version: str = Field(default="v4", pattern=r"^v\d+$")
    claim_extraction_version: str = Field(default="v7", pattern=r"^v\d+$")
    claim_verification_version: str = Field(default="v2", pattern=r"^v\d+$")
    prompt_version: str = Field(default="v1", pattern=r"^v\d+$")
    registry_version: str = Field(default="v3", pattern=r"^v\d+$")
    llm_enabled: bool = True
    batch_size: int = Field(default=16, ge=1, le=200)
    max_concurrency: int = Field(default=4, ge=1, le=32)
    timeout_seconds: float = Field(default=45, gt=0, le=300)
    retry_count: int = Field(default=2, ge=0, le=5)


def load_knowledge_config(path: Path | None = None) -> KnowledgeConfig:
    selected = (path or DEFAULT_KNOWLEDGE_CONFIG_PATH).resolve()
    try:
        payload = yaml.safe_load(selected.read_text(encoding="utf-8"))
        if not isinstance(payload, dict):
            raise ValueError("knowledge config root must be an object")
        return KnowledgeConfig.model_validate(payload)
    except (OSError, ValueError, yaml.YAMLError) as exc:
        raise ValueError(f"knowledge_config_invalid:{selected}") from exc
