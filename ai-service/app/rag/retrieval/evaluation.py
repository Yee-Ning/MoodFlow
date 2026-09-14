"""Deterministic offline metrics for the pre-rerank candidate pool."""

from __future__ import annotations

import json
import math
from pathlib import Path
from typing import Sequence

from pydantic import BaseModel, ConfigDict, Field, model_validator


class RetrievalEvaluationCase(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    case_id: str = Field(min_length=1, max_length=100)
    query: str = Field(min_length=1, max_length=2_000)
    relevant_parent_ids: list[str] = Field(min_length=1, max_length=100)
    current_song_id: int | None = Field(default=None, gt=0)
    tags: list[str] = Field(default_factory=list, max_length=20)

    @model_validator(mode="after")
    def validate_case(self) -> "RetrievalEvaluationCase":
        if len(self.relevant_parent_ids) != len(set(self.relevant_parent_ids)):
            raise ValueError("relevant parent ids must be unique")
        if len(self.tags) != len(set(self.tags)):
            raise ValueError("evaluation tags must be unique")
        return self


class RetrievalCaseMetrics(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    case_id: str
    retrieved_count: int = Field(ge=0)
    recall_at_20: float = Field(ge=0, le=1)
    recall_at_40: float = Field(ge=0, le=1)
    reciprocal_rank_at_10: float = Field(ge=0, le=1)
    ndcg_at_10: float = Field(ge=0, le=1)


class RetrievalEvaluationReport(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    case_count: int = Field(ge=1)
    recall_at_20: float = Field(ge=0, le=1)
    recall_at_40: float = Field(ge=0, le=1)
    mrr_at_10: float = Field(ge=0, le=1)
    ndcg_at_10: float = Field(ge=0, le=1)
    cases: list[RetrievalCaseMetrics] = Field(min_length=1)


def load_evaluation_cases(path: Path) -> list[RetrievalEvaluationCase]:
    cases: list[RetrievalEvaluationCase] = []
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError as exc:
        raise ValueError(f"retrieval_evaluation_dataset_unreadable:{path}") from exc
    for line_number, raw_line in enumerate(lines, start=1):
        if not raw_line.strip():
            continue
        try:
            payload = json.loads(raw_line)
            cases.append(RetrievalEvaluationCase.model_validate(payload))
        except (ValueError, json.JSONDecodeError) as exc:
            raise ValueError(
                f"retrieval_evaluation_case_invalid:{path}:{line_number}"
            ) from exc
    if not cases:
        raise ValueError(f"retrieval_evaluation_dataset_empty:{path}")
    case_ids = [item.case_id for item in cases]
    if len(case_ids) != len(set(case_ids)):
        raise ValueError(f"retrieval_evaluation_case_id_duplicate:{path}")
    return cases


def evaluate_candidate_pool(
    cases: Sequence[RetrievalEvaluationCase],
    rankings: dict[str, Sequence[str]],
) -> RetrievalEvaluationReport:
    if not cases:
        raise ValueError("retrieval evaluation requires at least one case")
    scores = [
        _score_case(case, _unique_ranking(rankings.get(case.case_id, [])))
        for case in cases
    ]
    count = len(scores)
    return RetrievalEvaluationReport(
        case_count=count,
        recall_at_20=sum(item.recall_at_20 for item in scores) / count,
        recall_at_40=sum(item.recall_at_40 for item in scores) / count,
        mrr_at_10=sum(item.reciprocal_rank_at_10 for item in scores) / count,
        ndcg_at_10=sum(item.ndcg_at_10 for item in scores) / count,
        cases=scores,
    )


def _score_case(
    case: RetrievalEvaluationCase,
    ranking: Sequence[str],
) -> RetrievalCaseMetrics:
    relevant = set(case.relevant_parent_ids)
    return RetrievalCaseMetrics(
        case_id=case.case_id,
        retrieved_count=len(ranking),
        recall_at_20=_recall_at_k(ranking, relevant, 20),
        recall_at_40=_recall_at_k(ranking, relevant, 40),
        reciprocal_rank_at_10=_reciprocal_rank_at_k(ranking, relevant, 10),
        ndcg_at_10=_ndcg_at_k(ranking, relevant, 10),
    )


def _unique_ranking(ranking: Sequence[str]) -> list[str]:
    return list(dict.fromkeys(ranking))


def _recall_at_k(ranking: Sequence[str], relevant: set[str], k: int) -> float:
    hits = len(relevant.intersection(ranking[:k]))
    return hits / len(relevant)


def _reciprocal_rank_at_k(
    ranking: Sequence[str], relevant: set[str], k: int
) -> float:
    for rank, parent_id in enumerate(ranking[:k], start=1):
        if parent_id in relevant:
            return 1 / rank
    return 0


def _ndcg_at_k(ranking: Sequence[str], relevant: set[str], k: int) -> float:
    dcg = sum(
        1 / math.log2(rank + 1)
        for rank, parent_id in enumerate(ranking[:k], start=1)
        if parent_id in relevant
    )
    ideal_hits = min(len(relevant), k)
    ideal = sum(1 / math.log2(rank + 1) for rank in range(1, ideal_hits + 1))
    return dcg / ideal
