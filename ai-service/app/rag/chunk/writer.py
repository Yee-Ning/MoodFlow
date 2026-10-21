"""Atomic persistence for versioned Chunk V1 datasets."""

from __future__ import annotations

from pathlib import Path
import hashlib

from app.rag.clean.io import atomic_write_json, atomic_write_jsonl

from .models import ChunkedDocument


class ChunkDatasetWriter:
    def __init__(self, project_root: Path, output_root: Path) -> None:
        self.project_root = project_root.resolve()
        self.output_root = output_root.resolve()

    def write(
        self, documents: list[ChunkedDocument], *, manual_review: list[dict[str, object]] | None = None
    ) -> Path:
        review_by_document = {row["document_id"]: row for row in manual_review or []}
        rows: list[dict[str, object]] = []
        for document in sorted(documents, key=lambda value: value.document_id):
            directory = (self.output_root / document.source_id).resolve()
            if not directory.is_relative_to(self.output_root):
                raise ValueError("chunk_output_path_outside_root")
            output = directory / "chunks.json"
            atomic_write_json(output, document.model_dump(mode="json"))
            rows.append(
                {
                    "document_id": document.document_id,
                    "source_id": document.source_id,
                    "chunk_version": document.chunk_version,
                    "config_fingerprint": document.config_fingerprint,
                    "token_counter": document.token_counter,
                    "parent_count": document.stats.parent_count,
                    "child_count": document.stats.child_count,
                    "valid": document.validation.valid,
                    "chunks_path": output.relative_to(self.project_root).as_posix(),
                    "chunks_sha256": "sha256:" + hashlib.sha256(output.read_bytes()).hexdigest(),
                }
            )
            review = review_by_document.get(document.document_id)
            if review is not None:
                rows[-1].update(needs_review=True, review_reasons=review["reasons"],
                                disposition=review["disposition"])
        manifest = self.output_root / "chunked_corpus_manifest.jsonl"
        atomic_write_jsonl(manifest, rows)
        return manifest
