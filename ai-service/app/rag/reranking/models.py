"""Strict contracts for the reranking stage."""

from __future__ import annotations

import math
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.rag.query_understanding.models import IndexProfile
from app.rag.retrieval.models import RetrievalCandidate


class RerankStatus(StrEnum):
    SUCCESS = "success"
    DEGRADED = "degraded"
    EMPTY = "empty"
    UNAVAILABLE = "unavailable"


class RerankInput(BaseModel):
    """One query/primary-Child pair with future multi-query trace fields."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    candidate_index: int = Field(ge=0, le=39)
    parent_id: str = Field(min_length=1, max_length=128)
    query: str = Field(min_length=1, max_length=2_000)
    document: str = Field(min_length=1, max_length=12_000)
    subquery_ids: list[str] = Field(min_length=1, max_length=20)
    retrieval_legs: list[str] = Field(min_length=1, max_length=20)


class RerankedCandidate(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    candidate: RetrievalCandidate
    retrieval_rank: int = Field(ge=1, le=40)
    rerank_rank: int = Field(ge=1, le=15)
    rerank_score: float = Field(ge=0, le=1)

    @model_validator(mode="after")
    def validate_score(self) -> "RerankedCandidate":
        if not math.isfinite(self.rerank_score):
            raise ValueError("rerank score must be finite")
        return self


class RerankTimings(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    inference_ms: float = Field(default=0, ge=0)
    total_ms: float = Field(default=0, ge=0)


class RerankResult(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    status: RerankStatus
    plan_id: str = Field(pattern=r"^query-plan:[0-9a-f]{24}$")
    index_profile: IndexProfile
    model_name: str = Field(min_length=1, max_length=300)
    model_revision: str = Field(pattern=r"^[0-9a-f]{40}$")
    prompt_version: str = Field(pattern=r"^v\d+$")
    input_count: int = Field(ge=0, le=40)
    output_top_k: int = Field(ge=10, le=15)
    candidates: list[RerankedCandidate] = Field(default_factory=list, max_length=15)
    fallback_used: bool = False
    warnings: list[str] = Field(default_factory=list, max_length=50)
    timings: RerankTimings = Field(default_factory=RerankTimings)

    @model_validator(mode="after")
    def validate_result(self) -> "RerankResult":
        if len(self.candidates) > min(self.input_count, self.output_top_k):
            raise ValueError("rerank output exceeds its bounded input")
        if self.status is RerankStatus.SUCCESS and not self.candidates:
            raise ValueError("successful reranking requires candidates")
        if self.status is RerankStatus.EMPTY:
            if self.input_count != 0 or self.candidates:
                raise ValueError("empty reranking cannot contain candidates")
        if self.status is RerankStatus.UNAVAILABLE and self.candidates:
            raise ValueError("unavailable reranking cannot contain candidates")
        if self.status is RerankStatus.DEGRADED and not self.fallback_used:
            raise ValueError("degraded reranking must identify fallback use")
        if self.status is RerankStatus.SUCCESS and self.fallback_used:
            raise ValueError("successful reranking cannot use fallback")
        parent_ids = [item.candidate.parent_id for item in self.candidates]
        if len(parent_ids) != len(set(parent_ids)):
            raise ValueError("reranked candidates must be unique by parent")
        ranks = [item.rerank_rank for item in self.candidates]
        if ranks != list(range(1, len(ranks) + 1)):
            raise ValueError("rerank ranks must be contiguous and ordered")
        retrieval_ranks = [item.retrieval_rank for item in self.candidates]
        if len(retrieval_ranks) != len(set(retrieval_ranks)):
            raise ValueError("retrieval ranks must be unique")
        return self
