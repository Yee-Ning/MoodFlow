"""Read paired CleanedDocument and immutable NormalizedDocument records."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from pydantic import ValidationError

from app.rag.clean.models import CleanedDocument
from app.rag.parse_normalize.models import NormalizedDocument


class ChunkCorpusError(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class ChunkSourceDocument:
    cleaned: CleanedDocument
    normalized: NormalizedDocument
    cleaned_path: Path
    normalized_path: Path


class ChunkCorpusReader:
    def __init__(
        self,
        project_root: Path,
        *,
        cleaned_root: Path,
        manifest_path: Path | None = None,
    ) -> None:
        self.project_root = project_root.resolve()
        self.cleaned_root = cleaned_root.resolve()
        self.manifest_path = (
            manifest_path.resolve()
            if manifest_path is not None
            else self.cleaned_root / "cleaned_corpus_manifest.jsonl"
        )

    def load_all(self) -> list[ChunkSourceDocument]:
        try:
            lines = self.manifest_path.read_text(encoding="utf-8").splitlines()
        except OSError as exc:
            raise ChunkCorpusError("cleaned_manifest_unreadable") from exc
        sources: list[ChunkSourceDocument] = []
        seen_documents: set[str] = set()
        seen_sources: set[str] = set()
        for line_number, line in enumerate(lines, 1):
            if not line.strip():
                continue
            try:
                row = json.loads(line)
                if not isinstance(row, dict):
                    raise ValueError("manifest row must be an object")
                cleaned_path = self._project_path(row.get("cleaned_path"))
                if not cleaned_path.is_relative_to(self.cleaned_root):
                    raise ChunkCorpusError(
                        f"cleaned_path_outside_selected_root:{line_number}"
                    )
                cleaned = CleanedDocument.model_validate_json(
                    cleaned_path.read_text(encoding="utf-8")
                )
                normalized_path = self._project_path(cleaned.normalized_path)
                normalized = NormalizedDocument.model_validate_json(
                    normalized_path.read_text(encoding="utf-8")
                )
            except ChunkCorpusError:
                raise
            except (OSError, ValidationError, ValueError, json.JSONDecodeError) as exc:
                raise ChunkCorpusError(
                    f"chunk_source_invalid_line:{line_number}"
                ) from exc
            if cleaned.document_id in seen_documents:
                raise ChunkCorpusError(
                    f"duplicate_cleaned_document_id:{cleaned.document_id}"
                )
            if cleaned.source_id in seen_sources:
                raise ChunkCorpusError(
                    f"duplicate_cleaned_source_id:{cleaned.source_id}"
                )
            seen_documents.add(cleaned.document_id)
            seen_sources.add(cleaned.source_id)
            sources.append(
                ChunkSourceDocument(
                    cleaned=cleaned,
                    normalized=normalized,
                    cleaned_path=cleaned_path,
                    normalized_path=normalized_path,
                )
            )
        return sorted(sources, key=lambda item: item.cleaned.document_id)

    def _project_path(self, value: object) -> Path:
        if not isinstance(value, str) or not value:
            raise ChunkCorpusError("manifest_path_missing")
        relative = Path(value)
        if relative.is_absolute():
            raise ChunkCorpusError("manifest_path_must_be_relative")
        resolved = (self.project_root / relative).resolve()
        if not resolved.is_relative_to(self.project_root):
            raise ChunkCorpusError("manifest_path_outside_project")
        return resolved


def latest_cleaned_root(project_root: Path) -> Path:
    root = project_root.resolve() / "data" / "cleaned"
    candidates: list[tuple[tuple[int, ...], Path]] = []
    for path in root.iterdir() if root.is_dir() else ():
        if not path.is_dir() or not (path / "cleaned_corpus_manifest.jsonl").is_file():
            continue
        try:
            version = tuple(
                int(value) for value in path.name.removeprefix("v").split(".")
            )
        except ValueError:
            continue
        candidates.append((version, path))
    if not candidates:
        raise ChunkCorpusError("cleaned_corpus_not_found")
    return max(candidates, key=lambda item: item[0])[1]
