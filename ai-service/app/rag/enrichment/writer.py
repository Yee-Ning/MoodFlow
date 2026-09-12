"""Atomic JSONL persistence for enriched chunks, separate from Chunk V1."""

from __future__ import annotations

import json
from collections import defaultdict
from pathlib import Path

from app.rag.clean.io import atomic_write_jsonl

from .models import (
    EnrichedChildChunk,
    EnrichedChunk,
    EnrichedParentChunk,
    EnrichmentStatus,
)


class EnrichmentDatasetWriter:
    def __init__(self, project_root: Path, output_root: Path) -> None:
        self.project_root = project_root.resolve()
        self.output_root = output_root.resolve()
        if not self.output_root.is_relative_to(self.project_root):
            raise ValueError("enrichment_output_path_outside_project")

    @property
    def chunks_path(self) -> Path:
        return self.output_root / "enriched_chunks.jsonl"

    @property
    def manifest_path(self) -> Path:
        return self.output_root / "enriched_corpus_manifest.jsonl"

    def load_existing(self) -> dict[str, EnrichedChunk]:
        if not self.chunks_path.is_file():
            return {}
        result: dict[str, EnrichedChunk] = {}
        try:
            lines = self.chunks_path.read_text(encoding="utf-8").splitlines()
            for line_number, line in enumerate(lines, 1):
                if not line.strip():
                    continue
                payload = json.loads(line)
                if not isinstance(payload, dict):
                    raise ValueError(f"row_not_object:{line_number}")
                if payload.get("parent_id") is None:
                    chunk: EnrichedChunk = EnrichedParentChunk.model_validate(payload)
                else:
                    chunk = EnrichedChildChunk.model_validate(payload)
                if chunk.chunk_id in result:
                    raise ValueError(f"duplicate_enriched_chunk_id:{chunk.chunk_id}")
                result[chunk.chunk_id] = chunk
        except (OSError, ValueError, json.JSONDecodeError) as exc:
            raise ValueError("existing_enriched_chunks_invalid") from exc
        return result

    def write(
        self,
        chunks: list[EnrichedChunk],
        *,
        preserve_existing: bool = False,
    ) -> tuple[Path, Path]:
        by_id = self.load_existing() if preserve_existing else {}
        by_id.update({chunk.chunk_id: chunk for chunk in chunks})
        ordered = sorted(by_id.values(), key=self._sort_key)
        atomic_write_jsonl(
            self.chunks_path,
            (chunk.model_dump(mode="json") for chunk in ordered),
        )
        atomic_write_jsonl(self.manifest_path, self._manifest_rows(ordered))
        return self.chunks_path, self.manifest_path

    def _manifest_rows(self, chunks: list[EnrichedChunk]) -> list[dict[str, object]]:
        grouped: dict[str, list[EnrichedChunk]] = defaultdict(list)
        for chunk in chunks:
            grouped[chunk.doc_id].append(chunk)
        relative_chunks_path = self.chunks_path.relative_to(
            self.project_root
        ).as_posix()
        rows: list[dict[str, object]] = []
        for document_id, values in sorted(grouped.items()):
            source_id = values[0].enrichment.source.source_id
            statuses = {status.value: 0 for status in EnrichmentStatus}
            for value in values:
                statuses[value.enrichment.quality.enrichment_status.value] += 1
            rows.append(
                {
                    "document_id": document_id,
                    "source_id": source_id,
                    "enrichment_version": values[
                        0
                    ].enrichment.provenance.enrichment_version,
                    "schema_version": values[0].enrichment.provenance.schema_version,
                    "prompt_version": values[0].enrichment.provenance.prompt_version,
                    "chunk_count": len(values),
                    **{f"{key}_count": count for key, count in statuses.items()},
                    "enriched_chunks_path": relative_chunks_path,
                }
            )
        return rows

    @staticmethod
    def _sort_key(chunk: EnrichedChunk) -> tuple[str, int, int, int, str]:
        level = 0 if isinstance(chunk, EnrichedParentChunk) else 1
        parent_index = (
            chunk.chunk_index
            if isinstance(chunk, EnrichedParentChunk)
            else int(chunk.metadata.get("parent_chunk_index", chunk.chunk_index))
        )
        local_index = (
            0
            if isinstance(chunk, EnrichedParentChunk)
            else int(chunk.metadata.get("child_index_in_parent", chunk.chunk_index))
        )
        return chunk.doc_id, parent_index, level, local_index, chunk.chunk_id
