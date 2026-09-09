"""File type detection based on raw snapshot metadata."""

from pathlib import Path

from app.rag.source_ingestion.models import RawSnapshotRecord

from .models import DetectedFileType


class FileTypeDetector:
    _MIME_TYPES = {
        "text/html": DetectedFileType.HTML,
        "application/xhtml+xml": DetectedFileType.HTML,
        "text/markdown": DetectedFileType.MARKDOWN,
        "text/x-markdown": DetectedFileType.MARKDOWN,
        "text/plain": DetectedFileType.TXT,
        "application/json": DetectedFileType.JSON,
        "application/pdf": DetectedFileType.PDF,
    }
    _GENERIC_MIME_TYPES = {
        "",
        "application/octet-stream",
        "binary/octet-stream",
    }
    _EXTENSIONS = {
        ".html": DetectedFileType.HTML,
        ".htm": DetectedFileType.HTML,
        ".xhtml": DetectedFileType.HTML,
        ".md": DetectedFileType.MARKDOWN,
        ".markdown": DetectedFileType.MARKDOWN,
        ".txt": DetectedFileType.TXT,
        ".text": DetectedFileType.TXT,
        ".json": DetectedFileType.JSON,
        ".pdf": DetectedFileType.PDF,
    }

    def detect(self, record: RawSnapshotRecord) -> DetectedFileType:
        media_type = record.content_type.partition(";")[0].strip().lower()
        detected = self._MIME_TYPES.get(media_type)
        if detected is not None:
            return detected
        if media_type.startswith("application/") and media_type.endswith("+json"):
            return DetectedFileType.JSON
        if media_type not in self._GENERIC_MIME_TYPES:
            return DetectedFileType.UNKNOWN
        return self._EXTENSIONS.get(
            Path(record.raw_path).suffix.lower(), DetectedFileType.UNKNOWN
        )
