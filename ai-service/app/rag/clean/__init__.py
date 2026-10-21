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
from .drift import ConceptDriftEvent, ConceptDriftMonitor
from .evaluation_hooks import (
    CleanEvaluationMetrics,
    CleanEvaluationSample,
    CleanEvaluationService,
)
from .language import LanguageDetector
from .models import (
    CleanDocumentAudit,
    CleanDocumentReport,
    CleanedDocument,
    CleanExecutionResult,
    LanguageAnnotation,
    LineDiffStats,
    StageMetrics,
)
from .profiling import NormalizedCorpusProfiler, write_profile_report
from .registry import RuleAction, RuleRegistry, RuleScopeType
from .review import (
    CandidateReviewEntry,
    CandidateReviewFile,
    CandidateReviewManager,
)
from .sampling import SamplingManifest, StratifiedSampler, write_sampling_manifest
from .service import CleanPipelineService, CleanRunOutcome
from .structural import StructuralCleaner, StructuralCleanResult
from .table_inspection import (
    NormalizedTableInspector,
    TableInspectionConfig,
    TableInspectionPaths,
    write_table_inspection_report,
)
from .template_dedup import TemplateDeduplicationOutcome, TemplateDeduplicator

__all__ = [
    "DEFAULT_CLEAN_CONFIG_PATH",
    "CandidateReviewEntry",
    "CandidateReviewFile",
    "CandidateReviewManager",
    "CleanPipelineConfig",
    "CleanPipelineService",
    "CleanRunOutcome",
    "CleanDocumentAudit",
    "CleanDocumentReport",
    "CleanedDocument",
    "CleanEngine",
    "CleanEvaluationMetrics",
    "CleanEvaluationSample",
    "CleanEvaluationService",
    "CleanExecutionResult",
    "ConceptDriftEvent",
    "ConceptDriftMonitor",
    "DocumentTypeAssignment",
    "DocumentTypeOrigin",
    "DocumentTypeResolver",
    "DocumentTypeReviewStatus",
    "DocumentTypeSidecar",
    "LanguageAnnotation",
    "LanguageDetector",
    "LineDiffStats",
    "NormalizedCorpusProfiler",
    "NormalizedCorpusReader",
    "NormalizedDocumentContext",
    "ProfilingConfig",
    "RuleAction",
    "RuleRegistry",
    "RuleScopeType",
    "SamplingManifest",
    "StageMetrics",
    "StratifiedSampler",
    "StructuralCleaner",
    "StructuralCleanResult",
    "TemplateDeduplicationOutcome",
    "TemplateDeduplicator",
    "NormalizedTableInspector",
    "TableInspectionConfig",
    "TableInspectionPaths",
    "SemanticGuard",
    "load_clean_config",
    "write_profile_report",
    "write_sampling_manifest",
    "write_table_inspection_report",
]
