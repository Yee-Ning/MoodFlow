"""Lightweight, deterministic parse quality evaluation."""

from __future__ import annotations

import unicodedata
from dataclasses import dataclass

from .models import (
    BlockType,
    DocumentBlock,
    ParseMetrics,
    ParseReport,
    ParseStatus,
)


@dataclass(frozen=True, slots=True)
class ParseQualityConfig:
    warning_score: float = 0.65
    garbled_char_warning_ratio: float = 0.02
    duplicated_line_warning_ratio: float = 0.35
    empty_penalty: float = 1.0
    garbled_penalty_weight: float = 4.0
    duplication_penalty_weight: float = 0.75

    def __post_init__(self) -> None:
        for name in (
            "warning_score",
            "garbled_char_warning_ratio",
            "duplicated_line_warning_ratio",
            "empty_penalty",
        ):
            value = getattr(self, name)
            if not 0 <= value <= 1:
                raise ValueError(f"{name} must be between zero and one")
        if self.garbled_penalty_weight < 0 or self.duplication_penalty_weight < 0:
            raise ValueError("quality penalty weights must be non-negative")


class ParseQualityEvaluator:
    def __init__(self, config: ParseQualityConfig | None = None) -> None:
        self.config = config or ParseQualityConfig()

    def evaluate(
        self,
        source_id: str,
        parser_name: str,
        blocks: list[DocumentBlock],
        elapsed_ms: float,
    ) -> ParseReport:
        text = "\n".join(block.text for block in blocks)
        text_length = len(text)
        non_empty_lines = [line.strip() for line in text.splitlines() if line.strip()]
        duplicated_line_ratio = (
            (len(non_empty_lines) - len(set(non_empty_lines))) / len(non_empty_lines)
            if non_empty_lines
            else 0.0
        )
        garbled_count = sum(1 for character in text if self._is_garbled(character))
        garbled_char_ratio = garbled_count / text_length if text_length else 0.0
        empty_content = not text.strip()
        metrics = ParseMetrics(
            text_length=text_length,
            heading_count=sum(
                block.block_type is BlockType.HEADING for block in blocks
            ),
            paragraph_count=sum(
                block.block_type is BlockType.PARAGRAPH for block in blocks
            ),
            table_count=sum(block.block_type is BlockType.TABLE for block in blocks),
            empty_content=empty_content,
            garbled_char_ratio=garbled_char_ratio,
            duplicated_line_ratio=duplicated_line_ratio,
        )
        warnings: list[str] = []
        if empty_content:
            warnings.append("empty_content")
        if garbled_char_ratio > self.config.garbled_char_warning_ratio:
            warnings.append("high_garbled_char_ratio")
        if duplicated_line_ratio > self.config.duplicated_line_warning_ratio:
            warnings.append("high_duplicated_line_ratio")
        score = 1.0
        if empty_content:
            score -= self.config.empty_penalty
        score -= garbled_char_ratio * self.config.garbled_penalty_weight
        score -= duplicated_line_ratio * self.config.duplication_penalty_weight
        quality_score = round(max(0.0, min(1.0, score)), 4)
        if empty_content:
            status = ParseStatus.FAILED
        elif quality_score < self.config.warning_score or warnings:
            status = ParseStatus.WARNING
        else:
            status = ParseStatus.PASS
        return ParseReport(
            source_id=source_id,
            parser_name=parser_name,
            quality_score=quality_score,
            status=status,
            warnings=warnings,
            metrics=metrics,
            elapsed_ms=max(0.0, elapsed_ms),
        )

    def failure(
        self,
        source_id: str,
        parser_name: str,
        warning: str,
        elapsed_ms: float,
    ) -> ParseReport:
        return ParseReport(
            source_id=source_id,
            parser_name=parser_name,
            quality_score=0,
            status=ParseStatus.FAILED,
            warnings=[warning],
            metrics=ParseMetrics(
                text_length=0,
                heading_count=0,
                paragraph_count=0,
                table_count=0,
                empty_content=True,
                garbled_char_ratio=0,
                duplicated_line_ratio=0,
            ),
            elapsed_ms=max(0.0, elapsed_ms),
        )

    @staticmethod
    def _is_garbled(character: str) -> bool:
        if character == "\ufffd":
            return True
        category = unicodedata.category(character)
        if category == "Co":
            return True
        return category == "Cc" and character not in "\n\r\t"
