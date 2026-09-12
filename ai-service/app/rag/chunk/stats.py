"""Deterministic per-document Chunk V1 statistics."""

from __future__ import annotations

from app.rag.parse_normalize.models import DocumentTable

from .models import (
    ChildChunk,
    ChunkStats,
    ChunkType,
    ChunkValidationReport,
    ParentChunk,
    TableHeaderState,
)


def build_chunk_stats(
    parents: list[ParentChunk],
    children: list[ChildChunk],
    source_tables: list[DocumentTable],
    validation: ChunkValidationReport,
) -> ChunkStats:
    child_counts = [child.token_count for child in children]
    emitted_rows = sum(
        len(child.table_data.rows) for child in children if child.table_data is not None
    )
    header_states = {
        child.table_data.table_id: child.table_data.header_state
        for child in children
        if child.table_data is not None
    }
    source_rows = 0
    for table in source_tables:
        header_count = (
            1
            if table.rows
            and header_states.get(table.table_id, TableHeaderState.NONE)
            is not TableHeaderState.NONE
            else 0
        )
        source_rows += max(0, len(table.rows) - header_count)
    return ChunkStats(
        parent_count=len(parents),
        child_count=len(children),
        narrative_parent_count=sum(
            parent.chunk_type is ChunkType.NARRATIVE for parent in parents
        ),
        narrative_child_count=sum(
            child.chunk_type is ChunkType.NARRATIVE for child in children
        ),
        table_parent_count=sum(
            parent.chunk_type is ChunkType.TABLE for parent in parents
        ),
        table_child_count=sum(
            child.chunk_type is ChunkType.TABLE for child in children
        ),
        parent_tokens=sum(parent.token_count for parent in parents),
        child_tokens=sum(child_counts),
        min_child_tokens=min(child_counts, default=0),
        max_child_tokens=max(child_counts, default=0),
        average_child_tokens=(
            round(sum(child_counts) / len(child_counts), 4) if child_counts else 0
        ),
        source_table_rows=source_rows,
        emitted_table_rows=emitted_rows,
        validation_errors=validation.error_count,
        validation_warnings=validation.warning_count,
    )
