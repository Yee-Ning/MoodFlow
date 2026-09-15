"""Versioned, framework-neutral contracts for the complete RAG evaluation path."""

from __future__ import annotations

import math
from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, HttpUrl, model_validator


class AnnotationStatus(StrEnum):
    """Whether a human has reviewed all labels needed by the case."""

    PENDING = "pending"
    APPROVED = "approved"


class ExpectedOutcome(StrEnum):
    ANSWERABLE = "answerable"
    NEEDS_INPUT = "needs_input"
    INSUFFICIENT_EVIDENCE = "insufficient_evidence"


class SupportingSpanAnnotation(BaseModel):
    """An exact human-reviewed evidence span from one relevant Parent."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    parent_id: str = Field(min_length=1, max_length=128)
    exact_text: str = Field(min_length=1, max_length=4_000)
    source_id: str | None = Field(default=None, min_length=1, max_length=300)


class RAGEvaluationCase(BaseModel):
    """One human-reviewable case in the versioned RAG evaluation set."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal["v0"] = "v0"
    case_id: str = Field(min_length=1, max_length=100)
    query: str = Field(min_length=1, max_length=2_000)
    expected_outcome: ExpectedOutcome
    annotation_status: AnnotationStatus = AnnotationStatus.PENDING
    current_song_id: int | None = Field(default=None, gt=0)
    expected_entity_ids: list[str] = Field(default_factory=list, max_length=20)
    expected_query_types: list[str] = Field(default_factory=list, max_length=10)
    relevant_parent_ids: list[str] = Field(default_factory=list, max_length=100)
    supporting_spans: list[SupportingSpanAnnotation] = Field(
        default_factory=list,
        max_length=100,
    )
    required_facts: list[str] = Field(default_factory=list, max_length=50)
    forbidden_claims: list[str] = Field(default_factory=list, max_length=50)
    expected_source_ids: list[str] = Field(default_factory=list, max_length=50)
    reference_answer: str | None = Field(default=None, max_length=8_000)
    tags: list[str] = Field(default_factory=list, max_length=20)
    notes: str | None = Field(default=None, max_length=2_000)

    @model_validator(mode="after")
    def validate_labels(self) -> "RAGEvaluationCase":
        for field_name in (
            "expected_entity_ids",
            "expected_query_types",
            "relevant_parent_ids",
            "required_facts",
            "forbidden_claims",
            "expected_source_ids",
            "tags",
        ):
            values = getattr(self, field_name)
            if len(values) != len(set(values)):
                raise ValueError(f"{field_name} must be unique")
        span_keys = [(item.parent_id, item.exact_text) for item in self.supporting_spans]
        if len(span_keys) != len(set(span_keys)):
            raise ValueError("supporting spans must be unique")
        if self.annotation_status is AnnotationStatus.PENDING:
            return self
        if self.expected_outcome is ExpectedOutcome.ANSWERABLE:
            if not self.relevant_parent_ids:
                raise ValueError("approved answerable case requires relevant parents")
            if not self.supporting_spans:
                raise ValueError("approved answerable case requires supporting spans")
            if not self.required_facts:
                raise ValueError("approved answerable case requires required facts")
            if not self.reference_answer:
                raise ValueError("approved answerable case requires a reference answer")
            unknown_span_parents = {
                item.parent_id for item in self.supporting_spans
            }.difference(self.relevant_parent_ids)
            if unknown_span_parents:
                raise ValueError("supporting spans must reference relevant parents")
        elif self.expected_outcome is ExpectedOutcome.NEEDS_INPUT:
            if any(
                (
                    self.relevant_parent_ids,
                    self.supporting_spans,
                    self.required_facts,
                    self.expected_source_ids,
                    self.reference_answer,
                )
            ):
                raise ValueError("needs-input case cannot contain answer evidence labels")
        return self


class EvaluationContext(BaseModel):
    """One inspectable context item at a retrieval, rerank, or packing boundary."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    parent_id: str = Field(min_length=1, max_length=128)
    rank: int = Field(ge=1, le=200)
    title: str = Field(min_length=1, max_length=500)
    content: str = Field(min_length=1, max_length=20_000)
    source_id: str = Field(min_length=1, max_length=300)
    source_name: str = Field(min_length=1, max_length=500)
    source_url: HttpUrl | None = None
    matched_child_id: str | None = Field(default=None, min_length=1, max_length=128)
    token_count: int = Field(default=0, ge=0)


class TraceStageStatuses(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    query_understanding: str = Field(min_length=1, max_length=100)
    retrieval: str = Field(default="not_run", min_length=1, max_length=100)
    rerank: str = Field(default="not_run", min_length=1, max_length=100)
    context: str = Field(default="not_run", min_length=1, max_length=100)
    generation: str = Field(default="not_run", min_length=1, max_length=100)


class EvaluationVersions(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    index_release: str | None = Field(default=None, max_length=200)
    index_schema: str | None = Field(default=None, max_length=100)
    query_analysis: str | None = Field(default=None, max_length=100)
    retrieval: str | None = Field(default=None, max_length=100)
    rerank: str | None = Field(default=None, max_length=100)
    rerank_prompt: str | None = Field(default=None, max_length=100)
    context: str | None = Field(default=None, max_length=100)
    generation_prompt: str | None = Field(default=None, max_length=100)
    generation_model: str | None = Field(default=None, max_length=300)


class RAGEvaluationTrace(BaseModel):
    """A replayable snapshot of one case across Top40 -> Top12 -> final context."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal["v0"] = "v0"
    run_id: str = Field(min_length=1, max_length=128)
    case_id: str = Field(min_length=1, max_length=100)
    query: str = Field(min_length=1, max_length=2_000)
    standalone_query: str | None = Field(default=None, max_length=4_000)
    statuses: TraceStageStatuses
    candidate_pool: list[EvaluationContext] = Field(default_factory=list, max_length=40)
    reranked_context: list[EvaluationContext] = Field(
        default_factory=list,
        max_length=12,
    )
    final_context: list[EvaluationContext] = Field(default_factory=list, max_length=10)
    actual_answer: str | None = Field(default=None, max_length=12_000)
    citation_parent_ids: list[str] = Field(default_factory=list, max_length=30)
    fallback_used: bool = False
    context_token_count: int | None = Field(default=None, ge=0)
    context_token_budget: int | None = Field(default=None, gt=0)
    timings_ms: dict[str, float] = Field(default_factory=dict, max_length=30)
    versions: EvaluationVersions = Field(default_factory=EvaluationVersions)
    warnings: list[str] = Field(default_factory=list, max_length=50)

    @model_validator(mode="after")
    def validate_trace(self) -> "RAGEvaluationTrace":
        candidate_ids = _validate_context_stage("candidate_pool", self.candidate_pool)
        reranked_ids = _validate_context_stage("reranked_context", self.reranked_context)
        final_ids = _validate_context_stage("final_context", self.final_context)
        if not reranked_ids.issubset(candidate_ids):
            raise ValueError("reranked context must be a subset of the candidate pool")
        final_parent_set = reranked_ids if reranked_ids else candidate_ids
        if not final_ids.issubset(final_parent_set):
            raise ValueError("final context must come from the preceding ranked stage")
        if len(self.citation_parent_ids) != len(set(self.citation_parent_ids)):
            raise ValueError("citation parent ids must be unique")
        if not set(self.citation_parent_ids).issubset(final_ids):
            raise ValueError("citations must reference final context parents")
        if (self.context_token_count is None) != (self.context_token_budget is None):
            raise ValueError("context token count and budget must be recorded together")
        for name, value in self.timings_ms.items():
            if not name.strip() or not math.isfinite(value) or value < 0:
                raise ValueError("trace timings must have names and finite nonnegative values")
        return self


class DeepEvalCasePayload(BaseModel):
    """Dependency-free interchange format consumed by the isolated Judge runner."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal["v0"] = "v0"
    case_id: str = Field(min_length=1, max_length=100)
    input: str = Field(min_length=1, max_length=2_000)
    actual_output: str = Field(min_length=1, max_length=12_000)
    expected_output: str = Field(min_length=1, max_length=8_000)
    retrieval_context: list[str] = Field(min_length=1, max_length=10)
    reference_context: list[str] = Field(min_length=1, max_length=100)
    tags: list[str] = Field(default_factory=list, max_length=20)
    metadata: dict[str, str | int | float | bool | None] = Field(default_factory=dict)


def _validate_context_stage(
    stage_name: str,
    contexts: list[EvaluationContext],
) -> set[str]:
    parent_ids = [item.parent_id for item in contexts]
    if len(parent_ids) != len(set(parent_ids)):
        raise ValueError(f"{stage_name} parents must be unique")
    ranks = [item.rank for item in contexts]
    if ranks != list(range(1, len(ranks) + 1)):
        raise ValueError(f"{stage_name} ranks must be contiguous and ordered")
    return set(parent_ids)
