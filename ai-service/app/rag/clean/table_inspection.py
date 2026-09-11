"""Lightweight, read-only table triage for normalized documents."""

from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass
from pathlib import Path

from app.rag.parse_normalize.models import BlockType, DocumentBlock, DocumentTable
from app.rag.parse_normalize.table_support import (
    extract_html_table,
    normalized_table_rows,
)

from .corpus import NormalizedDocumentContext
from .io import atomic_write_json, atomic_write_jsonl


SEMANTIC_CANDIDATE = "semantic_candidate"
LAYOUT_CANDIDATE = "layout_candidate"
UNCERTAIN = "uncertain"


@dataclass(frozen=True, slots=True)
class TableInspectionConfig:
    """Named conservative thresholds used only to prioritize human review."""

    high_empty_cell_ratio: float = 0.60
    long_cell_chars: int = 500
    minimum_grid_consistency: float = 0.75
    preview_chars: int = 240


@dataclass(frozen=True, slots=True)
class TableInspectionPaths:
    summary: Path
    manifest: Path


class NormalizedTableInspector:
    def __init__(self, config: TableInspectionConfig | None = None) -> None:
        self.config = config or TableInspectionConfig()

    def inspect(
        self, documents: list[NormalizedDocumentContext]
    ) -> dict[str, object]:
        tables: list[dict[str, object]] = []
        documents_with_tables: set[str] = set()
        review_documents: set[str] = set()
        for context in documents:
            rows = self._inspect_document(context)
            if rows:
                documents_with_tables.add(context.document.document_id)
            if any(row["classification"] != SEMANTIC_CANDIDATE for row in rows):
                review_documents.add(context.document.document_id)
            tables.extend(rows)
        classifications = Counter(str(row["classification"]) for row in tables)
        return {
            "report_version": "v0.1",
            "document_count": len(documents),
            "documents_with_tables": len(documents_with_tables),
            "table_count": len(tables),
            "classification_statistics": dict(sorted(classifications.items())),
            "review_document_count": len(review_documents),
            "policy": {
                "mutates_normalized_or_cleaned_data": False,
                "automatic_table_deletion": False,
                "classification_is_review_hint_only": True,
            },
            "tables": tables,
        }

    def _inspect_document(
        self, context: NormalizedDocumentContext
    ) -> list[dict[str, object]]:
        document = context.document
        top_level_by_block = {
            str(table.metadata.get("source_block_id")): table
            for table in document.tables
            if table.metadata.get("source_block_id")
        }
        used_table_ids: set[str] = set()
        rows: list[dict[str, object]] = []
        for block in document.blocks:
            if block.block_type is not BlockType.TABLE:
                continue
            table = top_level_by_block.get(block.block_id)
            if table is not None:
                used_table_ids.add(table.table_id)
            rows.append(self._row(context, block, table))
        for table in document.tables:
            if table.table_id in used_table_ids:
                continue
            rows.append(self._row(context, None, table))
        return rows

    def _row(
        self,
        context: NormalizedDocumentContext,
        block: DocumentBlock | None,
        table: DocumentTable | None,
    ) -> dict[str, object]:
        block_rows = normalized_table_rows(
            block.metadata.get("rows") if block is not None else []
        )
        table_rows = normalized_table_rows(table.rows if table is not None else [])
        rows = block_rows or table_rows
        raw_text = block.text if block is not None else (table.markdown if table else "")
        html_rows, html_has_header = extract_html_table(raw_text)
        if not rows:
            rows = html_rows
        has_header = html_has_header or self._bool_metadata(
            block, table, "has_header"
        )
        hint = self._string_metadata(block, table, "table_kind_hint")
        statistics = self._statistics(rows)
        classification, reasons = self._classify(
            statistics, has_header=has_header, hint=hint
        )
        document = context.document
        return {
            "document_id": document.document_id,
            "source_id": document.source_id,
            "source": context.source,
            "document_type": context.document_type,
            "table_id": (
                table.table_id
                if table is not None
                else block.block_id if block is not None else "unknown"
            ),
            "source_block_id": block.block_id if block is not None else None,
            "parser_name": document.parser_name,
            "page_number": table.page_number if table is not None else None,
            "classification": classification,
            "reasons": reasons,
            "has_header": has_header,
            **statistics,
            "preview": self._preview(rows, raw_text),
        }

    def _classify(
        self,
        statistics: dict[str, int | float],
        *,
        has_header: bool,
        hint: str | None,
    ) -> tuple[str, list[str]]:
        row_count = int(statistics["row_count"])
        column_count = int(statistics["column_count"])
        empty_cell_ratio = float(statistics["empty_cell_ratio"])
        max_cell_chars = int(statistics["max_cell_chars"])
        grid_consistency = float(statistics["grid_consistency"])
        if not row_count or not column_count:
            return UNCERTAIN, ["structured_rows_unavailable"]
        if has_header or hint == SEMANTIC_CANDIDATE:
            return SEMANTIC_CANDIDATE, ["explicit_table_header"]
        layout_reasons: list[str] = []
        if row_count == 1:
            layout_reasons.append("single_row_table")
        if empty_cell_ratio >= self.config.high_empty_cell_ratio:
            layout_reasons.append("many_empty_cells")
        if max_cell_chars >= self.config.long_cell_chars:
            layout_reasons.append("very_long_cell")
        if layout_reasons:
            return LAYOUT_CANDIDATE, layout_reasons
        if (
            row_count >= 2
            and column_count >= 2
            and grid_consistency >= self.config.minimum_grid_consistency
        ):
            return SEMANTIC_CANDIDATE, ["consistent_multi_row_grid"]
        return UNCERTAIN, ["weak_or_irregular_table_structure"]

    @staticmethod
    def _statistics(rows: list[list[str]]) -> dict[str, int | float]:
        if not rows:
            return {
                "row_count": 0,
                "column_count": 0,
                "empty_cell_ratio": 0.0,
                "grid_consistency": 0.0,
                "max_cell_chars": 0,
            }
        column_count = max(len(row) for row in rows)
        total_cells = len(rows) * column_count
        non_empty = sum(bool(cell.strip()) for row in rows for cell in row)
        consistent_rows = sum(len(row) == column_count for row in rows)
        return {
            "row_count": len(rows),
            "column_count": column_count,
            "empty_cell_ratio": round(
                1 - (non_empty / total_cells) if total_cells else 0.0, 6
            ),
            "grid_consistency": round(consistent_rows / len(rows), 6),
            "max_cell_chars": max(
                (len(cell) for row in rows for cell in row), default=0
            ),
        }

    def _preview(self, rows: list[list[str]], raw_text: str) -> str:
        if rows:
            value = " / ".join(
                " | ".join(cell.strip() for cell in row if cell.strip())
                for row in rows[:3]
            )
        else:
            value = re.sub(r"\s+", " ", raw_text).strip()
        return value[: self.config.preview_chars]

    @staticmethod
    def _bool_metadata(
        block: DocumentBlock | None, table: DocumentTable | None, key: str
    ) -> bool:
        return any(
            value is True
            for value in (
                block.metadata.get(key) if block is not None else None,
                table.metadata.get(key) if table is not None else None,
            )
        )

    @staticmethod
    def _string_metadata(
        block: DocumentBlock | None, table: DocumentTable | None, key: str
    ) -> str | None:
        for value in (
            block.metadata.get(key) if block is not None else None,
            table.metadata.get(key) if table is not None else None,
        ):
            if isinstance(value, str) and value:
                return value
        return None


def write_table_inspection_report(
    report: dict[str, object], output: Path
) -> TableInspectionPaths:
    """Write one compact summary plus an inspectable per-table JSONL manifest."""

    raw_tables = report.get("tables", [])
    tables = raw_tables if isinstance(raw_tables, list) else []
    manifest = output.with_name("table_manifest.jsonl")
    summary = {key: value for key, value in report.items() if key != "tables"}
    summary["table_manifest"] = manifest.name
    atomic_write_json(output, summary)
    atomic_write_jsonl(manifest, tables)
    return TableInspectionPaths(summary=output, manifest=manifest)
