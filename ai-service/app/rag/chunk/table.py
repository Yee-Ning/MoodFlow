"""Row-preserving chunking that consumes DocumentTable directly."""

from __future__ import annotations

from collections.abc import Sequence

from pydantic import JsonValue

from app.rag.parse_normalize.models import DocumentBlock, DocumentTable

from .config import ChunkConfig
from .context import breadcrumb
from .internal import ChildDraft, ParentDraft, RoutedTable
from .models import ChunkType, TableChunkData, TableHeaderState
from .models import TableRowFragment
from .text_splitter import ChineseRecursiveSplitter
from .token_counter import TokenCounter


class TableChunker:
    def __init__(self, config: ChunkConfig, counter: TokenCounter) -> None:
        self.config = config
        self.counter = counter
        self.splitter = ChineseRecursiveSplitter(config, counter)

    def chunk(
        self,
        routed: RoutedTable,
        table: DocumentTable,
        *,
        source_block: DocumentBlock | None,
        document_title: str | None,
        base_metadata: dict[str, JsonValue],
    ) -> list[ParentDraft]:
        if not table.rows:
            return []
        state = self.header_state(table, source_block)
        headers = list(table.rows[0]) if state is not TableHeaderState.NONE else []
        data_start = 1 if headers else 0
        data_rows = [list(row) for row in table.rows[data_start:]]
        column_count = max((len(row) for row in table.rows), default=0)
        compact_header = bool(
            headers
            and self.counter.count(self._render(headers, []))
            >= self.config.target_child_tokens
        )
        render_headers = [] if compact_header else headers
        schema = (
            self._schema(column_count)
            if state is TableHeaderState.NONE or compact_header
            else None
        )
        parent_groups = self._group_rows(
            data_rows,
            row_offset=data_start,
            headers=render_headers,
            schema=schema,
            target=self.config.target_parent_tokens,
            maximum=self.config.max_parent_tokens,
        )
        if not data_rows:
            parent_groups = [(data_start, [])]

        parents: list[ParentDraft] = []
        for row_start, rows in parent_groups:
            if (
                len(rows) == 1
                and self._context_token_count(render_headers, rows, schema)
                > self.config.max_parent_tokens
            ):
                parents.extend(
                    self._chunk_oversized_row(
                        routed,
                        table,
                        row=rows[0],
                        source_row_index=row_start,
                        state=state,
                        headers=headers,
                        render_headers=render_headers,
                        schema=schema,
                        document_title=document_title,
                        metadata={
                            **base_metadata,
                            "table_id": table.table_id,
                            "source_block_id": routed.source_block_id,
                            "table_page_number": table.page_number,
                            "table_bbox": (
                                list(table.bbox) if table.bbox is not None else None
                            ),
                            "table_metadata": table.metadata,
                            "table_header_state": state.value,
                            "table_header_context_mode": (
                                "payload_and_schema" if compact_header else "content"
                            ),
                        },
                        parent_offset=len(parents),
                    )
                )
                continue
            row_end = row_start + len(rows)
            content = self._render(render_headers, rows)
            if not content.strip() or self.counter.count(content) == 0:
                continue
            parent_table_data = TableChunkData(
                table_id=table.table_id,
                header_state=state,
                headers=headers,
                rows=rows,
                row_start=row_start,
                row_end=row_end,
                source_row_count=len(table.rows),
            )
            metadata: dict[str, JsonValue] = {
                **base_metadata,
                "table_id": table.table_id,
                "source_block_id": routed.source_block_id,
                "table_page_number": table.page_number,
                "table_bbox": list(table.bbox) if table.bbox is not None else None,
                "table_metadata": table.metadata,
                "table_header_state": state.value,
                "table_header_context_mode": (
                    "payload_and_schema" if compact_header else "content"
                ),
            }
            child_groups = self._group_rows(
                rows,
                row_offset=row_start,
                headers=render_headers,
                schema=schema,
                target=self.config.target_child_tokens,
                maximum=self.config.max_child_tokens,
            )
            if not rows:
                child_groups = [(row_start, [])]
            child_groups = self._merge_small_groups(
                child_groups, headers=render_headers, schema=schema
            )
            children: list[ChildDraft] = []
            for child_start, child_rows in child_groups:
                if (
                    len(child_rows) == 1
                    and self._context_token_count(render_headers, child_rows, schema)
                    > self.config.max_child_tokens
                ):
                    children.extend(
                        self._fragment_child_drafts(
                            table,
                            row=child_rows[0],
                            source_row_index=child_start,
                            state=state,
                            headers=headers,
                            render_headers=render_headers,
                            schema=schema,
                            metadata=metadata,
                        )
                    )
                    continue
                child_content = self._render(render_headers, child_rows)
                if not child_content.strip() or self.counter.count(child_content) == 0:
                    continue
                child_data = TableChunkData(
                    table_id=table.table_id,
                    header_state=state,
                    headers=headers,
                    rows=child_rows,
                    row_start=child_start,
                    row_end=child_start + len(child_rows),
                    source_row_count=len(table.rows),
                )
                children.append(
                    ChildDraft(
                        content=child_content,
                        metadata=dict(metadata),
                        table_data=child_data,
                    )
                )
            parents.append(
                ParentDraft(
                    order_key=(routed.order_index, len(parents)),
                    chunk_type=ChunkType.TABLE,
                    section_path=routed.section_path,
                    breadcrumb=breadcrumb(document_title, routed.section_path),
                    content=content,
                    metadata=metadata,
                    table_data=parent_table_data,
                    children=children,
                )
            )
        return parents

    def _fragment_child_drafts(
        self,
        table: DocumentTable,
        *,
        row: list[str],
        source_row_index: int,
        state: TableHeaderState,
        headers: list[str],
        render_headers: list[str],
        schema: str | None,
        metadata: dict[str, JsonValue],
    ) -> list[ChildDraft]:
        prefix = self._render(render_headers, [])
        prefix = f"{prefix}\n" if prefix else ""
        sizing_context = "\n".join(
            value for value in (prefix.rstrip("\n"), schema) if value
        )
        budget = max(
            1,
            self.config.target_child_tokens - self.counter.count(sizing_context),
        )
        pieces = self.splitter.split_table_text(
            self._render_row(row), chunk_size=budget
        )
        pieces = self._merge_text_fragments(
            pieces,
            prefix=prefix,
            schema=schema,
            maximum=self.config.max_child_tokens,
        )
        children: list[ChildDraft] = []
        for fragment_index, piece in enumerate(pieces):
            fragment = TableRowFragment(
                source_row_index=source_row_index,
                fragment_index=fragment_index,
                fragment_count=len(pieces),
                content=piece,
                owns_source_row=fragment_index == 0,
            )
            children.append(
                ChildDraft(
                    content=prefix + piece,
                    metadata={**metadata, "oversized_table_row_fragment": True},
                    table_data=TableChunkData(
                        table_id=table.table_id,
                        header_state=state,
                        headers=headers,
                        rows=[row] if fragment.owns_source_row else [],
                        row_fragments=[fragment],
                        row_start=source_row_index,
                        row_end=source_row_index + 1,
                        source_row_count=len(table.rows),
                    ),
                )
            )
        return children

    def _chunk_oversized_row(
        self,
        routed: RoutedTable,
        table: DocumentTable,
        *,
        row: list[str],
        source_row_index: int,
        state: TableHeaderState,
        headers: list[str],
        render_headers: list[str],
        schema: str | None,
        document_title: str | None,
        metadata: dict[str, JsonValue],
        parent_offset: int,
    ) -> list[ParentDraft]:
        prefix = self._render(render_headers, [])
        prefix = f"{prefix}\n" if prefix else ""
        sizing_context = "\n".join(
            value for value in (prefix.rstrip("\n"), schema) if value
        )
        child_budget = max(
            1,
            self.config.target_child_tokens - self.counter.count(sizing_context),
        )
        rendered_row = self._render_row(row)
        pieces = self.splitter.split_table_text(rendered_row, chunk_size=child_budget)
        pieces = self._merge_text_fragments(
            pieces,
            prefix=prefix,
            schema=schema,
            maximum=self.config.max_child_tokens,
        )
        parent_piece_groups: list[list[tuple[int, str]]] = []
        current: list[tuple[int, str]] = []
        for fragment_index, piece in enumerate(pieces):
            candidate = "".join(
                value for _, value in [*current, (fragment_index, piece)]
            )
            candidate_content = prefix + candidate
            if (
                current
                and self.counter.count(candidate_content)
                > self.config.target_parent_tokens
            ):
                parent_piece_groups.append(current)
                current = []
            current.append((fragment_index, piece))
        if current:
            parent_piece_groups.append(current)

        parents: list[ParentDraft] = []
        for group_offset, piece_group in enumerate(parent_piece_groups):
            group_fragments = [
                TableRowFragment(
                    source_row_index=source_row_index,
                    fragment_index=fragment_index,
                    fragment_count=len(pieces),
                    content=piece,
                    owns_source_row=fragment_index == 0,
                )
                for fragment_index, piece in piece_group
            ]
            parent_data = TableChunkData(
                table_id=table.table_id,
                header_state=state,
                headers=headers,
                rows=[row]
                if any(value.owns_source_row for value in group_fragments)
                else [],
                row_fragments=group_fragments,
                row_start=source_row_index,
                row_end=source_row_index + 1,
                source_row_count=len(table.rows),
            )
            children = []
            for fragment in group_fragments:
                child_data = TableChunkData(
                    table_id=table.table_id,
                    header_state=state,
                    headers=headers,
                    rows=[row] if fragment.owns_source_row else [],
                    row_fragments=[fragment],
                    row_start=source_row_index,
                    row_end=source_row_index + 1,
                    source_row_count=len(table.rows),
                )
                children.append(
                    ChildDraft(
                        content=prefix + fragment.content,
                        metadata={**metadata, "oversized_table_row_fragment": True},
                        table_data=child_data,
                    )
                )
            parents.append(
                ParentDraft(
                    order_key=(routed.order_index, parent_offset + group_offset),
                    chunk_type=ChunkType.TABLE,
                    section_path=routed.section_path,
                    breadcrumb=breadcrumb(document_title, routed.section_path),
                    content=prefix
                    + "".join(value.content for value in group_fragments),
                    metadata={**metadata, "oversized_table_row_fragment": True},
                    table_data=parent_data,
                    children=children,
                )
            )
        return parents

    def _merge_text_fragments(
        self,
        pieces: list[str],
        *,
        prefix: str,
        schema: str | None,
        maximum: int,
    ) -> list[str]:
        values = list(pieces)
        index = 0
        while index < len(values):
            context = "\n".join(
                value for value in (schema, prefix + values[index]) if value
            )
            if self.counter.count(context) >= self.config.min_child_tokens:
                index += 1
                continue
            if index > 0:
                combined = values[index - 1] + values[index]
                combined_context = "\n".join(
                    value for value in (schema, prefix + combined) if value
                )
                if self.counter.count(combined_context) <= maximum:
                    values[index - 1] = combined
                    del values[index]
                    continue
            index += 1
        return values

    @staticmethod
    def header_state(
        table: DocumentTable, source_block: DocumentBlock | None = None
    ) -> TableHeaderState:
        metadata = dict(source_block.metadata) if source_block is not None else {}
        metadata.update(table.metadata)
        raw_state = metadata.get("header_state", metadata.get("header_status"))
        if isinstance(raw_state, str):
            normalized = raw_state.casefold().strip()
            if normalized in {"inferred", "inferred_header", "guessed"}:
                return TableHeaderState.INFERRED
            if normalized in {"explicit", "normal", "provided"}:
                return TableHeaderState.EXPLICIT
            if normalized in {"none", "no_header", "headerless"}:
                return TableHeaderState.NONE
        if metadata.get("has_header") is True:
            if metadata.get("header_inferred") is True:
                return TableHeaderState.INFERRED
            return TableHeaderState.EXPLICIT
        return TableHeaderState.NONE

    def _group_rows(
        self,
        rows: list[list[str]],
        *,
        row_offset: int,
        headers: list[str],
        schema: str | None,
        target: int,
        maximum: int,
    ) -> list[tuple[int, list[list[str]]]]:
        groups: list[tuple[int, list[list[str]]]] = []
        current: list[list[str]] = []
        current_start = row_offset
        for source_index, row in enumerate(rows, row_offset):
            candidate = [*current, row]
            candidate_count = self._context_token_count(headers, candidate, schema)
            if current and candidate_count > target:
                groups.append((current_start, current))
                current = []
                current_start = source_index
                candidate = [row]
                candidate_count = self._context_token_count(headers, candidate, schema)
            if current and candidate_count > maximum:
                groups.append((current_start, current))
                current = []
                current_start = source_index
            current.append(list(row))
        if current:
            groups.append((current_start, current))
        return groups

    def _merge_small_groups(
        self,
        groups: list[tuple[int, list[list[str]]]],
        *,
        headers: list[str],
        schema: str | None,
    ) -> list[tuple[int, list[list[str]]]]:
        merged = [(start, list(rows)) for start, rows in groups]
        index = 0
        while index < len(merged):
            start, rows = merged[index]
            count = self._context_token_count(headers, rows, schema)
            if count >= self.config.min_child_tokens:
                index += 1
                continue
            if index > 0:
                previous_start, previous_rows = merged[index - 1]
                combined = [*previous_rows, *rows]
                if (
                    self._context_token_count(headers, combined, schema)
                    <= self.config.max_child_tokens
                ):
                    merged[index - 1] = (previous_start, combined)
                    del merged[index]
                    continue
            if index + 1 < len(merged):
                next_start, next_rows = merged[index + 1]
                combined = [*rows, *next_rows]
                if (
                    self._context_token_count(headers, combined, schema)
                    <= self.config.max_child_tokens
                ):
                    merged[index] = (start, combined)
                    del merged[index + 1]
                    continue
            index += 1
        return merged

    def _context_token_count(
        self, headers: list[str], rows: list[list[str]], schema: str | None
    ) -> int:
        content = self._render(headers, rows)
        if schema:
            content = f"{schema}\n{content}" if content else schema
        return self.counter.count(content)

    @classmethod
    def _render(cls, headers: Sequence[str], rows: Sequence[Sequence[str]]) -> str:
        values: list[Sequence[str]] = []
        if headers:
            values.append(headers)
        values.extend(rows)
        return "\n".join(cls._render_row(row) for row in values)

    @classmethod
    def _render_row(cls, row: Sequence[str]) -> str:
        return "| " + " | ".join(cls._render_cell(cell) for cell in row) + " |"

    @staticmethod
    def _render_cell(cell: str) -> str:
        return (
            cell.replace("|", "\\|")
            .replace("\r\n", "<br>")
            .replace("\r", "<br>")
            .replace("\n", "<br>")
        )

    @staticmethod
    def _schema(column_count: int) -> str:
        return " | ".join(f"col_{index}" for index in range(1, column_count + 1))
