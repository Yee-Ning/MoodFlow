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

__all__ = [
    "NORMALIZATION_VERSION",
    "BaseParser",
    "BlockType",
    "DetectedFileType",
    "DocumentBlock",
    "DuplicateParserError",
    "FileTypeDetector",
    "NormalizedDocument",
    "ParseInput",
    "ParseMetrics",
    "ParseNormalizeError",
    "ParseReport",
    "ParseStatus",
    "ParserError",
    "ParserRegistry",
    "ParserResult",
    "ParserRouter",
    "UnsupportedCurrentStageError",
    "UnsupportedFileTypeError",
]
