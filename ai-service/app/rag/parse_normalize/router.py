"""Central parser selection for raw snapshots."""

from app.rag.source_ingestion.models import RawSnapshotRecord

from .base import BaseParser, ParseNormalizeError
from .detection import FileTypeDetector
from .models import DetectedFileType
from .registry import ParserNotRegisteredError, ParserRegistry


class UnsupportedCurrentStageError(ParseNormalizeError):
    """The input belongs to a deliberately deferred parser stage."""


class UnsupportedFileTypeError(ParseNormalizeError):
    """The input type cannot be routed."""


class ParserRouter:
    def __init__(self, detector: FileTypeDetector, registry: ParserRegistry) -> None:
        self.detector = detector
        self.registry = registry

    def select(self, record: RawSnapshotRecord) -> BaseParser:
        detected = self.detector.detect(record)
        if detected is DetectedFileType.PDF:
            raise UnsupportedCurrentStageError("unsupported_for_current_stage: pdf")
        if detected is DetectedFileType.UNKNOWN:
            raise UnsupportedFileTypeError("unsupported_file_type: unknown")
        try:
            return self.registry.resolve(detected)
        except ParserNotRegisteredError as exc:
            raise UnsupportedFileTypeError(
                f"unsupported_file_type: {detected.value}"
            ) from exc
