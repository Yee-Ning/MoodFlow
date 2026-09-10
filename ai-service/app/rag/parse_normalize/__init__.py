"""Music RAG Parse + Normalize V1 public contracts."""

from .base import BaseParser, ParseNormalizeError, ParserError
from .corpus import (
    CorpusParseRunner,
    CorpusParseReporter,
    NormalizedCorpusManifestRow,
    RepresentativeBatchSelector,
    RepresentativeSample,
)
from .detection import FileTypeDetector
from .models import (
    NORMALIZATION_VERSION,
    BlockType,
    DetectedFileType,
    DocumentBlock,
    DocumentImage,
    DocumentTable,
    NormalizedDocument,
    OutputArtifact,
    ParseInput,
    ParseMetrics,
    ParseReport,
    ParseStatus,
    ParserAttempt,
    ParserAttemptStatus,
    ParserExecution,
    ParserResult,
)
from .registry import DuplicateParserError, ParserRegistry
from .pdf import (
    PDFInspector,
    PDFInspectorConfig,
    PDFParseQualityConfig,
    PDFParseQualityEvaluator,
    PdfCascadeParser,
)
from .router import (
    ParserRouter,
    UnsupportedCurrentStageError,
    UnsupportedFileTypeError,
)
from .service import (
    OutcomeStatus,
    ParseNormalizeService,
    ParseOutcome,
    create_default_service,
)

__all__ = [
    "NORMALIZATION_VERSION",
    "BaseParser",
    "BlockType",
    "CorpusParseReporter",
    "CorpusParseRunner",
    "DetectedFileType",
    "DocumentBlock",
    "DocumentImage",
    "DocumentTable",
    "DuplicateParserError",
    "FileTypeDetector",
    "NormalizedDocument",
    "NormalizedCorpusManifestRow",
    "OutputArtifact",
    "OutcomeStatus",
    "PDFInspector",
    "PDFInspectorConfig",
    "PDFParseQualityConfig",
    "PDFParseQualityEvaluator",
    "ParseInput",
    "ParseMetrics",
    "ParseNormalizeError",
    "ParseNormalizeService",
    "ParseOutcome",
    "ParseReport",
    "ParseStatus",
    "ParserAttempt",
    "ParserAttemptStatus",
    "ParserExecution",
    "ParserError",
    "ParserRegistry",
    "ParserResult",
    "ParserRouter",
    "RepresentativeBatchSelector",
    "RepresentativeSample",
    "PdfCascadeParser",
    "UnsupportedCurrentStageError",
    "UnsupportedFileTypeError",
    "create_default_service",
]
