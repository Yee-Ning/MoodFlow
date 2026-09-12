"""Non-persisted work records shared by chunking stages."""

from __future__ import annotations

from dataclasses import dataclass, field

from pydantic import JsonValue

from .models import ChunkType, TableChunkData


@dataclass(frozen=True, slots=True)
class NarrativePart:
    order_index: int
    block_id: str
    block_type: str
    content: str
    metadata: dict[str, JsonValue]


@dataclass(frozen=True, slots=True)
class NarrativeRun:
    order_index: int
    section_path: tuple[str, ...]
    parts: tuple[NarrativePart, ...]


@dataclass(frozen=True, slots=True)
class RoutedTable:
    order_index: int
    section_path: tuple[str, ...]
    source_block_id: str | None
    table_id: str


@dataclass(slots=True)
class ChildDraft:
    content: str
    metadata: dict[str, JsonValue]
    table_data: TableChunkData | None = None


@dataclass(slots=True)
class ParentDraft:
    order_key: tuple[int, int]
    chunk_type: ChunkType
    section_path: tuple[str, ...]
    breadcrumb: str
    content: str
    metadata: dict[str, JsonValue]
    table_data: TableChunkData | None = None
    children: list[ChildDraft] = field(default_factory=list)
