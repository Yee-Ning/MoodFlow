"""Read and validate KnowledgeEnrichedChunk JSONL for indexing."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from pydantic import ValidationError

from app.rag.knowledge.models import (
    KnowledgeEnrichedChildChunk,
    KnowledgeEnrichedChunk,
    KnowledgeEnrichedParentChunk,
)


class IndexCorpusError(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class IndexCorpus:
    chunks: list[KnowledgeEnrichedChunk]
    parents: dict[str, KnowledgeEnrichedParentChunk]
    children: list[KnowledgeEnrichedChildChunk]


class IndexCorpusReader:
    def __init__(self, project_root: Path, *, knowledge_path: Path) -> None:
        self.project_root = project_root.resolve()
        self.knowledge_path = knowledge_path.resolve()
        if not self.knowledge_path.is_relative_to(self.project_root):
            raise IndexCorpusError("knowledge_path_outside_project")

    def load(self) -> IndexCorpus:
        try:
            lines = self.knowledge_path.read_text(encoding="utf-8").splitlines()
        except OSError as exc:
            raise IndexCorpusError("knowledge_chunks_unreadable") from exc
        chunks: list[KnowledgeEnrichedChunk] = []
        parents: dict[str, KnowledgeEnrichedParentChunk] = {}
        children: list[KnowledgeEnrichedChildChunk] = []
        seen: set[str] = set()
        for line_number, line in enumerate(lines, 1):
            if not line.strip():
                continue
            try:
                payload = json.loads(line)
                if not isinstance(payload, dict):
                    raise ValueError("row must be an object")
                if payload.get("parent_id") is None:
                    chunk: KnowledgeEnrichedChunk = (
                        KnowledgeEnrichedParentChunk.model_validate(payload)
                    )
                    parents[chunk.chunk_id] = chunk
                else:
                    chunk = KnowledgeEnrichedChildChunk.model_validate(payload)
                    children.append(chunk)
            except (ValidationError, ValueError, json.JSONDecodeError) as exc:
                raise IndexCorpusError(
                    f"knowledge_chunk_invalid_line:{line_number}"
                ) from exc
            if chunk.chunk_id in seen:
                raise IndexCorpusError(f"duplicate_knowledge_chunk_id:{chunk.chunk_id}")
            seen.add(chunk.chunk_id)
            chunks.append(chunk)
        for child in children:
            parent = parents.get(child.parent_id)
            if parent is None:
                raise IndexCorpusError(
                    f"missing_parent:{child.chunk_id}:{child.parent_id}"
                )
            if parent.doc_id != child.doc_id:
                raise IndexCorpusError(f"parent_document_mismatch:{child.chunk_id}")
        return IndexCorpus(chunks=chunks, parents=parents, children=children)


def latest_knowledge_path(project_root: Path) -> Path:
    root = project_root.resolve() / "data" / "knowledge"
    candidates: list[tuple[tuple[int, ...], Path]] = []
    for path in root.iterdir() if root.is_dir() else ():
        chunks_path = path / "knowledge_enriched_chunks.jsonl"
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
        raise IndexCorpusError("knowledge_corpus_not_found")
    return max(candidates, key=lambda item: item[0])[1]
