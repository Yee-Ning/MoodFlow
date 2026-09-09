"""Music RAG Parse + Normalize V1 public contracts."""

from .base import BaseParser, ParseNormalizeError, ParserError
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
    "DetectedFileType",
    "DocumentBlock",
    "DocumentImage",
    "DocumentTable",
    "DuplicateParserError",
    "FileTypeDetector",
    "NormalizedDocument",
    "OutputArtifact",
    "OutcomeStatus",
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
    "UnsupportedCurrentStageError",
    "UnsupportedFileTypeError",
    "create_default_service",
]
