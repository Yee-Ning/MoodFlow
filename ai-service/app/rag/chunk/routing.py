"""Structure-aware routing from normalized Blocks to narrative/table streams."""

from __future__ import annotations

from dataclasses import dataclass

from app.rag.parse_normalize.models import (
    BlockType,
    DocumentBlock,
    DocumentTable,
    NormalizedDocument,
)

from .internal import NarrativePart, NarrativeRun, RoutedRecord, RoutedTable


class ChunkRoutingError(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class ChunkRoutes:
    narrative_runs: tuple[NarrativeRun, ...]
    tables: tuple[RoutedTable, ...]
    active_table_ids: frozenset[str]
    records: tuple[RoutedRecord, ...] = ()


class ChunkRouter:
    def route(
        self,
        document: NormalizedDocument,
        projected: dict[str, str],
    ) -> ChunkRoutes:
        block_by_id = {block.block_id: block for block in document.blocks}
        table_by_block = self._tables_by_block(document.tables)
        narrative_runs: list[NarrativeRun] = []
        routed_tables: list[RoutedTable] = []
        active_table_ids: set[str] = set()
        pending: list[NarrativePart] = []
        pending_path: tuple[str, ...] | None = None
        fallback_headings: dict[int, str] = {}
        record_parts: dict[str, list[NarrativePart]] = {}
        record_paths: dict[str, tuple[str, ...]] = {}
        previous_record_id = None

        def flush() -> None:
            nonlocal pending, pending_path
            if pending:
                narrative_runs.append(
                    NarrativeRun(
                        order_index=pending[0].order_index,
                        section_path=pending_path or (),
                        parts=tuple(pending),
                    )
                )
            pending = []
            pending_path = None

        for block in document.blocks:
            record_id = block.metadata.get("record_id")
            if record_id is not None:
                if not isinstance(record_id, str) or not record_id.strip():
                    raise ChunkRoutingError("structured_record_id_invalid")
                if block.block_type is BlockType.TABLE:
                    raise ChunkRoutingError("structured_record_contains_table_block")
                flush()
                if record_id in record_parts and previous_record_id != record_id:
                    raise ChunkRoutingError(f"structured_record_noncontiguous:{record_id}")
                content = projected.get(block.block_id, "").strip()
                if not content:
                    raise ChunkRoutingError(f"structured_record_projection_missing:{record_id}")
                record_paths.setdefault(record_id, self._section_path(
                    block, block_by_id, fallback_headings, projected
                ))
                record_parts.setdefault(record_id, []).append(NarrativePart(
                    order_index=block.order_index, block_id=block.block_id,
                    block_type=block.block_type.value, content=content,
                    metadata=block.metadata,
                ))
                previous_record_id = record_id
                continue
            previous_record_id = None
            if block.block_type is BlockType.HEADING:
                flush()
                if projected.get(block.block_id, "").strip():
                    level = block.level or 1
                    for candidate in list(fallback_headings):
                        if candidate >= level:
                            del fallback_headings[candidate]
                    fallback_headings[level] = block.block_id
                continue

            path = self._section_path(block, block_by_id, fallback_headings, projected)
            if block.block_type is BlockType.TABLE:
                flush()
                if not projected.get(block.block_id, "").strip():
                    continue
                table = table_by_block.get(block.block_id)
                if table is None:
                    raise ChunkRoutingError(
                        f"table_block_missing_document_table:{block.block_id}"
                    )
                routed_tables.append(
                    RoutedTable(
                        order_index=block.order_index,
                        section_path=path,
                        source_block_id=block.block_id,
                        table_id=table.table_id,
                    )
                )
                active_table_ids.add(table.table_id)
                continue

            content = projected.get(block.block_id, "").strip()
            if not content:
                continue
            if pending_path != path:
                flush()
                pending_path = path
            pending.append(
                NarrativePart(
                    order_index=block.order_index,
                    block_id=block.block_id,
                    block_type=block.block_type.value,
                    content=content,
                    metadata=block.metadata,
                )
            )
        flush()

        next_order = len(document.blocks)
        routed_ids = {item.table_id for item in routed_tables}
        for table in document.tables:
            if table.table_id in routed_ids:
                continue
            source_block_id = self._source_block_id(table)
            if source_block_id is not None and source_block_id in block_by_id:
                continue
            routed_tables.append(
                RoutedTable(
                    order_index=next_order,
                    section_path=(),
                    source_block_id=source_block_id,
                    table_id=table.table_id,
                )
            )
            active_table_ids.add(table.table_id)
            next_order += 1

        return ChunkRoutes(
            narrative_runs=tuple(narrative_runs),
            tables=tuple(routed_tables),
            active_table_ids=frozenset(active_table_ids),
            records=tuple(RoutedRecord(
                order_index=parts[0].order_index, record_id=record_id,
                section_path=record_paths[record_id], parts=tuple(parts),
            ) for record_id, parts in record_parts.items()),
        )

    @classmethod
    def _tables_by_block(cls, tables: list[DocumentTable]) -> dict[str, DocumentTable]:
        mapped: dict[str, DocumentTable] = {}
        for table in tables:
            source_block_id = cls._source_block_id(table) or table.table_id
            if source_block_id in mapped:
                raise ChunkRoutingError(
                    f"duplicate_document_table_source_block:{source_block_id}"
                )
            mapped[source_block_id] = table
        return mapped

    @staticmethod
    def _source_block_id(table: DocumentTable) -> str | None:
        value = table.metadata.get("source_block_id")
        return value if isinstance(value, str) and value else None

    @classmethod
    def _section_path(
        cls,
        block: DocumentBlock,
        block_by_id: dict[str, DocumentBlock],
        fallback_headings: dict[int, str],
        projected: dict[str, str],
    ) -> tuple[str, ...]:
        if block.metadata.get("heading_assignment") == "position_unavailable":
            return ()
        explicit: list[DocumentBlock] = []
        seen: set[str] = set()
        parent_id = block.parent_block_id
        while parent_id and parent_id not in seen:
            seen.add(parent_id)
            parent = block_by_id.get(parent_id)
            if parent is None:
                break
            if (
                parent.block_type is BlockType.HEADING
                and projected.get(parent.block_id, "").strip()
            ):
                explicit.append(parent)
            parent_id = parent.parent_block_id
        if explicit:
            return tuple(
                item.text.strip() for item in reversed(explicit) if item.text.strip()
            )
        return tuple(
            block_by_id[block_id].text.strip()
            for _, block_id in sorted(fallback_headings.items())
            if block_by_id[block_id].text.strip()
        )
