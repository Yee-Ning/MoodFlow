"""Versioned configuration contracts for Clean v0.1."""

from __future__ import annotations

import hashlib
import re
from pathlib import Path

import yaml
from pydantic import BaseModel, ConfigDict, Field, model_validator

from .errors import CleanConfigError


DEFAULT_CLEAN_CONFIG_PATH = Path(__file__).with_name("rules") / "v0.1.yaml"


class ProfilingConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    short_document_chars: int = Field(default=300, ge=0)
    frequent_line_min_documents: int = Field(default=3, ge=2)
    frequent_short_text_min_documents: int = Field(default=3, ge=2)
    frequent_short_text_max_chars: int = Field(default=80, ge=1)
    prefix_suffix_line_count: int = Field(default=1, ge=1, le=5)
    repeated_sequence_lines: int = Field(default=2, ge=2, le=5)
    repeated_sequence_min_documents: int = Field(default=3, ge=2)
    top_k: int = Field(default=100, ge=1, le=1_000)
    max_example_chars: int = Field(default=500, ge=40, le=10_000)


class LengthBucket(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    name: str = Field(min_length=1, max_length=50)
    max_chars: int | None = Field(default=None, ge=0)


class SamplingConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    seed: int = 104_729
    sample_size: int = Field(default=18, ge=1)
    max_text_chars_per_document: int = Field(default=12_000, ge=200)
    length_buckets: list[LengthBucket] = Field(min_length=1)

    @model_validator(mode="after")
    def validate_buckets(self) -> "SamplingConfig":
        names = [bucket.name for bucket in self.length_buckets]
        if len(names) != len(set(names)):
            raise ValueError("sampling length bucket names must be unique")
        bounded = [
            bucket.max_chars
            for bucket in self.length_buckets
            if bucket.max_chars is not None
        ]
        if bounded != sorted(bounded) or len(bounded) != len(set(bounded)):
            raise ValueError("sampling length bucket bounds must be unique and ascending")
        if self.length_buckets[-1].max_chars is not None:
            raise ValueError("the last sampling length bucket must be unbounded")
        if any(bucket.max_chars is None for bucket in self.length_buckets[:-1]):
            raise ValueError("only the last sampling length bucket may be unbounded")
        return self


class NoiseDiscoveryConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    prompt_version: str = Field(min_length=1, max_length=50)
    batch_size: int = Field(default=4, ge=1, le=20)
    max_candidates_per_batch: int = Field(default=12, ge=1, le=100)


class SemanticGuardConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    removed_ratio_warning: float = Field(default=0.35, ge=0, le=1)
    large_drop_ratio: float = Field(default=0.2, ge=0, le=1)
    large_drop_min_chars: int = Field(default=1_000, ge=0)
    fact_loss_warning_ratio: float = Field(default=0.25, ge=0, le=1)
    min_numeric_facts: int = Field(default=3, ge=1)
    min_structured_facts: int = Field(default=2, ge=1)
    numeric_pattern: str = Field(min_length=1)
    date_pattern: str = Field(min_length=1)
    structured_fact_patterns: list[str] = Field(min_length=1)

    @model_validator(mode="after")
    def validate_patterns(self) -> "SemanticGuardConfig":
        for name, pattern in (
            ("numeric_pattern", self.numeric_pattern),
            ("date_pattern", self.date_pattern),
        ):
            try:
                re.compile(pattern)
            except re.error as exc:
                raise ValueError(f"invalid {name}: {exc}") from exc
        for index, pattern in enumerate(self.structured_fact_patterns):
            try:
                re.compile(pattern)
            except re.error as exc:
                raise ValueError(
                    f"invalid structured_fact_patterns[{index}]: {exc}"
                ) from exc
        return self


class CleanRuleConfig(BaseModel):
    """Loose action values are validated by RuleRegistry at its boundary."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    id: str = Field(pattern=r"^[A-Z0-9][A-Z0-9_-]{2,99}$")
    name: str = Field(min_length=1, max_length=200)
    description: str = Field(min_length=1, max_length=1_000)
    scope: dict[str, object]
    action: str = Field(min_length=1, max_length=50)
    pattern: str = Field(min_length=1)
    replacement: str | None = None
    flags: list[str] = Field(default_factory=list)
    priority: int = Field(ge=0, le=100_000)
    enabled: bool = True
    safe: bool = False
    reason: str = Field(min_length=1, max_length=1_000)
    version: str = Field(min_length=1, max_length=50)


class CleanPipelineConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    clean_version: str = Field(pattern=r"^v\d+\.\d+$")
    known_sources: list[str] = Field(default_factory=list)
    known_document_types: list[str] = Field(default_factory=list)
    profiling: ProfilingConfig
    sampling: SamplingConfig
    noise_discovery: NoiseDiscoveryConfig
    semantic_guard: SemanticGuardConfig
    rules: list[CleanRuleConfig] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_registry_identity(self) -> "CleanPipelineConfig":
        if len(self.known_sources) != len(set(self.known_sources)):
            raise ValueError("known_sources must be unique")
        if len(self.known_document_types) != len(set(self.known_document_types)):
            raise ValueError("known_document_types must be unique")
        rule_ids = [rule.id for rule in self.rules]
        if len(rule_ids) != len(set(rule_ids)):
            raise ValueError("clean rule IDs must be unique")
        if any(rule.version != self.clean_version for rule in self.rules):
            raise ValueError("every rule version must equal clean_version")
        return self


def load_clean_config(
    path: Path | None = None,
) -> tuple[CleanPipelineConfig, str]:
    """Load one immutable registry file and return its content checksum."""

    selected = (path or DEFAULT_CLEAN_CONFIG_PATH).resolve()
    try:
        raw = selected.read_bytes()
        payload = yaml.safe_load(raw)
        if not isinstance(payload, dict):
            raise ValueError("clean config root must be an object")
        config = CleanPipelineConfig.model_validate(payload)
    except (OSError, ValueError, yaml.YAMLError) as exc:
        raise CleanConfigError(f"clean_config_invalid:{selected}") from exc
    return config, "sha256:" + hashlib.sha256(raw).hexdigest()
