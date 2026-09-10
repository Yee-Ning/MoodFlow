"""PDF-specific deterministic parse quality metrics and scoring."""

from __future__ import annotations

import unicodedata
from dataclasses import dataclass

from pydantic import BaseModel, ConfigDict

from app.rag.parse_normalize.models import (
    BlockType,
    ParseMetrics,
    ParserResult,
)

from .models import PDFInspection


@dataclass(frozen=True, slots=True)
class PDFParseQualityConfig:
    quality_pass_threshold: float = 0.78
    quality_fallback_threshold: float = 0.35
    minimum_chars_per_page: float = 40.0
    suspicious_line_max_chars: int = 20
    garbled_warning_ratio: float = 0.02
    replacement_warning_ratio: float = 0.01
    duplicated_line_warning_ratio: float = 0.35
    suspicious_line_break_warning_ratio: float = 0.65
    chars_per_page_penalty_weight: float = 0.50
    empty_page_penalty_weight: float = 0.30
    garbled_penalty_weight: float = 3.0
    replacement_penalty_weight: float = 5.0
    duplication_penalty_weight: float = 0.50
    suspicious_line_break_penalty_weight: float = 0.20
    parser_error_penalty: float = 0.10
    empty_content_penalty: float = 1.0

    def __post_init__(self) -> None:
        ratios = (
            "quality_pass_threshold",
            "quality_fallback_threshold",
            "garbled_warning_ratio",
            "replacement_warning_ratio",
            "duplicated_line_warning_ratio",
            "suspicious_line_break_warning_ratio",
            "empty_content_penalty",
        )
        if any(not 0 <= getattr(self, name) <= 1 for name in ratios):
            raise ValueError("PDF quality ratios must be between zero and one")
        if self.quality_fallback_threshold > self.quality_pass_threshold:
            raise ValueError("fallback threshold cannot exceed pass threshold")
        non_negative = (
            "minimum_chars_per_page",
            "suspicious_line_max_chars",
            "chars_per_page_penalty_weight",
            "empty_page_penalty_weight",
            "garbled_penalty_weight",
            "replacement_penalty_weight",
            "duplication_penalty_weight",
            "suspicious_line_break_penalty_weight",
            "parser_error_penalty",
        )
        if any(getattr(self, name) < 0 for name in non_negative):
            raise ValueError("PDF quality thresholds and weights must be non-negative")


class PDFQualityAssessment(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    quality_score: float
    warnings: list[str]
    metrics: ParseMetrics


class PDFParseQualityEvaluator:
    def __init__(self, config: PDFParseQualityConfig | None = None) -> None:
        self.config = config or PDFParseQualityConfig()

    def evaluate(
        self,
        result: ParserResult,
        inspection: PDFInspection,
        *,
        parser_error_count: int,
    ) -> PDFQualityAssessment:
        text = "\n".join(block.text for block in result.blocks)
        text_length = len(text)
        non_empty_lines = [line.strip() for line in text.splitlines() if line.strip()]
        duplicated_line_ratio = (
            (len(non_empty_lines) - len(set(non_empty_lines))) / len(non_empty_lines)
            if non_empty_lines
            else 0.0
        )
        replacement_count = text.count("\ufffd")
        replacement_ratio = replacement_count / text_length if text_length else 0.0
        garbled_count = sum(1 for char in text if self._is_garbled(char))
        garbled_ratio = garbled_count / text_length if text_length else 0.0
        flow_lines = [
            line.strip()
            for block in result.blocks
            if block.block_type
            in {BlockType.PARAGRAPH, BlockType.QUOTE, BlockType.OTHER}
            for line in block.text.splitlines()
            if line.strip()
        ]
        suspicious_count = sum(
            len(line) <= self.config.suspicious_line_max_chars
            for line in flow_lines
        )
        suspicious_ratio = (
            suspicious_count / len(flow_lines) if flow_lines else 0.0
        )
        non_empty_pages = {
            page
            for block in result.blocks
            if block.text.strip()
            and isinstance((page := block.metadata.get("page_number")), int)
            and 1 <= page <= inspection.page_count
        }
        empty_page_ratio = (
            (inspection.page_count - len(non_empty_pages)) / inspection.page_count
        )
        chars_per_page = text_length / inspection.page_count
        empty_content = not text.strip()
        table_count = max(
            len(result.tables),
            sum(block.block_type is BlockType.TABLE for block in result.blocks),
        )
        image_count = max(
            len(result.images),
            sum(block.block_type is BlockType.IMAGE for block in result.blocks),
        )
        metrics = ParseMetrics(
            text_length=text_length,
            chars_per_page=chars_per_page,
            empty_page_ratio=empty_page_ratio,
            heading_count=sum(
                block.block_type is BlockType.HEADING for block in result.blocks
            ),
            paragraph_count=sum(
                block.block_type is BlockType.PARAGRAPH for block in result.blocks
            ),
            table_count=table_count,
            image_count=image_count,
            empty_content=empty_content,
            garbled_char_ratio=garbled_ratio,
            replacement_char_ratio=replacement_ratio,
            duplicated_line_ratio=duplicated_line_ratio,
            suspicious_line_break_ratio=suspicious_ratio,
            parser_error_count=parser_error_count,
        )
        warnings = self._warnings(metrics)
        score = self._score(metrics)
        return PDFQualityAssessment(
            quality_score=round(max(0.0, min(1.0, score)), 4),
            warnings=warnings,
            metrics=metrics,
        )

    def empty_assessment(
        self, inspection: PDFInspection, *, parser_error_count: int
    ) -> PDFQualityAssessment:
        return self.evaluate(
            ParserResult(blocks=[]),
            inspection,
            parser_error_count=parser_error_count,
        )

    def _warnings(self, metrics: ParseMetrics) -> list[str]:
        warnings: list[str] = []
        if metrics.empty_content:
            warnings.append("empty_content")
        if metrics.chars_per_page < self.config.minimum_chars_per_page:
            warnings.append("low_chars_per_page")
        if metrics.empty_page_ratio > 0:
            warnings.append("empty_parsed_pages")
        if metrics.garbled_char_ratio > self.config.garbled_warning_ratio:
            warnings.append("high_garbled_char_ratio")
        if metrics.replacement_char_ratio > self.config.replacement_warning_ratio:
            warnings.append("high_replacement_char_ratio")
        if metrics.duplicated_line_ratio > self.config.duplicated_line_warning_ratio:
            warnings.append("high_duplicated_line_ratio")
        if (
            metrics.suspicious_line_break_ratio
            > self.config.suspicious_line_break_warning_ratio
        ):
            warnings.append("high_suspicious_line_break_ratio")
        if metrics.parser_error_count:
            warnings.append("parser_errors_recorded")
        return warnings

    def _score(self, metrics: ParseMetrics) -> float:
        score = 1.0
        if metrics.empty_content:
            score -= self.config.empty_content_penalty
        if self.config.minimum_chars_per_page > 0:
            deficit = max(
                0.0,
                1 - metrics.chars_per_page / self.config.minimum_chars_per_page,
            )
            score -= deficit * self.config.chars_per_page_penalty_weight
        score -= metrics.empty_page_ratio * self.config.empty_page_penalty_weight
        score -= metrics.garbled_char_ratio * self.config.garbled_penalty_weight
        score -= (
            metrics.replacement_char_ratio
            * self.config.replacement_penalty_weight
        )
        score -= (
            metrics.duplicated_line_ratio * self.config.duplication_penalty_weight
        )
        score -= (
            metrics.suspicious_line_break_ratio
            * self.config.suspicious_line_break_penalty_weight
        )
        score -= metrics.parser_error_count * self.config.parser_error_penalty
        return score

    @staticmethod
    def _is_garbled(character: str) -> bool:
        if character == "\ufffd":
            return True
        category = unicodedata.category(character)
        return category == "Co" or (
            category == "Cc" and character not in "\n\r\t"
        )
