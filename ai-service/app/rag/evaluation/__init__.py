"""Framework-neutral contracts and services for offline RAG evaluation."""

from app.rag.evaluation.io import (
    load_evaluation_cases,
    load_evaluation_traces,
    write_jsonl,
)
from app.rag.evaluation.models import (
    AnnotationStatus,
    DeepEvalCasePayload,
    EvaluationContext,
    EvaluationVersions,
    ExpectedOutcome,
    RAGEvaluationCase,
    RAGEvaluationTrace,
    SupportingSpanAnnotation,
    TraceStageStatuses,
)

__all__ = [
    "AnnotationStatus",
    "DeepEvalCasePayload",
    "EvaluationContext",
    "EvaluationVersions",
    "ExpectedOutcome",
    "RAGEvaluationCase",
    "RAGEvaluationTrace",
    "SupportingSpanAnnotation",
    "TraceStageStatuses",
    "load_evaluation_cases",
    "load_evaluation_traces",
    "write_jsonl",
]
