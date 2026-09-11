import unittest
from datetime import UTC, datetime

from app.rag.parse_normalize.base import ParserError
from app.rag.parse_normalize.models import BlockType, ParseInput
from app.rag.parse_normalize.normalizer import MarkdownNormalizer
from app.rag.parse_normalize.parsers.markdown import MarkdownParser
from app.rag.parse_normalize.parsers.text import TextDecodeError, TextParserConfig, TxtParser
from app.rag.source_ingestion.models import RawSnapshotRecord


def _parse_input(
    raw: bytes,
    content_type: str,
    raw_path: str,
    source_id: str = "text-source",
) -> ParseInput:
    snapshot = RawSnapshotRecord(
        source_id=source_id,
        original_url="https://example.test/source",
        content_type=content_type,
        content_hash="sha256:" + "0" * 64,
        fetched_at=datetime(2026, 9, 9, tzinfo=UTC),
        raw_path=raw_path,
        http_status=200,
        source_quality="high",
        corpus_version="jay-chou-corpus-v1",
    )
    return ParseInput(snapshot=snapshot, raw_bytes=raw, candidate=None)


MARKDOWN_SOURCE = """---
title: 范特西
author: 周杰伦
published_at: 2001-09-14
language: zh-Hant
catalog:
  id: fantasy-2001
---
#范特西

这是一段 **原始介绍**。

* 爱在西元前
* 爸，我回来了

> 音乐可以记录时代。

```python
print("Fantasy")
```

| 曲目 | 年份 |
| --- | --- |
| 简单爱 | 2001 |

![封面](cover.jpg)
"""


class MarkdownParserTests(unittest.TestCase):
    def test_front_matter_and_supported_blocks_are_preserved(self) -> None:
        result = MarkdownParser().parse(
            _parse_input(
                MARKDOWN_SOURCE.encode("utf-8"),
                "text/markdown; charset=utf-8",
                "data/raw/md/fantasy.md",
                "markdown-source",
            )
        )

        self.assertEqual(result.title, "范特西")
        self.assertEqual(result.author, "周杰伦")
        self.assertEqual(result.published_at.isoformat(), "2001-09-14")
        self.assertEqual(result.language, "zh-Hant")
        self.assertEqual(result.metadata["front_matter"]["catalog"]["id"], "fantasy-2001")
        self.assertEqual(
            [block.block_type for block in result.blocks],
            [
                BlockType.HEADING,
                BlockType.PARAGRAPH,
                BlockType.LIST,
                BlockType.QUOTE,
                BlockType.CODE,
                BlockType.TABLE,
                BlockType.IMAGE,
            ],
        )
        self.assertEqual(result.blocks[0].text, "范特西")
        self.assertEqual(
            result.blocks[2].metadata["items"], ["爱在西元前", "爸，我回来了"]
        )
        self.assertEqual(
            result.blocks[5].metadata["rows"],
            [["曲目", "年份"], ["简单爱", "2001"]],
        )
        self.assertEqual(len(result.tables), 1)
        self.assertTrue(result.tables[0].metadata["has_header"])
        self.assertEqual(result.tables[0].rows[1], ["简单爱", "2001"])
        self.assertEqual(result.blocks[6].metadata["src"], "cover.jpg")

    def test_markdown_is_reformatted_without_rewriting_inline_text(self) -> None:
        result = MarkdownParser().parse(
            _parse_input(
                MARKDOWN_SOURCE.encode("utf-8"),
                "text/markdown",
                "data/raw/md/fantasy.md",
            )
        )

        markdown = MarkdownNormalizer().render(result.blocks)

        self.assertIn("# 范特西", markdown)
        self.assertIn("这是一段 **原始介绍**。", markdown)
        self.assertIn("- 爱在西元前\n- 爸，我回来了", markdown)
        self.assertNotIn("* 爱在西元前", markdown)
        self.assertNotIn("\n\n\n", markdown)

    def test_invalid_front_matter_is_explicit(self) -> None:
        source = b"---\ntitle: [broken\n---\n# Heading\n"

        with self.assertRaisesRegex(ParserError, "markdown_front_matter_invalid"):
            MarkdownParser().parse(
                _parse_input(source, "text/markdown", "data/raw/md/broken.md")
            )


class TxtParserTests(unittest.TestCase):
    def test_gb18030_text_is_decoded_and_newlines_are_normalized(self) -> None:
        raw = "七里香\r\n\r\n这是一段正文。\r\n普通句子。\r\n".encode("gb18030")

        result = TxtParser().parse(
            _parse_input(
                raw,
                "text/plain; charset=gb18030",
                "data/raw/txt/qilixiang.txt",
                "txt-source",
            )
        )

        self.assertEqual(result.title, "七里香")
        self.assertEqual(result.language, "zh")
        self.assertEqual(result.metadata["detected_encoding"], "gb18030")
        self.assertEqual(result.blocks[0].block_type, BlockType.HEADING)
        self.assertEqual(result.blocks[0].text, "七里香")
        self.assertEqual(result.blocks[1].block_type, BlockType.PARAGRAPH)
        self.assertEqual(result.blocks[1].text, "这是一段正文。\n普通句子。")
        self.assertNotIn("\r", MarkdownNormalizer().render(result.blocks))

    def test_plain_lines_without_isolated_title_remain_one_paragraph(self) -> None:
        raw = "短句\n下一行仍属于同一段。".encode("utf-8")

        result = TxtParser().parse(
            _parse_input(raw, "text/plain", "data/raw/txt/plain.txt")
        )

        self.assertIsNone(result.title)
        self.assertEqual(len(result.blocks), 1)
        self.assertEqual(result.blocks[0].block_type, BlockType.PARAGRAPH)

    def test_undecodable_bytes_fail_without_replacement_text(self) -> None:
        parser = TxtParser(
            TextParserConfig(
                fallback_encodings=("utf-8",), min_encoding_confidence=1.0
            )
        )

        with self.assertRaises(TextDecodeError):
            parser.parse(
                _parse_input(
                    b"\x81\x00\xff", "text/plain", "data/raw/txt/broken.txt"
                )
            )


if __name__ == "__main__":
    unittest.main()
