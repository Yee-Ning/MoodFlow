"""Atomic persistence for KnowledgeEnrichedChunk datasets."""

from __future__ import annotations

import json
from collections import defaultdict
from pathlib import Path

from app.rag.chunk.models import ChildChunk
from app.rag.clean.io import atomic_write_jsonl

from .models import (
    KnowledgeEnrichedChildChunk,
    KnowledgeEnrichedChunk,
    KnowledgeEnrichedParentChunk,
    KnowledgeStatus,
)


class KnowledgeDatasetWriter:
    def __init__(self, project_root: Path, output_root: Path) -> None:
        self.project_root = project_root.resolve()
        self.output_root = output_root.resolve()
        if not self.output_root.is_relative_to(self.project_root):
            raise ValueError("knowledge_output_path_outside_project")

    @property
    def chunks_path(self) -> Path:
        return self.output_root / "knowledge_enriched_chunks.jsonl"

    @property
    def manifest_path(self) -> Path:
        return self.output_root / "knowledge_corpus_manifest.jsonl"

    def load_existing(self) -> dict[str, KnowledgeEnrichedChunk]:
        if not self.chunks_path.is_file():
            return {}
        result: dict[str, KnowledgeEnrichedChunk] = {}
        try:
            lines = self.chunks_path.read_text(encoding="utf-8").splitlines()
            for line_number, line in enumerate(lines, 1):
                if not line.strip():
                    continue
                payload = json.loads(line)
                if not isinstance(payload, dict):
                    raise ValueError(f"row_not_object:{line_number}")
                if payload.get("parent_id") is None:
                    chunk: KnowledgeEnrichedChunk = (
                        KnowledgeEnrichedParentChunk.model_validate(payload)
                    )
                else:
                    chunk = KnowledgeEnrichedChildChunk.model_validate(payload)
                if chunk.chunk_id in result:
                    raise ValueError(f"duplicate_knowledge_chunk_id:{chunk.chunk_id}")
                result[chunk.chunk_id] = chunk
        except (OSError, ValueError, json.JSONDecodeError) as exc:
            raise ValueError("existing_knowledge_chunks_invalid") from exc
        return result

    def write(
        self,
        chunks: list[KnowledgeEnrichedChunk],
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

    def _manifest_rows(
        self, chunks: list[KnowledgeEnrichedChunk]
    ) -> list[dict[str, object]]:
        grouped: dict[str, list[KnowledgeEnrichedChunk]] = defaultdict(list)
        for chunk in chunks:
            grouped[chunk.doc_id].append(chunk)
        chunks_path = self.chunks_path.relative_to(self.project_root).as_posix()
        rows: list[dict[str, object]] = []
        for document_id, values in sorted(grouped.items()):
            statuses = {status.value: 0 for status in KnowledgeStatus}
            for value in values:
                statuses[value.knowledge.quality.status.value] += 1
            stats = [value.knowledge.stats for value in values]
            provenance = values[0].knowledge.provenance
            rows.append(
                {
                    "document_id": document_id,
                    "source_id": values[0].enrichment.source.source_id,
                    "entity_linking_version": provenance.entity_linking_version,
                    "claim_extraction_version": provenance.claim_extraction_version,
                    "claim_verification_version": provenance.claim_verification_version,
                    "registry_version": provenance.registry_version,
                    "chunk_count": len(values),
                    "entity_count": sum(item.entity_count for item in stats),
                    "claim_count": sum(item.claim_count for item in stats),
                    **{f"{key}_count": count for key, count in statuses.items()},
                    "knowledge_chunks_path": chunks_path,
                }
            )
        return rows

    @staticmethod
    def _sort_key(chunk: KnowledgeEnrichedChunk) -> tuple[str, int, int, int, str]:
        level = 1 if isinstance(chunk, ChildChunk) else 0
        parent_index = (
            int(chunk.metadata.get("parent_chunk_index", chunk.chunk_index))
            if isinstance(chunk, ChildChunk)
            else chunk.chunk_index
        )
        local_index = (
            int(chunk.metadata.get("child_index_in_parent", chunk.chunk_index))
            if isinstance(chunk, ChildChunk)
            else 0
        )
        return chunk.doc_id, parent_index, level, local_index, chunk.chunk_id
