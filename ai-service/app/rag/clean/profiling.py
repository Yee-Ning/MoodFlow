"""Deterministic data profiling for the complete normalized corpus."""

from __future__ import annotations

import hashlib
import math
import statistics
from collections import Counter, defaultdict
from pathlib import Path
from typing import Iterable

from .config import ProfilingConfig
from .corpus import MISSING_DIMENSION, NormalizedDocumentContext
from .io import atomic_write_json


class NormalizedCorpusProfiler:
    def __init__(self, config: ProfilingConfig) -> None:
        self.config = config

    def profile(
        self, documents: Iterable[NormalizedDocumentContext]
    ) -> dict[str, object]:
        items = list(documents)
        lengths = [len(item.document.markdown) for item in items]
        exact_groups: defaultdict[str, list[str]] = defaultdict(list)
        line_documents: defaultdict[str, set[str]] = defaultdict(set)
        line_occurrences: Counter[str] = Counter()
        short_text_documents: defaultdict[str, set[str]] = defaultdict(set)
        short_text_occurrences: Counter[str] = Counter()
        prefix_documents: defaultdict[str, set[str]] = defaultdict(set)
        suffix_documents: defaultdict[str, set[str]] = defaultdict(set)
        sequence_documents: defaultdict[str, set[str]] = defaultdict(set)

        for item in items:
            document = item.document
            document_id = document.document_id
            text = document.markdown
            exact_hash = hashlib.sha256(text.encode("utf-8")).hexdigest()
            exact_groups[exact_hash].append(document_id)
            lines = [line.strip() for line in text.splitlines() if line.strip()]
            for line in lines:
                line_documents[line].add(document_id)
                line_occurrences[line] += 1
            for block in document.blocks:
                value = block.text.strip()
                if value and len(value) <= self.config.frequent_short_text_max_chars:
                    short_text_documents[value].add(document_id)
                    short_text_occurrences[value] += 1
            window = self.config.prefix_suffix_line_count
            if lines:
                prefix_documents["\n".join(lines[:window])].add(document_id)
                suffix_documents["\n".join(lines[-window:])].add(document_id)
            sequence_size = self.config.repeated_sequence_lines
            for index in range(max(0, len(lines) - sequence_size + 1)):
                sequence = "\n".join(lines[index : index + sequence_size])
                sequence_documents[sequence].add(document_id)

        source_counts = Counter(item.source for item in items)
        source_type_counts = Counter(
            item.source_type or MISSING_DIMENSION for item in items
        )
        document_type_counts = Counter(
            item.document_type or MISSING_DIMENSION for item in items
        )
        empty_documents = [
            item.document.document_id
            for item in items
            if not item.document.markdown.strip()
        ]
        short_documents = [
            {
                "document_id": item.document.document_id,
                "length": len(item.document.markdown),
            }
            for item in items
            if len(item.document.markdown) <= self.config.short_document_chars
        ]
        return {
            "profile_version": "v0.1",
            "document_count": len(items),
            "source_distribution": dict(sorted(source_counts.items())),
            "source_type_distribution": dict(sorted(source_type_counts.items())),
            "document_type_distribution": dict(
                sorted(document_type_counts.items())
            ),
            "missing_dimensions": {
                "source_type_documents": source_type_counts[MISSING_DIMENSION],
                "document_type_documents": document_type_counts[MISSING_DIMENSION],
            },
            "text_length": self._length_statistics(lengths),
            "empty_documents": empty_documents,
            "short_document_threshold": self.config.short_document_chars,
            "short_documents": short_documents,
            "exact_duplicate_groups": [
                {"sha256": digest, "document_ids": sorted(document_ids)}
                for digest, document_ids in sorted(exact_groups.items())
                if len(document_ids) > 1
            ],
            "frequent_lines": self._frequency_rows(
                line_documents,
                line_occurrences,
                self.config.frequent_line_min_documents,
            ),
            "frequent_short_texts": self._frequency_rows(
                short_text_documents,
                short_text_occurrences,
                self.config.frequent_short_text_min_documents,
            ),
            "frequent_prefixes": self._document_frequency_rows(
                prefix_documents, self.config.frequent_line_min_documents
            ),
            "frequent_suffixes": self._document_frequency_rows(
                suffix_documents, self.config.frequent_line_min_documents
            ),
            "repeated_line_sequences": self._document_frequency_rows(
                sequence_documents,
                self.config.repeated_sequence_min_documents,
            ),
            "documents": [
                {
                    "document_id": item.document.document_id,
                    "source_id": item.document.source_id,
                    "source": item.source,
                    "source_type": item.source_type,
                    "document_type": item.document_type,
                    "text_length": len(item.document.markdown),
                    "normalized_path": item.relative_normalized_path,
                }
                for item in items
            ],
            "interpretation_note": (
                "Frequency is a discovery signal, not proof of noise. Domain facts "
                "such as names, titles, dates, years, ranks, and organizations must "
                "not be removed solely because they repeat."
            ),
        }

    @staticmethod
    def _length_statistics(lengths: list[int]) -> dict[str, float | int]:
        if not lengths:
            return {"min": 0, "max": 0, "avg": 0.0, "median": 0.0, "p95": 0}
        ordered = sorted(lengths)
        p95_index = max(0, math.ceil(len(ordered) * 0.95) - 1)
        return {
            "min": ordered[0],
            "max": ordered[-1],
            "avg": round(statistics.fmean(ordered), 4),
            "median": statistics.median(ordered),
            "p95": ordered[p95_index],
        }

    def _frequency_rows(
        self,
        document_sets: dict[str, set[str]],
        occurrence_counts: Counter[str],
        minimum_documents: int,
    ) -> list[dict[str, object]]:
        rows = [
            self._frequency_row(
                value,
                document_ids,
                occurrence_count=occurrence_counts[value],
            )
            for value, document_ids in document_sets.items()
            if len(document_ids) >= minimum_documents
        ]
        rows.sort(
            key=lambda row: (
                -int(row["document_count"]),
                -int(row["occurrence_count"]),
                str(row["sha256"]),
            )
        )
        return rows[: self.config.top_k]

    def _document_frequency_rows(
        self,
        document_sets: dict[str, set[str]],
        minimum_documents: int,
    ) -> list[dict[str, object]]:
        rows = [
            self._frequency_row(value, document_ids)
            for value, document_ids in document_sets.items()
            if len(document_ids) >= minimum_documents
        ]
        rows.sort(
            key=lambda row: (-int(row["document_count"]), str(row["sha256"]))
        )
        return rows[: self.config.top_k]

    def _frequency_row(
        self,
        value: str,
        document_ids: set[str],
        *,
        occurrence_count: int | None = None,
    ) -> dict[str, object]:
        truncated = len(value) > self.config.max_example_chars
        row: dict[str, object] = {
            "text": (
                value[: self.config.max_example_chars] + "…" if truncated else value
            ),
            "sha256": hashlib.sha256(value.encode("utf-8")).hexdigest(),
            "document_count": len(document_ids),
            "document_ids": sorted(document_ids),
            "truncated": truncated,
        }
        if occurrence_count is not None:
            row["occurrence_count"] = occurrence_count
        return row


def write_profile_report(report: dict[str, object], output_path: Path) -> None:
    atomic_write_json(output_path, report)
