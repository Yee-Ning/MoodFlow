"""Deterministic Markdown serialization shared by every parser adapter."""

from collections.abc import Sequence

from .models import BlockType, DocumentBlock, JsonValue
from .table_support import normalized_table_rows, render_table_markdown


class MarkdownNormalizer:
    version = "1.1.0"

    def render(self, blocks: Sequence[DocumentBlock]) -> str:
        rendered = [self._render_block(block) for block in blocks]
        normalized = [self._normalize_newlines(value).strip() for value in rendered]
        non_empty = [value for value in normalized if value]
        return "\n\n".join(non_empty) + ("\n" if non_empty else "")

    def _render_block(self, block: DocumentBlock) -> str:
        if block.block_type is BlockType.HEADING:
            return f"{'#' * (block.level or 1)} {block.text.strip()}"
        if block.block_type is BlockType.LIST:
            return self._render_list(block)
        if block.block_type is BlockType.QUOTE:
            return "\n".join(f"> {line}" for line in block.text.splitlines())
        if block.block_type is BlockType.CODE:
            return self._render_code(block)
        if block.block_type is BlockType.TABLE:
            return self._render_table(block)
        if block.block_type is BlockType.IMAGE:
            alt = self._string_value(block.metadata.get("alt", block.text))
            src = self._string_value(block.metadata.get("src", ""))
            return f"![{alt}]({src})" if src else block.text
        return block.text

    def _render_list(self, block: DocumentBlock) -> str:
        items = block.metadata.get("items", [])
        if not isinstance(items, list):
            return block.text
        marker = "1." if block.metadata.get("ordered") is True else "-"
        return "\n".join(
            f"{marker} {self._string_value(item).strip()}"
            for item in items
            if self._string_value(item).strip()
        )

    def _render_code(self, block: DocumentBlock) -> str:
        language = self._string_value(block.metadata.get("language", "")).strip()
        code = self._normalize_newlines(block.text).rstrip("\n")
        longest_run = 0
        current_run = 0
        for character in code:
            if character == "`":
                current_run += 1
                longest_run = max(longest_run, current_run)
            else:
                current_run = 0
        fence = "`" * max(3, longest_run + 1)
        return f"{fence}{language}\n{code}\n{fence}"

    def _render_table(self, block: DocumentBlock) -> str:
        rows = normalized_table_rows(block.metadata.get("rows"))
        if not rows:
            return block.text
        return render_table_markdown(
            rows, has_header=block.metadata.get("has_header") is not False
        )

    @staticmethod
    def _normalize_newlines(value: str) -> str:
        return value.replace("\r\n", "\n").replace("\r", "\n")

    @staticmethod
    def _string_value(value: JsonValue) -> str:
        if value is None:
            return ""
        if isinstance(value, bool):
            return "true" if value else "false"
        return str(value)
