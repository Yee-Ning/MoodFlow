"""Validated access to immutable normalized documents."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlparse

from pydantic import ValidationError

from app.rag.parse_normalize.models import NormalizedDocument

from .document_types import (
    DocumentTypeOrigin,
    DocumentTypeResolver,
    DocumentTypeSidecar,
)
from .errors import NormalizedCorpusError


MISSING_DIMENSION = "__missing__"


@dataclass(frozen=True, slots=True)
class NormalizedDocumentContext:
    document: NormalizedDocument
    normalized_path: Path
    relative_normalized_path: str
    source: str
    source_type: str | None
    document_type: str | None
    document_type_origin: DocumentTypeOrigin | None
    document_type_sidecar_version: str | None
    document_type_sidecar_checksum: str | None


class NormalizedCorpusReader:
    """Read only manifest-referenced normalized output beneath the project root."""

    def __init__(
        self,
        project_root: Path,
        *,
        normalized_root: Path | None = None,
        manifest_path: Path | None = None,
        document_type_sidecar: DocumentTypeSidecar | None = None,
        document_type_sidecar_checksum: str | None = None,
    ) -> None:
        self.project_root = project_root.resolve()
        self.normalized_root = (
            normalized_root.resolve()
            if normalized_root is not None
            else self.project_root / "data" / "normalized"
        )
        self.manifest_path = (
            manifest_path.resolve()
            if manifest_path is not None
            else self.normalized_root / "normalized_corpus_manifest.jsonl"
        )
        self.document_type_resolver = DocumentTypeResolver(
            document_type_sidecar,
            sidecar_checksum=document_type_sidecar_checksum,
        )

    def load_all(self) -> list[NormalizedDocumentContext]:
        paths = (
            self._manifest_document_paths()
            if self.manifest_path.is_file()
            else sorted(self.normalized_root.glob("*/document.json"))
        )
        documents: list[NormalizedDocumentContext] = []
        seen_ids: set[str] = set()
        seen_sources: set[str] = set()
        for path in paths:
            resolved = path.resolve()
            if not resolved.is_relative_to(self.normalized_root.resolve()):
                raise NormalizedCorpusError("normalized_path_outside_root")
            try:
                document = NormalizedDocument.model_validate_json(
                    resolved.read_text(encoding="utf-8")
                )
            except (OSError, ValidationError, ValueError) as exc:
                raise NormalizedCorpusError(
                    f"normalized_document_invalid:{resolved}"
                ) from exc
            if document.document_id in seen_ids:
                raise NormalizedCorpusError(
                    f"duplicate_normalized_document_id:{document.document_id}"
                )
            if document.source_id in seen_sources:
                raise NormalizedCorpusError(
                    f"duplicate_normalized_source_id:{document.source_id}"
                )
            seen_ids.add(document.document_id)
            seen_sources.add(document.source_id)
            source_metadata = self._mapping(document.metadata.get("source_metadata"))
            normalized_document_type = self._optional_dimension(
                source_metadata.get("document_type")
                or document.metadata.get("document_type")
            )
            (
                document_type,
                document_type_origin,
                sidecar_version,
                sidecar_checksum,
            ) = self.document_type_resolver.resolve(
                document.source_id, normalized_document_type
            )
            documents.append(
                NormalizedDocumentContext(
                    document=document,
                    normalized_path=resolved,
                    relative_normalized_path=resolved.relative_to(
                        self.project_root
                    ).as_posix(),
                    source=self._source(document, source_metadata),
                    source_type=self._optional_dimension(
                        source_metadata.get("source_type")
                        or document.metadata.get("source_type")
                    ),
                    document_type=document_type,
                    document_type_origin=document_type_origin,
                    document_type_sidecar_version=sidecar_version,
                    document_type_sidecar_checksum=sidecar_checksum,
                )
            )
        return sorted(documents, key=lambda item: item.document.document_id)

    def _manifest_document_paths(self) -> list[Path]:
        try:
            lines = self.manifest_path.read_text(encoding="utf-8").splitlines()
        except OSError as exc:
            raise NormalizedCorpusError("normalized_manifest_unreadable") from exc
        paths: list[Path] = []
        for line_number, line in enumerate(lines, 1):
            if not line.strip():
                continue
            try:
                row = json.loads(line)
                if not isinstance(row, dict):
                    raise ValueError("manifest row must be an object")
            except (json.JSONDecodeError, ValueError) as exc:
                raise NormalizedCorpusError(
                    f"normalized_manifest_invalid_line:{line_number}"
                ) from exc
            document_id = row.get("document_id")
            normalized_path = row.get("normalized_path")
            if document_id is None:
                continue
            if not isinstance(normalized_path, str) or not normalized_path:
                raise NormalizedCorpusError(
                    f"normalized_manifest_missing_path:{line_number}"
                )
            candidate = (self.project_root / normalized_path).resolve()
            if not candidate.is_relative_to(self.normalized_root.resolve()):
                raise NormalizedCorpusError(
                    f"normalized_manifest_path_outside_root:{line_number}"
                )
            paths.append(candidate)
        return paths

    @staticmethod
    def _mapping(value: object) -> dict[str, object]:
        return value if isinstance(value, dict) else {}

    @classmethod
    def _source(
        cls, document: NormalizedDocument, metadata: dict[str, object]
    ) -> str:
        for value in (metadata.get("source"), metadata.get("publisher")):
            normalized = cls._optional_dimension(value)
            if normalized:
                return normalized
        original_url = cls._optional_dimension(metadata.get("original_url"))
        if original_url:
            hostname = urlparse(original_url).hostname
            if hostname:
                return hostname.casefold()
        return document.source_id

    @staticmethod
    def _optional_dimension(value: object) -> str | None:
        if not isinstance(value, str):
            return None
        normalized = value.strip()
        return normalized or None
