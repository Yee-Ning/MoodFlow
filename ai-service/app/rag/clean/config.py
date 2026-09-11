"""Versioned configuration contracts for Clean v0.1."""

from __future__ import annotations

import hashlib
import re
from pathlib import Path

import yaml
from pydantic import BaseModel, ConfigDict, Field, model_validator

from .document_types import DocumentTypeSidecar
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


class ReportingConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    before_after_sample_size: int = Field(default=20, ge=1, le=1_000)
    max_sample_text_chars: int = Field(default=4_000, ge=200, le=100_000)


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
    document_type_sidecar_path: str | None = None
    document_type_sidecar: DocumentTypeSidecar | None = Field(
        default=None, exclude=True
    )
    document_type_sidecar_checksum: str | None = Field(
        default=None, pattern=r"^sha256:[0-9a-f]{64}$", exclude=True
    )
    known_sources: list[str] = Field(default_factory=list)
    known_document_types: list[str] = Field(default_factory=list)
    profiling: ProfilingConfig
    sampling: SamplingConfig
    noise_discovery: NoiseDiscoveryConfig
    semantic_guard: SemanticGuardConfig
    reporting: ReportingConfig
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
        if self.document_type_sidecar_path is None:
            if self.document_type_sidecar is not None:
                raise ValueError("document type sidecar payload requires a path")
            if self.document_type_sidecar_checksum is not None:
                raise ValueError("document type sidecar checksum requires a path")
        else:
            if self.document_type_sidecar is None:
                raise ValueError("document type sidecar path requires a payload")
            if self.document_type_sidecar_checksum is None:
                raise ValueError("document type sidecar path requires a checksum")
            if self.document_type_sidecar.sidecar_version != self.clean_version:
                raise ValueError(
                    "document type sidecar version must equal clean_version"
                )
            approved_types = {
                assignment.document_type
                for assignment in (
                    self.document_type_sidecar.approved_assignments().values()
                )
            }
            unknown_types = approved_types - set(self.known_document_types)
            if unknown_types:
                raise ValueError(
                    "approved sidecar document types must be known:"
                    f"{sorted(unknown_types)}"
                )
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
        if (
            "document_type_sidecar" in payload
            or "document_type_sidecar_checksum" in payload
        ):
            raise ValueError("document type sidecar runtime fields are reserved")
        sidecar_raw = b""
        sidecar_path_value = payload.get("document_type_sidecar_path")
        if sidecar_path_value is not None:
            if (
                not isinstance(sidecar_path_value, str)
                or not sidecar_path_value.strip()
            ):
                raise ValueError(
                    "document_type_sidecar_path must be a non-empty string"
                )
            relative_sidecar = Path(sidecar_path_value)
            if relative_sidecar.is_absolute():
                raise ValueError("document_type_sidecar_path must be relative")
            sidecar_path = (selected.parent / relative_sidecar).resolve()
            if not sidecar_path.is_relative_to(selected.parent):
                raise ValueError("document_type_sidecar_path escapes config directory")
            sidecar_raw = sidecar_path.read_bytes()
            sidecar_payload = yaml.safe_load(sidecar_raw)
            if not isinstance(sidecar_payload, dict):
                raise ValueError("document type sidecar root must be an object")
            payload["document_type_sidecar"] = sidecar_payload
            payload["document_type_sidecar_checksum"] = (
                "sha256:" + hashlib.sha256(sidecar_raw).hexdigest()
            )
        config = CleanPipelineConfig.model_validate(payload)
    except (OSError, ValueError, yaml.YAMLError) as exc:
        raise CleanConfigError(f"clean_config_invalid:{selected}") from exc
    fingerprint = hashlib.sha256()
    fingerprint.update(raw)
    if config.document_type_sidecar_path is not None:
        fingerprint.update(b"\0document_type_sidecar\0")
        fingerprint.update(config.document_type_sidecar_path.encode("utf-8"))
        fingerprint.update(b"\0")
        fingerprint.update(sidecar_raw)
    return config, "sha256:" + fingerprint.hexdigest()
