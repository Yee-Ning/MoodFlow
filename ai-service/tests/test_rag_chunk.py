from __future__ import annotations

import hashlib
import json
import tempfile
import unittest
from pathlib import Path

from app.rag.chunk import (
    ChunkConfig,
    ChunkEngine,
    ChunkPipelineService,
    ChunkType,
    HuggingFaceTokenCounter,
    TableHeaderState,
    TokenCounterConfig,
    UnicodeHeuristicTokenCounter,
)
from app.rag.clean.models import CleanedDocument
from app.rag.chunk.validator import ChunkValidator
from app.rag.parse_normalize.models import (
    BlockType,
    DetectedFileType,
    DocumentBlock,
    DocumentTable,
    NormalizedDocument,
)
from app.rag.parse_normalize.normalizer import MarkdownNormalizer


CHECKSUM = "sha256:" + "a" * 64
AI_SERVICE_ROOT = Path(__file__).resolve().parents[1]


def _document(
    blocks: list[DocumentBlock],
    *,
    tables: list[DocumentTable] | None = None,
    source_id: str = "chunk-fixture",
    title: str = "周杰伦",
) -> tuple[CleanedDocument, NormalizedDocument]:
    markdown = MarkdownNormalizer().render(blocks)
    normalized = NormalizedDocument(
        document_id=f"doc-{source_id}",
        source_id=source_id,
        title=title,
        language="zh",
        original_file_type=DetectedFileType.HTML,
        raw_path=f"data/raw/html/{source_id}.html",
        parser_name="fixture",
        parser_version="1.0.0",
        normalization_version="1.1.0",
        raw_content_hash=CHECKSUM,
        markdown=markdown,
        blocks=blocks,
        tables=tables or [],
        metadata={"source_metadata": {"artist": "周杰伦"}},
        parse_quality_score=1,
        corpus_version="chunk-test-v1",
    )
    cleaned = CleanedDocument(
        document_id=normalized.document_id,
        source_id=source_id,
        source="fixture",
        source_type="article",
        document_type="artist",
        title=title,
        language="zh",
        original_file_type="html",
        corpus_version="chunk-test-v1",
        cleaned_text=markdown,
        clean_version="v0.2",
        rule_registry_checksum=CHECKSUM,
        rules_applied=[],
        warnings=[],
        needs_review=False,
        normalized_path=f"data/normalized/{source_id}/document.json",
        normalized_content_hash="sha256:"
        + hashlib.sha256(markdown.encode("utf-8")).hexdigest(),
        metadata=normalized.metadata,
    )
    return cleaned, normalized


def _small_config() -> ChunkConfig:
    return ChunkConfig(
        target_parent_tokens=45,
        max_parent_tokens=65,
        target_child_tokens=18,
        max_child_tokens=28,
        min_child_tokens=5,
        child_overlap_tokens=3,
    )


class TokenCounterTests(unittest.TestCase):
    def test_unicode_counter_is_lexical_not_character_length(self) -> None:
        counter = UnicodeHeuristicTokenCounter()

        self.assertEqual(counter.count("hello world"), 2)
        self.assertEqual(counter.count("周杰伦 music"), 4)
        self.assertNotEqual(counter.count("hello world"), len("hello world"))

    def test_huggingface_counter_accepts_a_configurable_tokenizer(self) -> None:
        class FakeTokenizer:
            name_or_path = "fixture-tokenizer"

            @staticmethod
            def encode(
                text: str, *, add_special_tokens: bool, truncation: bool
            ) -> list[int]:
                assert not add_special_tokens
                assert not truncation
                return list(range(len(text.split())))

        counter = HuggingFaceTokenCounter(FakeTokenizer())

        self.assertEqual(counter.name, "huggingface:fixture-tokenizer")
        self.assertEqual(counter.count("one two three"), 3)

    def test_huggingface_backend_requires_name_from_config(self) -> None:
        with self.assertRaises(ValueError):
            TokenCounterConfig(backend="huggingface")


class NarrativeChunkTests(unittest.TestCase):
    def _structured_document(self) -> tuple[CleanedDocument, NormalizedDocument]:
        blocks = [
            DocumentBlock(
                block_id="b0",
                block_type=BlockType.HEADING,
                text="音乐生涯",
                level=1,
                order_index=0,
            ),
            DocumentBlock(
                block_id="b1",
                block_type=BlockType.HEADING,
                text="2000-2003",
                level=2,
                order_index=1,
                parent_block_id="b0",
            ),
            DocumentBlock(
                block_id="b2",
                block_type=BlockType.PARAGRAPH,
                text=(
                    "2000年发行首张专辑。随后他把节奏蓝调、中国风与说唱结合起来！"
                    "这些作品逐步形成鲜明的个人语言，也影响了华语流行音乐的制作方式。"
                )
                * 3,
                order_index=2,
                parent_block_id="b1",
                metadata={"page_number": 1},
            ),
            DocumentBlock(
                block_id="b3",
                block_type=BlockType.HEADING,
                text="2004-2008",
                level=2,
                order_index=3,
                parent_block_id="b0",
            ),
            DocumentBlock(
                block_id="b4",
                block_type=BlockType.PARAGRAPH,
                text="《七里香》延续了旋律与叙事并重的创作方向。",
                order_index=4,
                parent_block_id="b3",
            ),
        ]
        return _document(blocks)

    def test_structure_parent_child_recursive_context_and_relationships(self) -> None:
        cleaned, normalized = self._structured_document()

        result = ChunkEngine(_small_config()).chunk(cleaned, normalized)

        self.assertTrue(result.validation.valid, result.validation.issues)
        self.assertGreater(result.stats.parent_count, 1)
        self.assertGreater(result.stats.child_count, result.stats.parent_count)
        first = result.children[0]
        self.assertEqual(first.section_path, ["音乐生涯", "2000-2003"])
        self.assertEqual(first.breadcrumb, "周杰伦 > 音乐生涯 > 2000-2003")
        self.assertTrue(first.retrieval_content.startswith(first.breadcrumb))
        self.assertNotEqual(first.content, first.retrieval_content)
        parent_ids = {value.chunk_id for value in result.parents}
        self.assertTrue(all(value.parent_id in parent_ids for value in result.children))
        self.assertIsNone(result.children[0].previous_chunk_id)
        self.assertEqual(result.children[0].next_chunk_id, result.children[1].chunk_id)
        self.assertIsNone(result.children[-1].next_chunk_id)
        self.assertTrue(
            all(
                value.token_count <= _small_config().max_child_tokens
                for value in result.children
            )
        )

    def test_stable_ids_repeat_and_change_with_content(self) -> None:
        cleaned, normalized = self._structured_document()
        engine = ChunkEngine(_small_config())

        first = engine.chunk(cleaned, normalized)
        second = engine.chunk(cleaned, normalized)
        self.assertEqual(
            [value.chunk_id for value in first.parents],
            [value.chunk_id for value in second.parents],
        )
        self.assertEqual(
            [value.chunk_id for value in first.children],
            [value.chunk_id for value in second.children],
        )

        changed_blocks = list(normalized.blocks)
        changed_blocks[-1] = changed_blocks[-1].model_copy(
            update={"text": changed_blocks[-1].text + "新的事实。"}
        )
        changed_cleaned, changed_normalized = _document(changed_blocks)
        changed = engine.chunk(changed_cleaned, changed_normalized)
        self.assertNotEqual(first.children[-1].chunk_id, changed.children[-1].chunk_id)

    def test_clean_projection_does_not_restore_removed_list_items(self) -> None:
        block = DocumentBlock(
            block_id="list-0",
            block_type=BlockType.LIST,
            text="NEWS 最新消息\n作品年表\nFB",
            order_index=0,
            metadata={
                "ordered": False,
                "items": ["NEWS 最新消息", "作品年表", "FB"],
            },
        )
        cleaned, normalized = _document([block])
        cleaned = cleaned.model_copy(update={"cleaned_text": "- 作品年表\n"})

        result = ChunkEngine(_small_config()).chunk(cleaned, normalized)

        content = "\n".join(value.content for value in result.parents)
        self.assertIn("作品年表", content)
        self.assertNotIn("NEWS 最新消息", content)
        self.assertNotIn("FB", content)

    def test_markdown_splitter_is_used_only_when_blocks_are_absent(self) -> None:
        cleaned, normalized = _document([])
        markdown = "# 音乐生涯\n\n## 2000-2003\n\n首张专辑开启了新的创作阶段。\n"
        normalized = normalized.model_copy(update={"markdown": markdown, "blocks": []})
        cleaned = cleaned.model_copy(
            update={
                "cleaned_text": markdown,
                "normalized_content_hash": "sha256:"
                + hashlib.sha256(markdown.encode("utf-8")).hexdigest(),
            }
        )

        result = ChunkEngine(_small_config()).chunk(cleaned, normalized)

        self.assertTrue(result.validation.valid, result.validation.issues)
        self.assertEqual(result.children[0].section_path, ["音乐生涯", "2000-2003"])
        self.assertEqual(
            result.children[0].metadata["source_block_types"],
            ["markdown_fallback"],
        )


class TableChunkTests(unittest.TestCase):
    def _table_document(
        self,
        *,
        rows: list[list[str]],
        metadata: dict[str, object],
        table_id: str = "table-0",
    ) -> tuple[CleanedDocument, NormalizedDocument, DocumentTable]:
        block = DocumentBlock(
            block_id="table-block-0",
            block_type=BlockType.TABLE,
            text="table display is not consumed",
            order_index=0,
            metadata={"rows": rows, **metadata},
        )
        table = DocumentTable(
            table_id=table_id,
            markdown="this field is deliberately ignored by TableChunker",
            rows=rows,
            metadata={"source_block_id": block.block_id, **metadata},
        )
        cleaned, normalized = _document([block], tables=[table])
        return cleaned, normalized, table

    def test_large_explicit_header_table_splits_by_rows_without_loss(self) -> None:
        rows = [["曲目", "年份", "说明"]] + [
            [f"歌曲{index}", str(2000 + index), "中国风作品"] for index in range(18)
        ]
        cleaned, normalized, table = self._table_document(
            rows=rows,
            metadata={"has_header": True, "table_kind_hint": "semantic_candidate"},
        )

        result = ChunkEngine(_small_config()).chunk(cleaned, normalized)

        table_children = [
            value for value in result.children if value.chunk_type is ChunkType.TABLE
        ]
        self.assertTrue(result.validation.valid, result.validation.issues)
        self.assertGreater(len(table_children), 1)
        self.assertTrue(
            all(value.table_data.headers == rows[0] for value in table_children)
        )
        emitted_rows = [
            row for value in table_children for row in value.table_data.rows
        ]
        self.assertEqual(emitted_rows, table.rows[1:])
        self.assertEqual(result.stats.source_table_rows, 18)
        self.assertEqual(result.stats.emitted_table_rows, 18)

    def test_inferred_header_is_respected_without_new_guessing(self) -> None:
        rows = [["推断列一", "推断列二"], ["值一", "值二"]]
        cleaned, normalized, _ = self._table_document(
            rows=rows,
            metadata={"has_header": True, "header_state": "inferred"},
        )

        result = ChunkEngine(_small_config()).chunk(cleaned, normalized)
        data = result.children[0].table_data

        self.assertEqual(data.header_state, TableHeaderState.INFERRED)
        self.assertEqual(data.headers, rows[0])
        self.assertEqual(data.rows, rows[1:])

    def test_headerless_table_keeps_first_row_and_empty_cells(self) -> None:
        rows = [
            ["第一行不是表头", "", "2000"],
            ["第二行", "保留", ""],
            ["第三行", "", "2002"],
        ]
        cleaned, normalized, _ = self._table_document(
            rows=rows,
            metadata={"has_header": False, "table_kind_hint": "uncertain"},
        )

        result = ChunkEngine(_small_config()).chunk(cleaned, normalized)
        table_children = [
            value for value in result.children if value.chunk_type is ChunkType.TABLE
        ]
        data_rows = [row for child in table_children for row in child.table_data.rows]

        self.assertTrue(result.validation.valid, result.validation.issues)
        self.assertEqual(data_rows, rows)
        self.assertTrue(all(child.table_data.headers == [] for child in table_children))
        self.assertTrue(
            all(
                "col_1 | col_2 | col_3" in child.retrieval_content
                for child in table_children
            )
        )
        self.assertTrue(
            all(not child.content.startswith("col_1") for child in table_children)
        )
        self.assertEqual(data_rows[0], ["第一行不是表头", "", "2000"])

    def test_oversized_atomic_row_is_fragmented_but_owned_once(self) -> None:
        rows = [["字段", "正文"], ["内容", "很长的中文句子。" * 80]]
        cleaned, normalized, _ = self._table_document(
            rows=rows,
            metadata={"has_header": True},
        )

        result = ChunkEngine(_small_config()).chunk(cleaned, normalized)
        table_children = [
            child for child in result.children if child.chunk_type is ChunkType.TABLE
        ]
        fragments = [
            fragment
            for child in table_children
            for fragment in child.table_data.row_fragments
        ]
        owned_rows = [row for child in table_children for row in child.table_data.rows]

        self.assertTrue(result.validation.valid, result.validation.issues)
        self.assertGreater(len(fragments), 1)
        self.assertEqual(owned_rows, [rows[1]])
        self.assertEqual(sum(fragment.owns_source_row for fragment in fragments), 1)
        self.assertTrue(
            all(
                child.token_count <= _small_config().max_child_tokens
                for child in table_children
            )
        )


class ChunkPipelineServiceTests(unittest.TestCase):
    def test_paired_clean_normalized_corpus_validates_and_writes(self) -> None:
        block = DocumentBlock(
            block_id="service-b0",
            block_type=BlockType.PARAGRAPH,
            text="离线 Chunk 服务保留清洗后的内容。",
            order_index=0,
        )
        cleaned, normalized = _document(
            [block], source_id="service-source", title="服务夹具"
        )
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            normalized_path = root / cleaned.normalized_path
            cleaned_path = root / "data/cleaned/v0.2/service-source/document.json"
            normalized_path.parent.mkdir(parents=True)
            cleaned_path.parent.mkdir(parents=True)
            normalized_path.write_text(
                normalized.model_dump_json(indent=2), encoding="utf-8"
            )
            cleaned_path.write_text(cleaned.model_dump_json(indent=2), encoding="utf-8")
            manifest = cleaned_path.parents[1] / "cleaned_corpus_manifest.jsonl"
            manifest.write_text(
                json.dumps(
                    {
                        "document_id": cleaned.document_id,
                        "source_id": cleaned.source_id,
                        "cleaned_path": cleaned_path.relative_to(root).as_posix(),
                    }
                )
                + "\n",
                encoding="utf-8",
            )
            service = ChunkPipelineService(
                root,
                _small_config(),
                cleaned_root=manifest.parent,
            )

            validate_only = service.run(validate_only=True)
            written = service.run(validate_only=False)

            self.assertEqual(validate_only.summary["status"], "validated")
            self.assertIsNone(validate_only.manifest_path)
            self.assertIsNotNone(written.manifest_path)
            assert written.manifest_path is not None
            self.assertTrue(written.manifest_path.is_file())
            output = root / "data/chunked/v1/service-source/chunks.json"
            self.assertTrue(output.is_file())
            payload = json.loads(output.read_text(encoding="utf-8"))
            self.assertTrue(payload["validation"]["valid"])


class ChunkValidatorFailureTests(unittest.TestCase):
    def test_relationship_id_breadcrumb_and_metadata_corruption_are_rejected(
        self,
    ) -> None:
        blocks = [
            DocumentBlock(
                block_id="validation-b0",
                block_type=BlockType.PARAGRAPH,
                text="第一段包含足够多的中文内容。" * 5,
                order_index=0,
            ),
            DocumentBlock(
                block_id="validation-b1",
                block_type=BlockType.PARAGRAPH,
                text="第二段继续提供用于拆分的中文内容。" * 5,
                order_index=1,
            ),
        ]
        cleaned, normalized = _document(blocks)
        config = _small_config()
        result = ChunkEngine(config).chunk(cleaned, normalized)
        self.assertGreater(len(result.children), 1)
        corrupted = list(result.children)
        corrupted[0] = corrupted[0].model_copy(
            update={
                "parent_id": "parent-missing",
                "next_chunk_id": None,
                "breadcrumb": "错误路径",
                "metadata": {},
            }
        )
        corrupted[1] = corrupted[1].model_copy(
            update={"chunk_id": corrupted[0].chunk_id}
        )

        report = ChunkValidator(config).validate(
            cleaned,
            result.parents,
            corrupted,
            [],
            {block.block_id: block for block in normalized.blocks},
        )
        codes = {issue.code for issue in report.issues}

        self.assertFalse(report.valid)
        self.assertTrue(
            {
                "invalid_parent_id",
                "invalid_next_chunk_id",
                "duplicate_chunk_id",
                "breadcrumb_mismatch",
                "metadata_not_inherited",
            }.issubset(codes)
        )

    def test_changed_table_row_is_rejected(self) -> None:
        rows = [["曲目", "年份"], ["七里香", "2004"]]
        block = DocumentBlock(
            block_id="validation-table-block",
            block_type=BlockType.TABLE,
            text="not consumed",
            order_index=0,
            metadata={"rows": rows, "has_header": True},
        )
        table = DocumentTable(
            table_id=block.block_id,
            markdown="not consumed",
            rows=rows,
            metadata={"source_block_id": block.block_id, "has_header": True},
        )
        cleaned, normalized = _document([block], tables=[table])
        config = _small_config()
        result = ChunkEngine(config).chunk(cleaned, normalized)
        corrupted = list(result.children)
        data = corrupted[0].table_data
        assert data is not None
        corrupted[0] = corrupted[0].model_copy(
            update={"table_data": data.model_copy(update={"rows": [["损坏", "2004"]]})}
        )

        report = ChunkValidator(config).validate(
            cleaned,
            result.parents,
            corrupted,
            [table],
            {block.block_id: block},
        )

        self.assertFalse(report.valid)
        self.assertIn("table_rows_changed", {issue.code for issue in report.issues})


class RealCleanOutputEndToEndTests(unittest.TestCase):
    def test_real_v02_clean_document_to_valid_parent_and_child_chunks(self) -> None:
        cleaned_path = AI_SERVICE_ROOT / "data/cleaned/v0.2/jc-v1-001/document.json"
        normalized_path = AI_SERVICE_ROOT / "data/normalized/jc-v1-001/document.json"
        if not cleaned_path.is_file() or not normalized_path.is_file():
            self.skipTest("project Clean v0.2 fixture is not available")
        cleaned = CleanedDocument.model_validate_json(
            cleaned_path.read_text(encoding="utf-8")
        )
        normalized = NormalizedDocument.model_validate_json(
            normalized_path.read_text(encoding="utf-8")
        )

        result = ChunkEngine().chunk(cleaned, normalized)

        self.assertTrue(result.validation.valid, result.validation.issues)
        self.assertGreater(result.stats.parent_count, 0)
        self.assertGreater(result.stats.child_count, 0)
        combined = "\n".join(value.content for value in result.parents)
        self.assertIn("周杰倫(Jay Chou", combined)
        self.assertNotIn("NEWS 最新消息", combined)
        self.assertEqual(
            result.children[0].breadcrumb,
            "周杰倫藝人介紹與作品年表 > 藝人專區",
        )


if __name__ == "__main__":
    unittest.main()
