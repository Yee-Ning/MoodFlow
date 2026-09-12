"""Cross-chunk invariants and lossless table validation."""

from __future__ import annotations

from collections import Counter, defaultdict

from app.rag.clean.models import CleanedDocument
from app.rag.parse_normalize.models import DocumentBlock, DocumentTable

from .config import ChunkConfig
from .context import breadcrumb
from .models import (
    ChildChunk,
    ChunkType,
    ChunkValidationIssue,
    ChunkValidationReport,
    ParentChunk,
    TableHeaderState,
    ValidationSeverity,
)
from .table import TableChunker


class ChunkValidator:
    def __init__(self, config: ChunkConfig) -> None:
        self.config = config

    def validate(
        self,
        cleaned: CleanedDocument,
        parents: list[ParentChunk],
        children: list[ChildChunk],
        source_tables: list[DocumentTable],
        block_by_id: dict[str, DocumentBlock],
    ) -> ChunkValidationReport:
        issues: list[ChunkValidationIssue] = []
        all_chunks: list[ParentChunk | ChildChunk] = [*parents, *children]
        if cleaned.cleaned_text.strip() and not children:
            self._add(
                issues,
                "no_chunks_emitted",
                "non-empty CleanedDocument emitted no child chunks",
            )
        self._basic(cleaned, all_chunks, issues)
        self._duplicates(parents, children, issues)
        self._relationships(parents, children, issues)
        self._tables(source_tables, block_by_id, children, issues)
        errors = sum(issue.severity is ValidationSeverity.ERROR for issue in issues)
        warnings = len(issues) - errors
        return ChunkValidationReport(
            valid=errors == 0,
            issues=issues,
            error_count=errors,
            warning_count=warnings,
        )

    def _basic(
        self,
        cleaned: CleanedDocument,
        chunks: list[ParentChunk | ChildChunk],
        issues: list[ChunkValidationIssue],
    ) -> None:
        for chunk in chunks:
            if not chunk.content.strip():
                self._add(
                    issues, "empty_chunk", "chunk content is empty", chunk.chunk_id
                )
            maximum = (
                self.config.max_parent_tokens
                if isinstance(chunk, ParentChunk)
                else self.config.max_child_tokens
            )
            if chunk.token_count > maximum:
                self._add(
                    issues,
                    "chunk_too_long",
                    f"token_count {chunk.token_count} exceeds {maximum}",
                    chunk.chunk_id,
                )
            if (
                isinstance(chunk, ChildChunk)
                and chunk.token_count < self.config.min_child_tokens
            ):
                self._add(
                    issues,
                    "child_too_short",
                    f"token_count {chunk.token_count} is below {self.config.min_child_tokens}; structure boundary prevented a safe merge",
                    chunk.chunk_id,
                    severity=ValidationSeverity.WARNING,
                )
            expected_breadcrumb = breadcrumb(cleaned.title, tuple(chunk.section_path))
            if chunk.breadcrumb != expected_breadcrumb:
                self._add(
                    issues,
                    "breadcrumb_mismatch",
                    "breadcrumb does not match title and section_path",
                    chunk.chunk_id,
                )
            if not chunk.breadcrumb.strip():
                self._add(
                    issues,
                    "breadcrumb_missing",
                    "breadcrumb is empty",
                    chunk.chunk_id,
                )
            if chunk.metadata.get("document_metadata") != cleaned.metadata:
                self._add(
                    issues,
                    "metadata_not_inherited",
                    "CleanedDocument metadata was not inherited losslessly",
                    chunk.chunk_id,
                )
            if chunk.doc_id != cleaned.document_id:
                self._add(
                    issues,
                    "doc_id_mismatch",
                    "chunk doc_id does not match CleanedDocument",
                    chunk.chunk_id,
                )
            if isinstance(chunk, ChildChunk):
                expected_retrieval_prefix = chunk.breadcrumb + "\n\n"
                if not chunk.retrieval_content.startswith(expected_retrieval_prefix):
                    self._add(
                        issues,
                        "retrieval_context_missing",
                        "retrieval_content does not start with deterministic breadcrumb",
                        chunk.chunk_id,
                    )
                if chunk.content not in chunk.retrieval_content:
                    self._add(
                        issues,
                        "retrieval_content_missing_original",
                        "retrieval_content does not preserve content",
                        chunk.chunk_id,
                    )

    @classmethod
    def _duplicates(
        cls,
        parents: list[ParentChunk],
        children: list[ChildChunk],
        issues: list[ChunkValidationIssue],
    ) -> None:
        ids = [chunk.chunk_id for chunk in [*parents, *children]]
        for chunk_id, count in Counter(ids).items():
            if count > 1:
                cls._add(
                    issues,
                    "duplicate_chunk_id",
                    f"chunk_id occurs {count} times",
                    chunk_id,
                )
        for level, chunks in (("parent", parents), ("child", children)):
            seen: dict[tuple[ChunkType, tuple[str, ...], str], str] = {}
            for chunk in chunks:
                key = (chunk.chunk_type, tuple(chunk.section_path), chunk.content)
                if key in seen:
                    cls._add(
                        issues,
                        "duplicate_chunk_content",
                        f"{level} content duplicates {seen[key]}",
                        chunk.chunk_id,
                        severity=ValidationSeverity.WARNING,
                    )
                else:
                    seen[key] = chunk.chunk_id

    @classmethod
    def _relationships(
        cls,
        parents: list[ParentChunk],
        children: list[ChildChunk],
        issues: list[ChunkValidationIssue],
    ) -> None:
        parent_ids = {parent.chunk_id for parent in parents}
        for index, child in enumerate(children):
            if child.parent_id not in parent_ids:
                cls._add(
                    issues,
                    "invalid_parent_id",
                    "child parent_id does not reference a parent chunk",
                    child.chunk_id,
                )
            expected_previous = children[index - 1].chunk_id if index else None
            expected_next = (
                children[index + 1].chunk_id if index + 1 < len(children) else None
            )
            if child.previous_chunk_id != expected_previous:
                cls._add(
                    issues,
                    "invalid_previous_chunk_id",
                    "previous_chunk_id does not match document order",
                    child.chunk_id,
                )
            if child.next_chunk_id != expected_next:
                cls._add(
                    issues,
                    "invalid_next_chunk_id",
                    "next_chunk_id does not match document order",
                    child.chunk_id,
                )

    @classmethod
    def _tables(
        cls,
        source_tables: list[DocumentTable],
        block_by_id: dict[str, DocumentBlock],
        children: list[ChildChunk],
        issues: list[ChunkValidationIssue],
    ) -> None:
        children_by_table: dict[str, list[ChildChunk]] = defaultdict(list)
        for child in children:
            if child.chunk_type is ChunkType.TABLE and child.table_data is not None:
                children_by_table[child.table_data.table_id].append(child)

        for table in source_tables:
            source_block_id = table.metadata.get("source_block_id")
            source_block = (
                block_by_id.get(source_block_id)
                if isinstance(source_block_id, str)
                else None
            )
            state = TableChunker.header_state(table, source_block)
            expected_headers = (
                list(table.rows[0])
                if table.rows and state is not TableHeaderState.NONE
                else []
            )
            data_start = 1 if expected_headers else 0
            expected_data = [list(row) for row in table.rows[data_start:]]
            emitted: list[tuple[int, list[str]]] = []
            fragments: dict[int, list[tuple[int, int, str]]] = defaultdict(list)
            table_children = children_by_table.get(table.table_id, [])
            if not table_children:
                cls._add(
                    issues,
                    "table_chunks_missing",
                    "active DocumentTable emitted no child chunks",
                    table_id=table.table_id,
                )
                continue
            for child in table_children:
                data = child.table_data
                assert data is not None
                if data.header_state is not state or data.headers != expected_headers:
                    cls._add(
                        issues,
                        "table_header_missing",
                        "table header state or values changed",
                        child.chunk_id,
                        table_id=table.table_id,
                    )
                compact_header = (
                    child.metadata.get("table_header_context_mode")
                    == "payload_and_schema"
                )
                if expected_headers and not compact_header:
                    expected_line = TableChunker._render_row(expected_headers)
                    if child.content.splitlines()[0] != expected_line:
                        cls._add(
                            issues,
                            "table_header_missing_from_content",
                            "table child content does not repeat its header",
                            child.chunk_id,
                            table_id=table.table_id,
                        )
                else:
                    column_count = max((len(row) for row in table.rows), default=0)
                    schema = TableChunker._schema(column_count)
                    if schema and schema not in child.retrieval_content:
                        cls._add(
                            issues,
                            "table_schema_context_missing",
                            "headerless table retrieval_content lacks synthetic schema",
                            child.chunk_id,
                            table_id=table.table_id,
                        )
                    if (
                        state is TableHeaderState.NONE
                        and schema
                        and child.content.startswith(schema)
                    ):
                        cls._add(
                            issues,
                            "synthetic_header_leaked_into_content",
                            "synthetic schema must only appear in retrieval_content",
                            child.chunk_id,
                            table_id=table.table_id,
                        )
                if data.row_fragments:
                    for fragment in data.row_fragments:
                        fragments[fragment.source_row_index].append(
                            (
                                fragment.fragment_index,
                                fragment.fragment_count,
                                fragment.content,
                            )
                        )
                    owning = [
                        fragment
                        for fragment in data.row_fragments
                        if fragment.owns_source_row
                    ]
                    if owning:
                        if len(owning) != 1 or len(data.rows) != 1:
                            cls._add(
                                issues,
                                "table_row_fragment_ownership_invalid",
                                "fragmented source row must have exactly one owning payload",
                                child.chunk_id,
                                table_id=table.table_id,
                            )
                        else:
                            emitted.append((owning[0].source_row_index, data.rows[0]))
                    elif data.rows:
                        cls._add(
                            issues,
                            "table_row_fragment_duplicate_payload",
                            "non-owning fragment unexpectedly carries source rows",
                            child.chunk_id,
                            table_id=table.table_id,
                        )
                else:
                    for offset, row in enumerate(data.rows, data.row_start):
                        emitted.append((offset, row))

            for source_row_index, values in fragments.items():
                ordered = sorted(values)
                indexes = [index for index, _, _ in ordered]
                declared_counts = {count for _, count, _ in ordered}
                if len(declared_counts) != 1 or indexes != list(
                    range(next(iter(declared_counts), 0))
                ):
                    cls._add(
                        issues,
                        "table_row_fragments_missing_or_duplicate",
                        "oversized row fragment sequence is incomplete",
                        table_id=table.table_id,
                    )
                    continue
                source_row = table.rows[source_row_index]
                if "".join(
                    content for _, _, content in ordered
                ) != TableChunker._render_row(source_row):
                    cls._add(
                        issues,
                        "table_row_fragments_changed",
                        "oversized row fragments do not reconstruct the source row",
                        table_id=table.table_id,
                    )

            emitted.sort(key=lambda item: item[0])
            positions = [position for position, _ in emitted]
            expected_positions = list(
                range(data_start, data_start + len(expected_data))
            )
            if positions != expected_positions:
                cls._add(
                    issues,
                    "table_rows_missing_or_duplicate",
                    "emitted table row positions are not exactly contiguous and unique",
                    table_id=table.table_id,
                )
            if [row for _, row in emitted] != expected_data:
                cls._add(
                    issues,
                    "table_rows_changed",
                    "emitted table rows differ from DocumentTable.rows",
                    table_id=table.table_id,
                )

    @staticmethod
    def _add(
        issues: list[ChunkValidationIssue],
        code: str,
        message: str,
        chunk_id: str | None = None,
        *,
        table_id: str | None = None,
        severity: ValidationSeverity = ValidationSeverity.ERROR,
    ) -> None:
        issues.append(
            ChunkValidationIssue(
                code=code,
                severity=severity,
                message=message,
                chunk_id=chunk_id,
                table_id=table_id,
            )
        )
