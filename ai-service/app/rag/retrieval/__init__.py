"""Online hybrid retrieval before reranking."""

from .binding import RetrievalIndexBinding, load_retrieval_index_binding
from .config import RetrievalConfig, load_retrieval_config
from .encoder import QueryEncoder, QwenQueryEncoder
from .evaluation import (
    RetrievalCaseMetrics,
    RetrievalEvaluationCase,
    RetrievalEvaluationReport,
    evaluate_candidate_pool,
    load_evaluation_cases,
)
from .filter_compiler import compile_qdrant_filter
from .fusion import (
    FusedSearchHit,
    LegSearchResult,
    RawSearchHit,
    failed_channels,
    weighted_rrf,
)
from .legs import RetrievalLeg, build_retrieval_legs, dense_variant_indexes
from .models import (
    ChannelEvidence,
    DeferredParentContext,
    RetrievalCandidate,
    RetrievalResult,
    RetrievalStatus,
    RetrievalTimings,
)
from .selection import select_candidates
from .service import HybridRetrievalService

__all__ = [
    "ChannelEvidence",
    "DeferredParentContext",
    "FusedSearchHit",
    "HybridRetrievalService",
    "LegSearchResult",
    "RawSearchHit",
    "RetrievalIndexBinding",
    "RetrievalLeg",
    "RetrievalCandidate",
    "RetrievalConfig",
    "RetrievalCaseMetrics",
    "RetrievalEvaluationCase",
    "RetrievalEvaluationReport",
    "RetrievalResult",
    "RetrievalStatus",
    "RetrievalTimings",
    "QueryEncoder",
    "QwenQueryEncoder",
    "build_retrieval_legs",
    "compile_qdrant_filter",
    "dense_variant_indexes",
    "evaluate_candidate_pool",
    "failed_channels",
    "load_retrieval_index_binding",
    "load_retrieval_config",
    "load_evaluation_cases",
    "select_candidates",
    "weighted_rrf",
]
