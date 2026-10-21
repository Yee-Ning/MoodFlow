"""Offline Clean -> Chunk corpus orchestration."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from .config import ChunkConfig
from .corpus import ChunkCorpusReader
from .engine import ChunkEngine
from .models import ChunkedDocument, ChunkType, ValidationSeverity
from .token_counter import TokenCounter
from .writer import ChunkDatasetWriter
from .stats import summarize_chunk_quality
from app.rag.clean.io import atomic_write_json


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

    def run(self, *, validate_only: bool = False, include_manual_review: bool = False) -> ChunkRunOutcome:
        sources = self.reader.load_all(include_manual_review=include_manual_review)
        manual_review = [dict(row) for row in self.reader.manual_review]
        documents: list[ChunkedDocument] = []
        oversized_review = []
        invalid = []
        for source in sources:
            document = self.engine.chunk(source.cleaned, source.normalized)
            if not document.validation.valid:
                oversized_ids = self._oversized_record_errors_only(document)
                if not oversized_ids:
                    invalid.append(document.document_id)
                    continue
                review = next((row for row in manual_review
                               if row["document_id"] == document.document_id), None)
                if review is None:
                    review = {"document_id": document.document_id, "source_id": document.source_id,
                              "reasons": [], "disposition": "included_by_flag" if include_manual_review else "quarantined"}
                    manual_review.append(review)
                review["reasons"] = [*review["reasons"], "chunk_structured_record_too_long"]
                review.update(chunk_ids=sorted(oversized_ids), max_parent_tokens=self.config.max_parent_tokens,
                              validation_error_codes=["chunk_too_long"])
                oversized_review.append(review)
                if not include_manual_review:
                    continue
                # The override permits persistence, never a passing validation.
                marker = {"needs_review": True, "reasons": list(review["reasons"]),
                          "disposition": "included_by_flag"}
                document = document.model_copy(update={
                    "parents": [chunk.model_copy(update={"metadata": {**chunk.metadata, "chunk_review": marker}})
                                for chunk in document.parents],
                    "children": [chunk.model_copy(update={"metadata": {**chunk.metadata, "chunk_review": marker}})
                                 for chunk in document.children],
                })
            documents.append(document)
        if invalid:
            raise ChunkValidationError(f"chunk_validation_failed:{invalid[:10]}")
        manifest = None
        if not validate_only:
            manifest = ChunkDatasetWriter(self.project_root, self.output_root).write(
                documents, manual_review=oversized_review
            )
        summary: dict[str, object] = {
            "status": "validated" if validate_only else "completed",
            "chunk_version": self.config.chunk_version,
            "config_fingerprint": self.config.fingerprint(),
            "token_counter": self.engine.counter.name,
            "document_count": len(documents),
            "manual_review_documents": manual_review,
            "input_document_count": len(documents) + sum(row["disposition"] == "quarantined" for row in manual_review),
            "include_manual_review": include_manual_review,
            "manual_review_document_count": len(manual_review),
            "quarantined_document_count": sum(row["disposition"] == "quarantined" for row in manual_review),
            "parent_count": sum(item.stats.parent_count for item in documents),
            "child_count": sum(item.stats.child_count for item in documents),
            "table_child_count": sum(
                item.stats.table_child_count for item in documents
            ),
            "structured_record_parent_count": sum(
                item.stats.structured_record_parent_count for item in documents
            ),
            "quality": summarize_chunk_quality(documents),
            "validation_warning_count": sum(
                item.validation.warning_count for item in documents
            ),
        }
        if not validate_only:
            atomic_write_json(self.output_root / "chunk_summary.json", summary)
        return ChunkRunOutcome(documents, summary, manifest)

    def _oversized_record_errors_only(self, document: ChunkedDocument) -> set[str]:
        oversized_ids = {
            parent.chunk_id for parent in document.parents
            if parent.chunk_type is ChunkType.STRUCTURED_RECORD
            and parent.token_count > self.config.max_parent_tokens
        }
        errors = [issue for issue in document.validation.issues if issue.severity is ValidationSeverity.ERROR]
        if errors and all(issue.code == "chunk_too_long" and issue.chunk_id in oversized_ids for issue in errors):
            return {issue.chunk_id for issue in errors}
        return set()
