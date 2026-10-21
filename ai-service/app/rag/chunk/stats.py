"""Deterministic per-document Chunk V1 statistics."""

from __future__ import annotations

from math import ceil

from app.rag.parse_normalize.models import DocumentTable

from .models import (
    ChildChunk,
    ChunkedDocument,
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
        structured_record_parent_count=sum(
            parent.chunk_type is ChunkType.STRUCTURED_RECORD for parent in parents
        ),
        structured_record_child_count=sum(
            child.chunk_type is ChunkType.STRUCTURED_RECORD for child in children
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


def summarize_chunk_quality(documents: list[ChunkedDocument]) -> dict[str, object]:
    """Batch statistics only; no cuts, merges, IDs or token policies are changed.

    Quantiles use nearest rank. Sentence cuts inspect non-final narrative-child
    end boundaries within each parent, after merges; this is a punctuation
    heuristic, not semantic sentence analysis. Ratios include denominators.
    """
    parents = [parent for document in documents for parent in document.parents]
    children = [child for document in documents for child in document.children]
    chunks = [*parents, *children]

    def distribution(values):
        ordered = sorted(values)
        return {"count": len(ordered), "p50": ordered[ceil(len(ordered) * .50) - 1] if ordered else 0,
                "p99": ordered[ceil(len(ordered) * .99) - 1] if ordered else 0}

    def ratio(numerator, denominator):
        return round(numerator / denominator, 6) if denominator else 0.0

    boundaries = cuts = 0
    for document in documents:
        grouped: dict[str, list[ChildChunk]] = {}
        for child in document.children:
            if child.chunk_type is ChunkType.NARRATIVE:
                grouped.setdefault(child.parent_id, []).append(child)
        for siblings in grouped.values():
            ordered = sorted(siblings, key=lambda child: child.chunk_index)
            for child in ordered[:-1]:
                boundaries += 1
                tail = child.content.rstrip().rstrip('"\'”’」』）)]').rstrip()
                cuts += not tail.endswith(("。", "！", "？", ".", "!", "?"))
    narrative = [parent for parent in parents if parent.chunk_type is ChunkType.NARRATIVE]
    merges = [parent.metadata.get("small_merge") for parent in narrative]
    observed = [value for value in merges if isinstance(value, dict)]
    triggered = sum(value.get("merge_operation_count", 0) > 0 for value in observed)
    type_counts = {kind.value: sum(chunk.chunk_type is kind for chunk in chunks) for kind in ChunkType}
    return {
        "length_tokens": {"quantile_method": "nearest_rank", "all": distribution(c.token_count for c in chunks),
                          "parent": distribution(c.token_count for c in parents),
                          "child": distribution(c.token_count for c in children)},
        "sentence_cut": {"scope": "nonfinal_narrative_child_ends_within_parent_after_merge",
                         "boundary_count": boundaries, "mid_sentence_cut_count": cuts,
                         "rate": ratio(cuts, boundaries), "method": "terminal_punctuation_v1"},
        "small_merge": {"scope": "narrative_parent", "parent_count": len(narrative),
                        "observed_parent_count": len(observed), "unobserved_parent_count": len(narrative) - len(observed),
                        "triggered_parent_count": triggered, "trigger_rate": ratio(triggered, len(observed)),
                        **{name: sum(value.get(name, 0) for value in observed) for name in
                           ("input_child_count", "small_candidate_count", "merge_operation_count")}},
        "chunk_type_scope": "all_persisted_parents_and_children", "chunk_count": len(chunks),
        "chunk_type_counts": type_counts,
        "table_chunk_ratio": ratio(type_counts[ChunkType.TABLE.value], len(chunks)),
        "structured_record_ratio": ratio(type_counts[ChunkType.STRUCTURED_RECORD.value], len(chunks)),
    }
