from __future__ import annotations

import asyncio
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from app.rag.chunk.models import ChildChunk, ChunkType, ParentChunk
from app.rag.enrichment import (
    EnrichmentConfig,
    EnrichmentStatus,
    MetadataEnrichmentPipeline,
    MetadataEnrichmentService,
    RuleEnricher,
    SemanticStatus,
    SemanticEnricher,
    StructuralStatus,
)
from app.rag.enrichment.models import QualityMetadata
from app.rag.enrichment.writer import EnrichmentDatasetWriter


AI_SERVICE_ROOT = Path(__file__).resolve().parents[1]


def _metadata(
    *,
    artist: str | None = "周杰伦",
    published_at: str | None = None,
    topics: list[str] | None = None,
) -> dict[str, object]:
    source_metadata: dict[str, object] = {
        "source_id": "source-1",
        "title": "周杰伦音乐资料",
        "url": "https://example.test/music",
        "publisher": "示例音乐资料库",
        "published_at": published_at,
        "topics": topics or [],
    }
    if artist is not None:
        source_metadata["artist"] = artist
    return {
        "source_id": "source-1",
        "source": "示例音乐资料库",
        "source_type": "official_profile",
        "document_type": "artist",
        "title": "周杰伦音乐资料",
        "published_at": published_at,
        "language": "zh-TW",
        "document_metadata": {"source_metadata": source_metadata},
    }


def _parent(
    content: str,
    *,
    metadata: dict[str, object] | None = None,
    chunk_id: str = "parent-1",
) -> ParentChunk:
    return ParentChunk(
        doc_id="doc-1",
        chunk_id=chunk_id,
        chunk_index=0,
        chunk_type=ChunkType.NARRATIVE,
        section_path=["生涯"],
        breadcrumb="周杰伦音乐资料 > 生涯",
        content=content,
        token_count=max(1, len(content)),
        metadata=metadata or _metadata(),
    )


def _child(
    content: str,
    *,
    metadata: dict[str, object] | None = None,
    parent_id: str = "parent-1",
) -> ChildChunk:
    return ChildChunk(
        doc_id="doc-1",
        chunk_id="child-1",
        parent_id=parent_id,
        chunk_index=0,
        chunk_type=ChunkType.NARRATIVE,
        section_path=["生涯"],
        breadcrumb="周杰伦音乐资料 > 生涯",
        content=content,
        token_count=max(1, len(content)),
        metadata=metadata or _metadata(),
        retrieval_content=f"周杰伦音乐资料 > 生涯\n\n{content}",
    )


def _semantic_output(
    *,
    content_type: str = "career_event",
    topics: list[str] | None = None,
    keywords: list[str] | None = None,
    mentions: list[dict[str, object]] | None = None,
    primary: dict[str, object] | None = None,
) -> str:
    return json.dumps(
        {
            "content_type": content_type,
            "topics": topics or [],
            "keywords": keywords or [],
            "entity_mentions": mentions or [],
            "primary_subject": primary,
        },
        ensure_ascii=False,
    )


class StaticRuntime:
    def __init__(self, responses: list[str | Exception], *, delay: float = 0) -> None:
        self.responses = list(responses)
        self.delay = delay
        self.calls = 0

    async def generate(self, payload, **kwargs) -> str:  # type: ignore[no-untyped-def]
        self.calls += 1
        if self.delay:
            await asyncio.sleep(self.delay)
        response = self.responses[min(self.calls - 1, len(self.responses) - 1)]
        if isinstance(response, Exception):
            raise response
        return response


class ContentRuntime:
    def __init__(self) -> None:
        self.calls = 0

    async def generate(self, payload, **kwargs) -> str:  # type: ignore[no-untyped-def]
        self.calls += 1
        content = payload["chunk_content"]
        if "坏输出" in content:
            return "not-json"
        return _semantic_output(
            topics=["音乐生涯"],
            mentions=[{"text": "周杰伦", "type_hint": "artist", "confidence": 0.99}],
            primary={"text": "周杰伦", "type_hint": "artist", "confidence": 0.99},
        )


class ExplodingEnrichmentPipeline:
    async def enrich_batch(self, *args, **kwargs):  # type: ignore[no-untyped-def]
        raise AssertionError("validate-only must not execute enrichment")


class StaticCorpusReader:
    def load_all(self) -> list[object]:
        return []


def _pipeline(
    runtime: StaticRuntime | ContentRuntime,
    *,
    timeout: float = 1,
    retry_count: int = 0,
) -> MetadataEnrichmentPipeline:
    config = EnrichmentConfig(
        timeout_seconds=timeout,
        retry_count=retry_count,
        batch_size=4,
        max_concurrency=2,
    )
    return MetadataEnrichmentPipeline(
        config,
        semantic_enricher=SemanticEnricher(config, runtime=runtime),
    )


class MetadataEnrichmentTests(unittest.IsolatedAsyncioTestCase):
    async def test_rule_enricher_core_interface_resolves_context(self) -> None:
        result = RuleEnricher().enrich(_parent("周杰伦于2000年出道。"))

        self.assertEqual(result.content.language, "zh-TW")
        self.assertEqual(result.temporal.year, 2000)
        self.assertEqual(result.entities.primary_subject.text, "周杰伦")

    async def test_rule_only_is_structurally_successful_and_index_ready(self) -> None:
        chunk = _parent("周杰伦于2000年出道。")

        enriched = await MetadataEnrichmentPipeline(
            EnrichmentConfig(llm_enabled=False)
        ).enrich_chunk(chunk)

        quality = enriched.enrichment.quality
        self.assertEqual(quality.enrichment_status, EnrichmentStatus.SUCCESS)
        self.assertEqual(quality.structural_status, StructuralStatus.SUCCESS)
        self.assertEqual(quality.semantic_status, SemanticStatus.DISABLED)
        self.assertTrue(quality.index_ready)

    async def test_legacy_partial_quality_is_usable_and_semantically_rejected(
        self,
    ) -> None:
        quality = QualityMetadata.model_validate(
            {
                "enrichment_status": "partial",
                "errors": ["ungrounded_entity_mention"],
            }
        )

        self.assertEqual(quality.structural_status, StructuralStatus.SUCCESS)
        self.assertEqual(quality.semantic_status, SemanticStatus.REJECTED)
        self.assertTrue(quality.index_ready)

    async def test_artist_career_chunk_preserves_content_and_enriches(self) -> None:
        chunk = _parent("周杰伦于2000年出道，开始其音乐生涯。")
        runtime = StaticRuntime(
            [
                _semantic_output(
                    topics=["音乐生涯"],
                    keywords=["出道"],
                    mentions=[
                        {
                            "text": "周杰伦",
                            "type_hint": "artist",
                            "confidence": 0.99,
                        }
                    ],
                    primary={
                        "text": "周杰伦",
                        "type_hint": "artist",
                        "confidence": 0.99,
                    },
                )
            ]
        )

        enriched = await _pipeline(runtime).enrich_chunk(chunk)

        self.assertEqual(enriched.content, chunk.content)
        self.assertEqual(
            enriched.enrichment.quality.enrichment_status,
            EnrichmentStatus.SUCCESS,
        )
        self.assertEqual(enriched.enrichment.content.language, "zh-TW")
        self.assertEqual(enriched.enrichment.content.content_type, "career_event")
        self.assertEqual(enriched.enrichment.temporal.year, 2000)
        self.assertEqual(enriched.enrichment.semantic.topics, ["音乐生涯"])
        self.assertEqual(enriched.enrichment.entities.primary_subject.text, "周杰伦")
        self.assertFalse(enriched.enrichment.quality.metadata_conflicts)

    async def test_artist_album_and_song_mentions_remain_separate(self) -> None:
        chunk = _parent("艺人周杰伦的专辑七里香收录歌曲晴天。")
        runtime = StaticRuntime(
            [
                _semantic_output(
                    content_type="album_description",
                    mentions=[
                        {"text": "周杰伦", "type_hint": "artist", "confidence": 1},
                        {"text": "七里香", "type_hint": "album", "confidence": 0.9},
                        {"text": "晴天", "type_hint": "song", "confidence": 0.9},
                    ],
                    primary={"text": "七里香", "type_hint": "album", "confidence": 0.8},
                )
            ]
        )

        enriched = await _pipeline(runtime).enrich_chunk(chunk)

        by_text = {
            item.text: item.type_hint.value
            for item in enriched.enrichment.entities.mentions
        }
        self.assertEqual(by_text["周杰伦"], "artist")
        self.assertEqual(by_text["七里香"], "album")
        self.assertEqual(by_text["晴天"], "song")
        self.assertEqual(enriched.enrichment.entities.primary_subject.text, "周杰伦")
        self.assertEqual(
            enriched.enrichment.quality.metadata_conflicts[0].field,
            "entities.primary_subject",
        )

    async def test_multiple_years_do_not_create_an_invented_single_year(self) -> None:
        chunk = _parent("他于2000年出道，并在2004年发行新作品。")
        enriched = await _pipeline(StaticRuntime([_semantic_output()])).enrich_chunk(
            chunk
        )

        self.assertIsNone(enriched.enrichment.temporal.year)
        self.assertEqual(
            enriched.enrichment.temporal.raw_mentions, ["2000年", "2004年"]
        )

    async def test_explicit_time_range_is_preserved_without_collapsing(self) -> None:
        chunk = _parent("2019-2025年间举办嘉年华世界巡回演唱会。")
        enriched = await _pipeline(StaticRuntime([_semantic_output()])).enrich_chunk(
            chunk
        )

        self.assertIsNone(enriched.enrichment.temporal.year)
        self.assertEqual(enriched.enrichment.temporal.start_year, 2019)
        self.assertEqual(enriched.enrichment.temporal.end_year, 2025)
        self.assertEqual(enriched.enrichment.temporal.raw_mentions, ["2019-2025年"])

    async def test_more_than_fifty_time_mentions_are_deduplicated_and_capped(
        self,
    ) -> None:
        content = "、".join(f"{year}年" for year in range(1900, 1960))

        enriched = await _pipeline(StaticRuntime([_semantic_output()])).enrich_chunk(
            _parent(content)
        )

        self.assertEqual(len(enriched.enrichment.temporal.raw_mentions), 50)
        self.assertEqual(enriched.enrichment.temporal.raw_mentions[0], "1900年")
        self.assertEqual(enriched.enrichment.temporal.raw_mentions[-1], "1949年")
        self.assertNotEqual(
            enriched.enrichment.quality.enrichment_status,
            EnrichmentStatus.FAILED,
        )

    async def test_source_published_at_is_not_content_event_time(self) -> None:
        chunk = _parent(
            "这是一段没有事件日期的音乐介绍。",
            metadata=_metadata(published_at="2024-06-02"),
        )
        enriched = await _pipeline(StaticRuntime([_semantic_output()])).enrich_chunk(
            chunk
        )

        self.assertEqual(enriched.enrichment.source.source_published_at, "2024-06-02")
        self.assertIsNone(enriched.enrichment.temporal.year)
        self.assertIsNone(enriched.enrichment.temporal.date)
        self.assertEqual(enriched.enrichment.temporal.raw_mentions, [])

    async def test_copyright_year_is_not_treated_as_event_time(self) -> None:
        chunk = _parent("杰威尔音乐有限公司\n© Copyright 2019")
        enriched = await _pipeline(StaticRuntime([_semantic_output()])).enrich_chunk(
            chunk
        )

        self.assertIsNone(enriched.enrichment.temporal.year)
        self.assertEqual(enriched.enrichment.temporal.raw_mentions, [])

    async def test_subjective_review_uses_controlled_content_type(self) -> None:
        chunk = _parent("我认为这张专辑的编曲细腻，但后半段略显重复。")
        enriched = await _pipeline(
            StaticRuntime(
                [_semantic_output(content_type="review", topics=["专辑评价"])]
            )
        ).enrich_chunk(chunk)

        self.assertEqual(enriched.enrichment.content.content_type, "review")
        self.assertEqual(enriched.enrichment.semantic.topics, ["专辑评价"])

    async def test_ambiguous_surface_mention_is_not_resolved(self) -> None:
        chunk = _parent("歌曲中出现了Apple这个名称，但上下文未说明其身份。")
        enriched = await _pipeline(
            StaticRuntime(
                [
                    _semantic_output(
                        mentions=[
                            {"text": "Apple", "type_hint": "unknown", "confidence": 0.3}
                        ]
                    )
                ]
            )
        ).enrich_chunk(chunk)

        mention = next(
            item
            for item in enriched.enrichment.entities.mentions
            if item.text == "Apple"
        )
        self.assertEqual(mention.normalized_text, "Apple")
        self.assertEqual(mention.type_hint, "unknown")
        self.assertNotIn("entity_id", mention.model_dump())

    async def test_child_inherits_parent_primary_subject(self) -> None:
        parent = _parent("周杰伦的音乐生涯。")
        child_metadata = _metadata(artist=None)
        child = _child("这一阶段的创作风格发生变化。", metadata=child_metadata)
        runtime = StaticRuntime(
            [
                _semantic_output(
                    primary={"text": "周杰伦", "type_hint": "artist", "confidence": 1}
                ),
                _semantic_output(),
            ]
        )
        pipeline = _pipeline(runtime)

        parent_enriched = await pipeline.enrich_chunk(parent)
        child_enriched = await pipeline.enrich_chunk(child, parent=parent_enriched)

        self.assertEqual(
            child_enriched.enrichment.entities.primary_subject.text, "周杰伦"
        )

    async def test_timeout_and_invalid_output_degrade_without_chunk_loss(self) -> None:
        cases = (
            (
                StaticRuntime([_semantic_output()], delay=0.05),
                0.01,
                "semantic_enrichment_timeout",
            ),
            (StaticRuntime(["not-json"]), 1, "semantic_enrichment_invalid_output"),
        )
        for runtime, timeout, expected_error in cases:
            with self.subTest(expected_error=expected_error):
                chunk = _parent("周杰伦的音乐生涯。")
                enriched = await _pipeline(runtime, timeout=timeout).enrich_chunk(chunk)
                self.assertEqual(enriched.content, chunk.content)
                self.assertEqual(
                    enriched.enrichment.quality.enrichment_status,
                    EnrichmentStatus.PARTIAL,
                )
                self.assertIn(expected_error, enriched.enrichment.quality.errors)

    async def test_invalid_structured_output_is_retried_then_validated(self) -> None:
        runtime = StaticRuntime(["not-json", _semantic_output(topics=["重试成功"])])

        enriched = await _pipeline(runtime, retry_count=1).enrich_chunk(
            _parent("周杰伦的音乐生涯。")
        )

        self.assertEqual(runtime.calls, 2)
        self.assertEqual(
            enriched.enrichment.quality.enrichment_status,
            EnrichmentStatus.SUCCESS,
        )
        self.assertEqual(enriched.enrichment.semantic.topics, ["重试成功"])

    async def test_same_input_and_versions_skip_second_llm_call(self) -> None:
        runtime = StaticRuntime([_semantic_output()])
        pipeline = _pipeline(runtime)
        first = await pipeline.enrich_chunk(_parent("周杰伦于2000年出道。"))

        second = await pipeline.enrich_chunk(first)

        self.assertEqual(runtime.calls, 1)
        self.assertEqual(
            second.enrichment.quality.enrichment_status, EnrichmentStatus.SKIPPED
        )
        self.assertEqual(
            second.enrichment.provenance.input_hash,
            first.enrichment.provenance.input_hash,
        )

    async def test_changed_content_recomputes_enrichment(self) -> None:
        runtime = StaticRuntime([_semantic_output()])
        pipeline = _pipeline(runtime)
        first = await pipeline.enrich_chunk(_parent("周杰伦于2000年出道。"))
        changed = first.model_copy(update={"content": "周杰伦于2001年推出新作品。"})

        second = await pipeline.enrich_chunk(changed)

        self.assertEqual(runtime.calls, 2)
        self.assertEqual(
            second.enrichment.quality.enrichment_status, EnrichmentStatus.SUCCESS
        )
        self.assertNotEqual(
            first.enrichment.provenance.input_hash,
            second.enrichment.provenance.input_hash,
        )
        self.assertEqual(second.enrichment.temporal.year, 2001)

    async def test_batch_failure_is_isolated_to_one_chunk(self) -> None:
        runtime = ContentRuntime()
        pipeline = _pipeline(runtime)
        good = _parent("周杰伦于2000年出道。", chunk_id="parent-good")
        bad = _parent("周杰伦对应坏输出。", chunk_id="parent-bad")

        results = await pipeline.enrich_batch([good, bad])

        statuses = {
            item.chunk_id: item.enrichment.quality.enrichment_status for item in results
        }
        self.assertEqual(statuses["parent-good"], EnrichmentStatus.SUCCESS)
        self.assertEqual(statuses["parent-bad"], EnrichmentStatus.PARTIAL)

    async def test_exact_mentions_are_deduplicated_but_aliases_are_not(self) -> None:
        chunk = _parent("周杰伦也被称为周董，英文名为Jay Chou。")
        runtime = StaticRuntime(
            [
                _semantic_output(
                    mentions=[
                        {"text": "周杰伦", "type_hint": "artist", "confidence": 1},
                        {"text": "周杰伦", "type_hint": "artist", "confidence": 0.9},
                        {"text": "周董", "type_hint": "artist", "confidence": 0.8},
                        {"text": "Jay Chou", "type_hint": "artist", "confidence": 0.8},
                    ]
                )
            ]
        )

        enriched = await _pipeline(runtime).enrich_chunk(chunk)
        texts = [item.text for item in enriched.enrichment.entities.mentions]

        self.assertEqual(texts.count("周杰伦"), 1)
        self.assertIn("周董", texts)
        self.assertIn("Jay Chou", texts)

    async def test_writer_uses_separate_jsonl_keyed_by_chunk_id(self) -> None:
        chunk = await _pipeline(StaticRuntime([_semantic_output()])).enrich_chunk(
            _parent("周杰伦于2000年出道。")
        )
        with tempfile.TemporaryDirectory(dir=AI_SERVICE_ROOT) as directory:
            output_root = Path(directory) / "enriched" / "v1"
            writer = EnrichmentDatasetWriter(AI_SERVICE_ROOT, output_root)

            chunks_path, manifest_path = writer.write([chunk])

            row = json.loads(chunks_path.read_text(encoding="utf-8"))
            self.assertEqual(row["chunk_id"], chunk.chunk_id)
            self.assertEqual(row["content"], chunk.content)
            self.assertIn("enrichment", row)
            self.assertTrue(manifest_path.is_file())

    async def test_validate_only_reads_persisted_rows_without_running_pipeline(
        self,
    ) -> None:
        source = _parent("周杰伦于2000年出道。")
        config = EnrichmentConfig(llm_enabled=False)
        enriched = await MetadataEnrichmentPipeline(config).enrich_chunk(source)
        with tempfile.TemporaryDirectory(dir=AI_SERVICE_ROOT) as directory:
            output_root = Path(directory) / "enriched" / "v1"
            EnrichmentDatasetWriter(AI_SERVICE_ROOT, output_root).write([enriched])
            service = MetadataEnrichmentService(
                AI_SERVICE_ROOT,
                config,
                chunked_root=Path(directory),
                output_root=output_root,
                pipeline=ExplodingEnrichmentPipeline(),  # type: ignore[arg-type]
            )
            service.reader = StaticCorpusReader()  # type: ignore[assignment]

            with patch.object(
                MetadataEnrichmentService, "_flatten", return_value=[source]
            ):
                outcome = await service.run(validate_only=True, sample_size=0)

            self.assertEqual(outcome.summary["status"], "validated")
            self.assertEqual(outcome.summary["total"], 1)
            self.assertIsNone(outcome.chunks_path)

    async def test_retry_failed_only_preserves_non_failed_rows(self) -> None:
        source = _parent("周杰伦于2000年出道。")
        config = EnrichmentConfig(llm_enabled=False)
        enriched = await MetadataEnrichmentPipeline(config).enrich_chunk(source)
        with tempfile.TemporaryDirectory(dir=AI_SERVICE_ROOT) as directory:
            output_root = Path(directory) / "enriched" / "v1"
            writer = EnrichmentDatasetWriter(AI_SERVICE_ROOT, output_root)
            writer.write([enriched])
            service = MetadataEnrichmentService(
                AI_SERVICE_ROOT,
                config,
                chunked_root=Path(directory),
                output_root=output_root,
            )
            service.reader = StaticCorpusReader()  # type: ignore[assignment]

            with patch.object(
                MetadataEnrichmentService, "_flatten", return_value=[source]
            ):
                outcome = await service.run(retry_failed_only=True, sample_size=0)

            self.assertEqual(outcome.summary["total"], 0)
            self.assertEqual(writer.load_existing()[source.chunk_id], enriched)


if __name__ == "__main__":
    unittest.main()
