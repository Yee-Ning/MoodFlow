"""Read immutable Chunk V1 datasets for metadata enrichment."""

from __future__ import annotations

import json
import hashlib
from pathlib import Path

from pydantic import ValidationError

from app.rag.chunk.models import ChunkedDocument


class EnrichmentCorpusError(ValueError):
    pass


class EnrichmentCorpusReader:
    def __init__(
        self,
        project_root: Path,
        *,
        chunked_root: Path,
        manifest_path: Path | None = None,
    ) -> None:
        self.project_root = project_root.resolve()
        self.chunked_root = chunked_root.resolve()
        self.manifest_path = (
            manifest_path.resolve()
            if manifest_path is not None
            else self.chunked_root / "chunked_corpus_manifest.jsonl"
        )

    def load_all(self) -> list[ChunkedDocument]:
        try:
            lines = self.manifest_path.read_text(encoding="utf-8").splitlines()
        except OSError as exc:
            raise EnrichmentCorpusError("chunked_manifest_unreadable") from exc
        documents: list[ChunkedDocument] = []
        seen: set[str] = set()
        for line_number, line in enumerate(lines, 1):
            if not line.strip():
                continue
            try:
                row = json.loads(line)
                if not isinstance(row, dict):
                    raise ValueError("manifest row must be an object")
                path = self._project_path(row.get("chunks_path"))
                if not path.is_relative_to(self.chunked_root):
                    raise EnrichmentCorpusError(
                        f"chunks_path_outside_selected_root:{line_number}"
                    )
                content = path.read_bytes()
                if row.get("chunks_sha256") != "sha256:" + hashlib.sha256(content).hexdigest():
                    raise EnrichmentCorpusError(f"chunked_manifest_hash_mismatch_or_missing:{line_number}")
                document = ChunkedDocument.model_validate_json(content)
                for field in ("document_id", "source_id", "chunk_version", "config_fingerprint", "token_counter"):
                    if row.get(field) != getattr(document, field):
                        raise EnrichmentCorpusError(f"chunked_manifest_identity_mismatch:{line_number}:{field}")
            except EnrichmentCorpusError:
                raise
            except (OSError, ValidationError, ValueError, json.JSONDecodeError) as exc:
                raise EnrichmentCorpusError(
                    f"enrichment_source_invalid_line:{line_number}"
                ) from exc
            if document.document_id in seen:
                raise EnrichmentCorpusError(
                    f"duplicate_chunked_document_id:{document.document_id}"
                )
            seen.add(document.document_id)
            documents.append(document)
        return sorted(documents, key=lambda item: item.document_id)

    def _project_path(self, value: object) -> Path:
        if not isinstance(value, str) or not value:
            raise EnrichmentCorpusError("chunks_path_missing")
        relative = Path(value)
        if relative.is_absolute():
            raise EnrichmentCorpusError("chunks_path_must_be_relative")
        resolved = (self.project_root / relative).resolve()
        if not resolved.is_relative_to(self.project_root):
            raise EnrichmentCorpusError("chunks_path_outside_project")
        return resolved


def latest_chunked_root(project_root: Path) -> Path:
    root = project_root.resolve() / "data" / "chunked"
    candidates: list[tuple[tuple[int, ...], Path]] = []
    for path in root.iterdir() if root.is_dir() else ():
        if not path.is_dir() or not (path / "chunked_corpus_manifest.jsonl").is_file():
            continue
        try:
            version = tuple(
                int(value) for value in path.name.removeprefix("v").split(".")
            )
        except ValueError:
            continue
        candidates.append((version, path))
    if not candidates:
        raise EnrichmentCorpusError("chunked_corpus_not_found")
    return max(candidates, key=lambda item: item[0])[1]
