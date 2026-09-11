"""Auditable and deterministic Music RAG Clean pipeline."""

from .config import (
    DEFAULT_CLEAN_CONFIG_PATH,
    CleanPipelineConfig,
    ProfilingConfig,
    load_clean_config,
)
from .corpus import NormalizedCorpusReader, NormalizedDocumentContext
from .document_types import (
    DocumentTypeAssignment,
    DocumentTypeOrigin,
    DocumentTypeResolver,
    DocumentTypeReviewStatus,
    DocumentTypeSidecar,
)
from .engine import CleanEngine
from .guard import SemanticGuard
from .models import CleanDocumentAudit, CleanedDocument, CleanExecutionResult
from .profiling import NormalizedCorpusProfiler, write_profile_report
from .registry import RuleAction, RuleRegistry, RuleScopeType
from .sampling import SamplingManifest, StratifiedSampler, write_sampling_manifest
from .service import CleanPipelineService, CleanRunOutcome

__all__ = [
    "DEFAULT_CLEAN_CONFIG_PATH",
    "CleanPipelineConfig",
    "CleanPipelineService",
    "CleanRunOutcome",
    "CleanDocumentAudit",
    "CleanedDocument",
    "CleanEngine",
    "CleanExecutionResult",
    "DocumentTypeAssignment",
    "DocumentTypeOrigin",
    "DocumentTypeResolver",
    "DocumentTypeReviewStatus",
    "DocumentTypeSidecar",
    "NormalizedCorpusProfiler",
    "NormalizedCorpusReader",
    "NormalizedDocumentContext",
    "ProfilingConfig",
    "RuleAction",
    "RuleRegistry",
    "RuleScopeType",
    "SamplingManifest",
    "StratifiedSampler",
    "SemanticGuard",
    "load_clean_config",
    "write_profile_report",
    "write_sampling_manifest",
]
