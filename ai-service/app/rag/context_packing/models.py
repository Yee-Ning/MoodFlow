"""Strict contracts for evidence sufficiency and generation context."""

from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field, HttpUrl, JsonValue, model_validator
from app.rag.source_context import RelationSourceContext


class EvidenceSufficiencyStatus(StrEnum):
    SUFFICIENT = "sufficient"
    INSUFFICIENT = "insufficient"
    UNAVAILABLE = "unavailable"


class EvidenceSufficiency(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    status: EvidenceSufficiencyStatus
    required_subquery_ids: list[str] = Field(default_factory=list, max_length=10)
    covered_subquery_ids: list[str] = Field(default_factory=list, max_length=10)
    missing_subquery_ids: list[str] = Field(default_factory=list, max_length=10)
    required_entity_ids: list[str] = Field(default_factory=list, max_length=20)
    covered_entity_ids: list[str] = Field(default_factory=list, max_length=20)
    missing_entity_ids: list[str] = Field(default_factory=list, max_length=20)
    reasons: list[str] = Field(default_factory=list, max_length=20)

    @model_validator(mode="after")
    def validate_coverage(self) -> "EvidenceSufficiency":
        for field_name in (
            "required_subquery_ids",
            "covered_subquery_ids",
            "missing_subquery_ids",
            "required_entity_ids",
            "covered_entity_ids",
            "missing_entity_ids",
            "reasons",
        ):
            values = getattr(self, field_name)
            if len(values) != len(set(values)):
                raise ValueError(f"{field_name} must be unique")
        if self.status is EvidenceSufficiencyStatus.SUFFICIENT and (
            self.missing_subquery_ids or self.missing_entity_ids
        ):
            raise ValueError("sufficient evidence cannot have missing required coverage")
        if not set(self.missing_subquery_ids).issubset(self.required_subquery_ids):
            raise ValueError("missing subqueries must be required")
        if not set(self.missing_entity_ids).issubset(self.required_entity_ids):
            raise ValueError("missing entities must be required")
        return self


class ContextEvidence(BaseModel):
    """One compressed Parent anchored by the matched Child evidence span."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    pack_rank: int = Field(ge=1, le=10)
    source_rank: int = Field(ge=1, le=40)
    parent_id: str = Field(min_length=1, max_length=128)
    child_id: str = Field(min_length=1, max_length=128)
    doc_id: str = Field(min_length=1, max_length=300)
    title: str = Field(min_length=1, max_length=500)
    exact_text: str = Field(min_length=1, max_length=4_000)
    context: str = Field(min_length=1, max_length=8_000)
    token_count: int = Field(gt=0, le=8_000)
    source_id: str = Field(min_length=1, max_length=300)
    source_name: str = Field(min_length=1, max_length=500)
    source_url: HttpUrl | None = None
    subquery_ids: list[str] = Field(min_length=1, max_length=20)
    retrieval_legs: list[str] = Field(min_length=1, max_length=20)
    entity_ids: list[str] = Field(default_factory=list, max_length=50)
    rerank_score: float | None = Field(default=None, ge=0, le=1)
    source_evidence_content: str | None = Field(default=None, min_length=1, max_length=8_000)
    relation_source_contexts: list[RelationSourceContext] = Field(default_factory=list, max_length=8)
    document_metadata: dict[str, JsonValue] = Field(default_factory=dict)
    source_provenance: dict[str, JsonValue] = Field(default_factory=dict)

    @model_validator(mode="after")
    def validate_evidence(self) -> "ContextEvidence":
        for field_name in ("subquery_ids", "retrieval_legs", "entity_ids"):
            values = getattr(self, field_name)
            if len(values) != len(set(values)):
                raise ValueError(f"{field_name} must be unique")
        if self.exact_text not in self.context:
            raise ValueError("exact evidence text must be present in compressed context")
        return self


class ContextPack(BaseModel):
    """Bounded, citation-ready input consumed by answer generation."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    context_version: str = Field(pattern=r"^v\d+$")
    plan_id: str = Field(pattern=r"^query-plan:[0-9a-f]{24}$")
    query: str = Field(min_length=1, max_length=4_000)
    sufficiency: EvidenceSufficiency
    items: list[ContextEvidence] = Field(default_factory=list, max_length=10)
    token_count: int = Field(ge=0)
    token_budget: int = Field(gt=0)
    warnings: list[str] = Field(default_factory=list, max_length=30)

    @model_validator(mode="after")
    def validate_pack(self) -> "ContextPack":
        parent_ids = [item.parent_id for item in self.items]
        if len(parent_ids) != len(set(parent_ids)):
            raise ValueError("context pack parents must be unique")
        if [item.pack_rank for item in self.items] != list(
            range(1, len(self.items) + 1)
        ):
            raise ValueError("context pack ranks must be contiguous")
        if self.token_count != sum(item.token_count for item in self.items):
            raise ValueError("context pack token count must equal its items")
        if self.token_count > self.token_budget:
            raise ValueError("context pack exceeds token budget")
        if (
            self.sufficiency.status is EvidenceSufficiencyStatus.SUFFICIENT
            and not self.items
        ):
            raise ValueError("sufficient context pack requires evidence items")
        return self
