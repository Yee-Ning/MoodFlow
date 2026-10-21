"""Stage 2: Structural Data Cleaning with Whitelist Protections."""

from __future__ import annotations

import re
from pydantic import BaseModel, ConfigDict, Field

from .config import StructuralCleanConfig
from .models import StageMetrics


class StructuralCleanResult(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    cleaned_text: str
    before_chars: int = Field(ge=0)
    after_chars: int = Field(ge=0)
    removed_chars: int = Field(ge=0)
    removed_blocks: int = Field(ge=0)
    protected_tables: int = Field(ge=0)
    protected_lists: int = Field(ge=0)
    stage_metrics: StageMetrics


class StructuralCleaner:
    """DOM and structural-level de-noising preserving whitelisted tables and lists."""

    # Markdown table separator regex: e.g. |---|---| or |:---|---:|
    _TABLE_SEPARATOR_PATTERN = re.compile(r"^\s*\|?\s*(?::?-+:?\s*\|)+\s*(?::?-+:?\s*\|?)?\s*$")
    # Markdown list item regex: - item, * item, + item, 1. item
    _LIST_ITEM_PATTERN = re.compile(r"^\s*(?:[-*+]|\d{1,5}\.)\s+\S+")
    # Structural empty / residual noise patterns (empty tags, orphan divs, redundant dividers)
    _ORPHAN_TAG_PATTERN = re.compile(r"^(?:<div[^>]*>\s*</div>|<span[^>]*>\s*</span>|<p[^>]*>\s*</p>|<!--.*?-->)$", re.IGNORECASE)
    _CONSECUTIVE_HR_PATTERN = re.compile(r"^(?:-{3,}|\*{3,}|_{3,})$")

    def __init__(self, config: StructuralCleanConfig | None = None) -> None:
        self.config = config or StructuralCleanConfig()

    def clean(self, text: str) -> StructuralCleanResult:
        if not self.config.enabled or not text:
            before_len = len(text)
            return StructuralCleanResult(
                cleaned_text=text,
                before_chars=before_len,
                after_chars=before_len,
                removed_chars=0,
                removed_blocks=0,
                protected_tables=0,
                protected_lists=0,
                stage_metrics=StageMetrics(
                    stage_index=2,
                    stage_name="structural_cleaning",
                    before_chars=before_len,
                    after_chars=before_len,
                    removed_chars=0,
                    removed_blocks=0,
                    details={"enabled": False},
                ),
            )

        before_chars = len(text)
        # Split text into paragraphs/blocks separated by double newlines
        blocks = re.split(r"\n\s*\n", text)
        cleaned_blocks: list[str] = []
        removed_blocks_count = 0
        protected_tables_count = 0
        protected_lists_count = 0

        for block in blocks:
            trimmed = block.strip()
            if not trimmed:
                continue

            # Check if block is a table
            if self.is_table(trimmed):
                if self.config.whitelist_tables:
                    protected_tables_count += 1
                    cleaned_blocks.append(block)
                    continue

            # Check if block is a list
            if self.is_list(trimmed):
                if self.config.whitelist_lists:
                    protected_lists_count += 1
                    cleaned_blocks.append(block)
                    continue

            # Structural noise removal
            if self._is_structural_noise(trimmed):
                removed_blocks_count += 1
                continue

            cleaned_blocks.append(block)

        if removed_blocks_count == 0:
            result_text = text
        else:
            result_text = "\n\n".join(cleaned_blocks)
            if text.endswith("\n") and not result_text.endswith("\n"):
                result_text += "\n"

        after_chars = len(result_text)
        removed_chars = max(0, before_chars - after_chars)

        metrics = StageMetrics(
            stage_index=2,
            stage_name="structural_cleaning",
            before_chars=before_chars,
            after_chars=after_chars,
            removed_chars=removed_chars,
            removed_blocks=removed_blocks_count,
            details={
                "protected_tables": protected_tables_count,
                "protected_lists": protected_lists_count,
                "removed_structural_blocks": removed_blocks_count,
            },
        )

        return StructuralCleanResult(
            cleaned_text=result_text,
            before_chars=before_chars,
            after_chars=after_chars,
            removed_chars=removed_chars,
            removed_blocks=removed_blocks_count,
            protected_tables=protected_tables_count,
            protected_lists=protected_lists_count,
            stage_metrics=metrics,
        )

    @classmethod
    def is_table(cls, text: str) -> bool:
        """Identify if a block of text is a Markdown or HTML table."""
        if "<table" in text.lower() and "</table>" in text.lower():
            return True
        lines = [line.strip() for line in text.splitlines() if line.strip()]
        if len(lines) < 2:
            return False
        # Table must contain separator line or rows with pipe delimiters
        has_separator = any(cls._TABLE_SEPARATOR_PATTERN.match(line) for line in lines)
        if has_separator:
            return True
        # Or multi-line where every line starts and ends or contains multiple pipes
        pipe_lines = sum(1 for line in lines if line.count("|") >= 2)
        return pipe_lines >= 2 and pipe_lines == len(lines)

    @classmethod
    def is_list(cls, text: str) -> bool:
        """Identify if a block of text is a Markdown list."""
        lines = [line.strip() for line in text.splitlines() if line.strip()]
        if not lines:
            return False
        list_lines = sum(1 for line in lines if cls._LIST_ITEM_PATTERN.match(line))
        # If at least half the non-empty lines are list items, treat as list block
        return list_lines > 0 and (list_lines >= len(lines) * 0.6 or list_lines >= 2)

    @classmethod
    def _is_structural_noise(cls, block: str) -> bool:
        """Identify pure structural garbage (orphan empty tags, redundant separators, etc.)."""
        lines = [line.strip() for line in block.splitlines() if line.strip()]
        if not lines:
            return True
        # Check for orphan empty HTML tags
        if all(cls._ORPHAN_TAG_PATTERN.match(line) for line in lines):
            return True
        # Consecutive thematic breaks with no text
        if all(cls._CONSECUTIVE_HR_PATTERN.match(line) for line in lines) and len(lines) >= 2:
            return True
        return False
