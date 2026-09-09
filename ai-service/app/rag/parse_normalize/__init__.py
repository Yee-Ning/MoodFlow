"""Music RAG Parse + Normalize V1 public contracts."""

from .base import BaseParser, ParseNormalizeError, ParserError
from .detection import FileTypeDetector
from .models import (
    NORMALIZATION_VERSION,
    BlockType,
    DetectedFileType,
    DocumentBlock,
    NormalizedDocument,
    ParseInput,
    ParseMetrics,
    ParseReport,
    ParseStatus,
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
    "DuplicateParserError",
    "FileTypeDetector",
    "NormalizedDocument",
    "OutcomeStatus",
    "ParseInput",
    "ParseMetrics",
    "ParseNormalizeError",
    "ParseNormalizeService",
    "ParseOutcome",
    "ParseReport",
    "ParseStatus",
    "ParserError",
    "ParserRegistry",
    "ParserResult",
    "ParserRouter",
    "UnsupportedCurrentStageError",
    "UnsupportedFileTypeError",
    "create_default_service",
]
