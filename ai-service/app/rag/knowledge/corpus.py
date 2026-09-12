"""Read immutable enriched chunk JSONL datasets."""

from __future__ import annotations

import json
from pathlib import Path

from pydantic import ValidationError

from app.rag.enrichment.models import (
    EnrichedChildChunk,
    EnrichedChunk,
    EnrichedParentChunk,
)


class KnowledgeCorpusError(ValueError):
    pass


class KnowledgeCorpusReader:
    def __init__(self, project_root: Path, *, enriched_path: Path) -> None:
        self.project_root = project_root.resolve()
        self.enriched_path = enriched_path.resolve()
        if not self.enriched_path.is_relative_to(self.project_root):
            raise KnowledgeCorpusError("enriched_path_outside_project")

    def load_all(self) -> list[EnrichedChunk]:
        try:
            lines = self.enriched_path.read_text(encoding="utf-8").splitlines()
        except OSError as exc:
            raise KnowledgeCorpusError("enriched_chunks_unreadable") from exc
        chunks: list[EnrichedChunk] = []
        seen: set[str] = set()
        for line_number, line in enumerate(lines, 1):
            if not line.strip():
                continue
            try:
                payload = json.loads(line)
                if not isinstance(payload, dict):
                    raise ValueError("row must be an object")
                if payload.get("parent_id") is None:
                    chunk: EnrichedChunk = EnrichedParentChunk.model_validate(payload)
                else:
                    chunk = EnrichedChildChunk.model_validate(payload)
            except (ValidationError, ValueError, json.JSONDecodeError) as exc:
                raise KnowledgeCorpusError(
                    f"enriched_chunk_invalid_line:{line_number}"
                ) from exc
            if chunk.chunk_id in seen:
                raise KnowledgeCorpusError(f"duplicate_enriched_chunk_id:{chunk.chunk_id}")
            seen.add(chunk.chunk_id)
            chunks.append(chunk)
        return chunks


def latest_enriched_path(project_root: Path) -> Path:
    root = project_root.resolve() / "data" / "enriched"
    candidates: list[tuple[tuple[int, ...], Path]] = []
    for path in root.iterdir() if root.is_dir() else ():
        chunks_path = path / "enriched_chunks.jsonl"
        if not path.is_dir() or not chunks_path.is_file():
            continue
        try:
            version = tuple(
                int(value) for value in path.name.removeprefix("v").split(".")
            )
        except ValueError:
            continue
        candidates.append((version, chunks_path))
    if not candidates:
        raise KnowledgeCorpusError("enriched_corpus_not_found")
    return max(candidates, key=lambda item: item[0])[1]
