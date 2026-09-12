"""Offline Clean -> Chunk corpus orchestration."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from .config import ChunkConfig
from .corpus import ChunkCorpusReader
from .engine import ChunkEngine
from .models import ChunkedDocument
from .token_counter import TokenCounter
from .writer import ChunkDatasetWriter


class ChunkValidationError(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class ChunkRunOutcome:
    documents: list[ChunkedDocument]
    summary: dict[str, object]
    manifest_path: Path | None


class ChunkPipelineService:
    def __init__(
        self,
        project_root: Path,
        config: ChunkConfig,
        *,
        cleaned_root: Path,
        cleaned_manifest_path: Path | None = None,
        output_root: Path | None = None,
        token_counter: TokenCounter | None = None,
    ) -> None:
        self.project_root = project_root.resolve()
        self.config = config
        self.reader = ChunkCorpusReader(
            self.project_root,
            cleaned_root=cleaned_root,
            manifest_path=cleaned_manifest_path,
        )
        self.engine = ChunkEngine(config, token_counter=token_counter)
        self.output_root = (
            output_root.resolve()
            if output_root is not None
            else self.project_root / "data" / "chunked" / config.chunk_version
        )

    def run(self, *, validate_only: bool = False) -> ChunkRunOutcome:
        documents = [
            self.engine.chunk(source.cleaned, source.normalized)
            for source in self.reader.load_all()
        ]
        invalid = [
            value.document_id for value in documents if not value.validation.valid
        ]
        if invalid:
            raise ChunkValidationError(f"chunk_validation_failed:{invalid[:10]}")
        manifest = None
        if not validate_only:
            manifest = ChunkDatasetWriter(self.project_root, self.output_root).write(
                documents
            )
        summary: dict[str, object] = {
            "status": "validated" if validate_only else "completed",
            "chunk_version": self.config.chunk_version,
            "config_fingerprint": self.config.fingerprint(),
            "token_counter": self.engine.counter.name,
            "document_count": len(documents),
            "parent_count": sum(item.stats.parent_count for item in documents),
            "child_count": sum(item.stats.child_count for item in documents),
            "table_child_count": sum(
                item.stats.table_child_count for item in documents
            ),
            "validation_warning_count": sum(
                item.validation.warning_count for item in documents
            ),
        }
        return ChunkRunOutcome(documents, summary, manifest)
