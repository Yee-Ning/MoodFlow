"""Framework-neutral contracts and services for offline RAG evaluation."""

from app.rag.evaluation.io import (
    load_evaluation_cases,
    load_evaluation_traces,
    write_jsonl,
)
from app.rag.evaluation.config import EvaluationConfig, load_evaluation_config
from app.rag.evaluation.dataset import build_pending_dataset
from app.rag.evaluation.deepeval_adapter import build_deepeval_payloads
from app.rag.evaluation.generation import (
    CitationAwareAnswer,
    CitationAwareAnswerGenerator,
    CitationAwareGenerationRuntime,
    generate_evaluation_answers,
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
from app.rag.evaluation.service import (
    DeterministicEvaluationReport,
    evaluate_traces,
)
from app.rag.evaluation.silver import bootstrap_silver_dataset
from app.rag.evaluation.collector import collect_evaluation_traces
from app.rag.evaluation.review import build_annotation_review_markdown
from app.rag.evaluation.trace_builder import build_evaluation_trace

__all__ = [
    "AnnotationStatus",
    "CitationAwareAnswer",
    "CitationAwareAnswerGenerator",
    "CitationAwareGenerationRuntime",
    "DeepEvalCasePayload",
    "DeterministicEvaluationReport",
    "EvaluationContext",
    "EvaluationConfig",
    "EvaluationVersions",
    "ExpectedOutcome",
    "RAGEvaluationCase",
    "RAGEvaluationTrace",
    "SupportingSpanAnnotation",
    "TraceStageStatuses",
    "build_evaluation_trace",
    "build_deepeval_payloads",
    "build_annotation_review_markdown",
    "build_pending_dataset",
    "bootstrap_silver_dataset",
    "collect_evaluation_traces",
    "evaluate_traces",
    "generate_evaluation_answers",
    "load_evaluation_cases",
    "load_evaluation_traces",
    "load_evaluation_config",
    "write_jsonl",
]
