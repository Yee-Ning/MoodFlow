"""Configuration contracts for the offline Chunk V1 pipeline."""

from __future__ import annotations

import hashlib
import json
from enum import StrEnum
from pathlib import Path

import yaml
from pydantic import BaseModel, ConfigDict, Field, JsonValue, model_validator


DEFAULT_CHUNK_CONFIG_PATH = Path(__file__).with_name("config") / "v1.yaml"


class TokenCounterBackend(StrEnum):
    UNICODE_HEURISTIC = "unicode_heuristic"
    HUGGINGFACE = "huggingface"


class TokenCounterConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    backend: TokenCounterBackend = TokenCounterBackend.UNICODE_HEURISTIC
    tokenizer_name_or_path: str | None = Field(default=None, min_length=1)
    add_special_tokens: bool = False
    local_files_only: bool = False
    tokenizer_kwargs: dict[str, JsonValue] = Field(default_factory=dict)

    @model_validator(mode="after")
    def validate_backend(self) -> "TokenCounterConfig":
        if (
            self.backend is TokenCounterBackend.HUGGINGFACE
            and self.tokenizer_name_or_path is None
        ):
            raise ValueError(
                "huggingface token counter requires tokenizer_name_or_path"
            )
        return self


class ChunkConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    chunk_version: str = Field(default="v1", pattern=r"^v\d+$")
    target_parent_tokens: int = Field(default=1_000, ge=1)
    max_parent_tokens: int = Field(default=1_500, ge=1)
    target_child_tokens: int = Field(default=350, ge=1)
    max_child_tokens: int = Field(default=500, ge=1)
    min_child_tokens: int = Field(default=80, ge=1)
    child_overlap_tokens: int = Field(default=50, ge=0)
    separators: tuple[str, ...] = (
        "\n\n",
        "\n",
        "。",
        "！",
        "？",
        "；",
        "：",
        "，",
        "、",
        " ",
        "",
    )
    content_joiner: str = "\n\n"
    token_counter: TokenCounterConfig = Field(default_factory=TokenCounterConfig)

    @model_validator(mode="after")
    def validate_thresholds(self) -> "ChunkConfig":
        if self.target_parent_tokens > self.max_parent_tokens:
            raise ValueError("target_parent_tokens must not exceed max_parent_tokens")
        if not (
            self.min_child_tokens <= self.target_child_tokens <= self.max_child_tokens
        ):
            raise ValueError("child token thresholds must satisfy min <= target <= max")
        if self.child_overlap_tokens >= self.target_child_tokens:
            raise ValueError("child_overlap_tokens must be smaller than target")
        if not self.separators or self.separators[-1] != "":
            raise ValueError("separators must end with the character fallback")
        if not self.content_joiner:
            raise ValueError("content_joiner must not be empty")
        return self

    def fingerprint(self) -> str:
        payload = json.dumps(
            self.model_dump(mode="json"),
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        return "sha256:" + hashlib.sha256(payload.encode("utf-8")).hexdigest()


def load_chunk_config(path: Path | None = None) -> ChunkConfig:
    selected = (path or DEFAULT_CHUNK_CONFIG_PATH).resolve()
    try:
        payload = yaml.safe_load(selected.read_text(encoding="utf-8"))
        if not isinstance(payload, dict):
            raise ValueError("chunk config root must be an object")
        return ChunkConfig.model_validate(payload)
    except (OSError, ValueError, yaml.YAMLError) as exc:
        raise ValueError(f"chunk_config_invalid:{selected}") from exc
