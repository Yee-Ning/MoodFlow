"""Markdown token adapter with safe front matter handling."""

from __future__ import annotations

import re
from datetime import date, datetime

import yaml
from markdown_it import MarkdownIt
from markdown_it.token import Token

from ..base import BaseParser, ParserError
from ..models import (
    BlockType,
    DetectedFileType,
    DocumentBlock,
    JsonValue,
    ParseInput,
    ParserResult,
)
from ..table_support import document_tables_from_blocks


class MarkdownParser(BaseParser):
    parser_name = "markdown"
    parser_version = "1.1.0"
    supported_file_types = frozenset({DetectedFileType.MARKDOWN})

    def __init__(self) -> None:
        self._markdown = MarkdownIt("commonmark").enable("table")

    def parse(self, parse_input: ParseInput) -> ParserResult:
        try:
            text = parse_input.raw_bytes.decode("utf-8-sig", errors="strict")
        except UnicodeDecodeError as exc:
            raise ParserError("markdown_utf8_decode_failed") from exc
        front_matter, body = self._split_front_matter(self._normalize_newlines(text))
        body = self._normalize_heading_markers(body)
        try:
            tokens = self._markdown.parse(body)
        except Exception as exc:
            raise ParserError("markdown_tokenize_failed") from exc
        blocks = self._blocks(tokens, parse_input.snapshot.source_id)
        first_h1 = next(
            (
                block.text
                for block in blocks
                if block.block_type is BlockType.HEADING and block.level == 1
            ),
            None,
        )
        title = self._optional_string(front_matter.get("title")) or first_h1
        author = self._optional_string(front_matter.get("author"))
        published_at = self._date_value(
            front_matter.get("published_at", front_matter.get("date"))
        )
        language = self._optional_string(
            front_matter.get("language", front_matter.get("lang"))
        ) or "und"
        return ParserResult(
            title=title,
            author=author,
            published_at=published_at,
            language=language,
            blocks=blocks,
            tables=document_tables_from_blocks(blocks),
            metadata={"front_matter": self._json_safe(front_matter)},
        )

    def _blocks(self, tokens: list[Token], source_id: str) -> list[DocumentBlock]:
        blocks: list[DocumentBlock] = []
        headings: dict[int, str] = {}
        index = 0
        while index < len(tokens):
            token = tokens[index]
            block_type: BlockType | None = None
            text = ""
            level: int | None = None
            metadata: dict[str, JsonValue] = {}
            next_index = index + 1
            if token.type == "heading_open" and index + 1 < len(tokens):
                inline = tokens[index + 1]
                block_type = BlockType.HEADING
                text = inline.content.strip()
                level = int(token.tag[1])
                next_index = index + 3
            elif token.type == "paragraph_open" and index + 1 < len(tokens):
                inline = tokens[index + 1]
                images = [child for child in (inline.children or []) if child.type == "image"]
                visible = [
                    child
                    for child in (inline.children or [])
                    if child.type not in {"image", "softbreak", "hardbreak"}
                    and child.content.strip()
                ]
                if images and not visible:
                    for image in images:
                        self._append_block(
                            blocks,
                            headings,
                            source_id,
                            BlockType.IMAGE,
                            image.content,
                            None,
                            {
                                "src": image.attrGet("src") or "",
                                "alt": image.content,
                                "title": image.attrGet("title") or "",
                            },
                        )
                    index += 3
                    continue
                block_type = BlockType.PARAGRAPH
                text = inline.content.strip()
                next_index = index + 3
            elif token.type in {"bullet_list_open", "ordered_list_open"}:
                items, next_index = self._list_items(tokens, index)
                block_type = BlockType.LIST
                text = "\n".join(items)
                metadata = {
                    "ordered": token.type == "ordered_list_open",
                    "items": items,
                }
            elif token.type == "blockquote_open":
                contents, next_index = self._container_inline_text(
                    tokens, index, "blockquote_open", "blockquote_close"
                )
                block_type = BlockType.QUOTE
                text = "\n".join(contents)
            elif token.type in {"fence", "code_block"}:
                block_type = BlockType.CODE
                text = token.content.rstrip("\n")
                metadata = {"language": token.info.strip().split(maxsplit=1)[0] if token.info.strip() else ""}
            elif token.type == "table_open":
                rows, next_index = self._table_rows(tokens, index)
                block_type = BlockType.TABLE
                text = "\n".join(" | ".join(row) for row in rows)
                metadata = {
                    "rows": rows,
                    "has_header": True,
                    "table_kind_hint": "semantic_candidate",
                }
            elif token.type == "html_block":
                block_type = BlockType.OTHER
                text = token.content.rstrip()

            if block_type is not None and (text or block_type is BlockType.IMAGE):
                self._append_block(
                    blocks, headings, source_id, block_type, text, level, metadata
                )
            index = next_index
        return blocks

    @staticmethod
    def _append_block(
        blocks: list[DocumentBlock],
        headings: dict[int, str],
        source_id: str,
        block_type: BlockType,
        text: str,
        level: int | None,
        metadata: dict[str, JsonValue],
    ) -> None:
        order_index = len(blocks)
        block_id = f"{source_id}-b{order_index:06d}"
        if block_type is BlockType.HEADING:
            assert level is not None
            lower_levels = [value for value in headings if value < level]
            parent_id = headings[max(lower_levels)] if lower_levels else None
            for heading_level in list(headings):
                if heading_level >= level:
                    del headings[heading_level]
            headings[level] = block_id
        else:
            parent_id = headings[max(headings)] if headings else None
        blocks.append(
            DocumentBlock(
                block_id=block_id,
                block_type=block_type,
                text=text,
                level=level,
                order_index=order_index,
                parent_block_id=parent_id,
                metadata=metadata,
            )
        )

    @staticmethod
    def _list_items(tokens: list[Token], start: int) -> tuple[list[str], int]:
        opening = tokens[start].type
        closing = opening.replace("_open", "_close")
        items: list[str] = []
        current: list[str] = []
        item_depth = 0
        list_depth = 0
        index = start
        while index < len(tokens):
            token = tokens[index]
            if token.type == opening:
                list_depth += 1
            elif token.type == closing:
                list_depth -= 1
                if list_depth == 0:
                    return items, index + 1
            elif token.type == "list_item_open":
                item_depth += 1
                if item_depth == 1:
                    current = []
            elif token.type == "list_item_close":
                if item_depth == 1:
                    value = " ".join(current).strip()
                    if value:
                        items.append(value)
                item_depth -= 1
            elif token.type == "inline" and item_depth == 1 and list_depth == 1:
                current.append(token.content)
            index += 1
        raise ParserError("markdown_list_unclosed")

    @staticmethod
    def _container_inline_text(
        tokens: list[Token], start: int, opening: str, closing: str
    ) -> tuple[list[str], int]:
        depth = 0
        values: list[str] = []
        for index in range(start, len(tokens)):
            token = tokens[index]
            if token.type == opening:
                depth += 1
            elif token.type == closing:
                depth -= 1
                if depth == 0:
                    return values, index + 1
            elif token.type == "inline" and token.content.strip():
                values.append(token.content.strip())
        raise ParserError("markdown_container_unclosed")

    @staticmethod
    def _table_rows(tokens: list[Token], start: int) -> tuple[list[list[str]], int]:
        rows: list[list[str]] = []
        current_row: list[str] | None = None
        current_cell = False
        for index in range(start + 1, len(tokens)):
            token = tokens[index]
            if token.type == "tr_open":
                current_row = []
            elif token.type in {"th_open", "td_open"}:
                current_cell = True
            elif token.type == "inline" and current_cell and current_row is not None:
                current_row.append(token.content)
            elif token.type in {"th_close", "td_close"}:
                current_cell = False
            elif token.type == "tr_close" and current_row is not None:
                rows.append(current_row)
                current_row = None
            elif token.type == "table_close":
                return rows, index + 1
        raise ParserError("markdown_table_unclosed")

    @staticmethod
    def _split_front_matter(text: str) -> tuple[dict[str, object], str]:
        if not text.startswith("---\n"):
            return {}, text
        lines = text.splitlines(keepends=True)
        closing_index = next(
            (index for index, line in enumerate(lines[1:], 1) if line.strip() in {"---", "..."}),
            None,
        )
        if closing_index is None:
            raise ParserError("markdown_front_matter_unclosed")
        source = "".join(lines[1:closing_index])
        try:
            loaded = yaml.safe_load(source) or {}
        except yaml.YAMLError as exc:
            raise ParserError("markdown_front_matter_invalid") from exc
        if not isinstance(loaded, dict):
            raise ParserError("markdown_front_matter_invalid")
        return loaded, "".join(lines[closing_index + 1 :])

    @staticmethod
    def _normalize_heading_markers(text: str) -> str:
        output: list[str] = []
        active_fence: str | None = None
        for line in text.splitlines(keepends=True):
            fence_match = re.match(r"^\s*(`{3,}|~{3,})", line)
            if fence_match:
                marker = fence_match.group(1)[0]
                active_fence = None if active_fence == marker else marker
                output.append(line)
                continue
            if active_fence is None:
                line = re.sub(r"^(#{1,6})([^#\s])", r"\1 \2", line)
            output.append(line)
        return "".join(output)

    @staticmethod
    def _normalize_newlines(text: str) -> str:
        return text.replace("\r\n", "\n").replace("\r", "\n")

    @classmethod
    def _json_safe(cls, value: object) -> JsonValue:
        if value is None or isinstance(value, (str, int, float, bool)):
            return value
        if isinstance(value, (date, datetime)):
            return value.isoformat()
        if isinstance(value, dict):
            return {str(key): cls._json_safe(item) for key, item in value.items()}
        if isinstance(value, (list, tuple)):
            return [cls._json_safe(item) for item in value]
        return str(value)

    @staticmethod
    def _optional_string(value: object) -> str | None:
        if value is None:
            return None
        normalized = str(value).strip()
        return normalized or None

    @staticmethod
    def _date_value(value: object) -> date | None:
        if isinstance(value, datetime):
            return value.date()
        if isinstance(value, date):
            return value
        if isinstance(value, str):
            try:
                return date.fromisoformat(value.strip()[:10])
            except ValueError:
                return None
        return None
