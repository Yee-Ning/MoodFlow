"""Conservative plain-text decoder and block adapter."""

from __future__ import annotations

import codecs
import re
from dataclasses import dataclass

from charset_normalizer import from_bytes

from ..base import BaseParser, ParserError
from ..models import (
    BlockType,
    DetectedFileType,
    DocumentBlock,
    ParseInput,
    ParserResult,
)


class TextDecodeError(ParserError):
    """Raw text bytes could not be decoded without replacement."""


@dataclass(frozen=True, slots=True)
class TextParserConfig:
    heading_max_chars: int = 80
    min_encoding_confidence: float = 0.5
    fallback_encodings: tuple[str, ...] = ("utf-8", "utf-16", "gb18030", "big5")

    def __post_init__(self) -> None:
        if self.heading_max_chars <= 0:
            raise ValueError("heading_max_chars must be positive")
        if not 0 <= self.min_encoding_confidence <= 1:
            raise ValueError("min_encoding_confidence must be between zero and one")


class TxtParser(BaseParser):
    parser_name = "txt"
    parser_version = "1.0.0"
    supported_file_types = frozenset({DetectedFileType.TXT})

    _BOMS = (
        (codecs.BOM_UTF8, "utf-8-sig"),
        (codecs.BOM_UTF32_LE, "utf-32"),
        (codecs.BOM_UTF32_BE, "utf-32"),
        (codecs.BOM_UTF16_LE, "utf-16"),
        (codecs.BOM_UTF16_BE, "utf-16"),
    )
    _SENTENCE_ENDINGS = ".?!。？！；;"

    def __init__(self, config: TextParserConfig | None = None) -> None:
        self.config = config or TextParserConfig()

    def parse(self, parse_input: ParseInput) -> ParserResult:
        text, encoding = self._decode(
            parse_input.raw_bytes, parse_input.snapshot.content_type
        )
        text = text.replace("\r\n", "\n").replace("\r", "\n")
        sections = [
            section.strip("\n")
            for section in re.split(r"\n[ \t]*\n+", text)
            if section.strip()
        ]
        blocks: list[DocumentBlock] = []
        title: str | None = None
        for index, section in enumerate(sections):
            is_heading = index == 0 and len(sections) > 1 and self._is_heading(section)
            block_type = BlockType.HEADING if is_heading else BlockType.PARAGRAPH
            value = "\n".join(line.rstrip() for line in section.splitlines()).strip()
            order_index = len(blocks)
            block_id = f"{parse_input.snapshot.source_id}-b{order_index:06d}"
            blocks.append(
                DocumentBlock(
                    block_id=block_id,
                    block_type=block_type,
                    text=value,
                    level=1 if is_heading else None,
                    order_index=order_index,
                    parent_block_id=None if is_heading or not blocks else blocks[0].block_id,
                    metadata={},
                )
            )
            if is_heading:
                title = value
        language = "zh" if re.search(r"[\u3400-\u9fff]", text) else "und"
        return ParserResult(
            title=title,
            author=None,
            published_at=None,
            language=language,
            blocks=blocks,
            metadata={"detected_encoding": encoding},
        )

    def _decode(self, raw: bytes, content_type: str) -> tuple[str, str]:
        candidates: list[str] = []
        for bom, encoding in self._BOMS:
            if raw.startswith(bom):
                candidates.append(encoding)
                break
        declared = self._declared_charset(content_type)
        if declared:
            candidates.append(declared)
        candidates.extend(self.config.fallback_encodings)
        seen: set[str] = set()
        for encoding in candidates:
            normalized = encoding.lower()
            if normalized in seen:
                continue
            seen.add(normalized)
            try:
                return raw.decode(encoding, errors="strict"), normalized
            except (LookupError, UnicodeDecodeError):
                continue

        best = from_bytes(raw).best()
        if best is not None:
            confidence = best.percent_coherence / 100
            if confidence >= self.config.min_encoding_confidence:
                try:
                    return str(best), str(best.encoding).lower()
                except (LookupError, UnicodeError):
                    pass
        raise TextDecodeError("text_decode_failed")

    def _is_heading(self, section: str) -> bool:
        lines = section.splitlines()
        value = section.strip()
        return (
            len(lines) == 1
            and 0 < len(value) <= self.config.heading_max_chars
            and not value.endswith(self._SENTENCE_ENDINGS)
        )

    @staticmethod
    def _declared_charset(content_type: str) -> str | None:
        match = re.search(
            r"charset\s*=\s*['\"]?([^;'\"\s]+)", content_type, flags=re.IGNORECASE
        )
        return match.group(1) if match else None
