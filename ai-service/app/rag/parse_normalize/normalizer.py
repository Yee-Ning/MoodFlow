"""Deterministic Markdown serialization shared by every parser adapter."""

from collections.abc import Sequence

from .models import BlockType, DocumentBlock, JsonValue


class MarkdownNormalizer:
    version = "1.0.0"

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
        rows = block.metadata.get("rows")
        if not isinstance(rows, list) or not rows:
            return block.text
        normalized_rows = [
            [self._escape_table_cell(self._string_value(cell)) for cell in row]
            for row in rows
            if isinstance(row, list)
        ]
        if not normalized_rows:
            return block.text
        column_count = max(len(row) for row in normalized_rows)
        padded = [row + [""] * (column_count - len(row)) for row in normalized_rows]
        header = padded[0]
        separator = ["---"] * column_count
        body = padded[1:]
        return "\n".join(
            self._markdown_row(row) for row in [header, separator, *body]
        )

    @staticmethod
    def _markdown_row(row: Sequence[str]) -> str:
        return f"| {' | '.join(row)} |"

    @staticmethod
    def _escape_table_cell(value: str) -> str:
        return value.replace("|", "\\|").replace("\r\n", "<br>").replace(
            "\r", "<br>"
        ).replace("\n", "<br>")

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
