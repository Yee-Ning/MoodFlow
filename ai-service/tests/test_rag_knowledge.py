from __future__ import annotations

import unittest

from app.rag.chunk.models import ChunkType, ParentChunk
from app.rag.enrichment.models import (
    ChunkEnrichment,
    ContentMetadata,
    EnrichedParentChunk,
    EnrichmentStatus,
    EntityMention,
    EntityMetadata,
    EntityTypeHint,
    MentionSource,
    PrimarySubject,
    ProvenanceMetadata,
    QualityMetadata,
    SemanticMetadata,
    SourceMetadata,
    TemporalMetadata,
)
from app.rag.knowledge import EntityLinker, EntityRegistry, KnowledgeConfig


def _enriched(
    content: str,
    *,
    mentions: list[tuple[str, EntityTypeHint]] | None = None,
    primary: tuple[str, EntityTypeHint] | None = None,
    chunk_id: str = "parent-1",
    metadata: dict[str, object] | None = None,
) -> EnrichedParentChunk:
    base = ParentChunk(
        doc_id="doc-1",
        chunk_id=chunk_id,
        chunk_index=0,
        chunk_type=ChunkType.NARRATIVE,
        section_path=["音乐资料"],
        breadcrumb="音乐资料",
        content=content,
        token_count=max(1, len(content)),
        metadata=metadata or {},
    )
    enrichment = ChunkEnrichment(
        content=ContentMetadata(language="zh-CN"),
        semantic=SemanticMetadata(),
        temporal=TemporalMetadata(),
        entities=EntityMetadata(
            primary_subject=(
                PrimarySubject(text=primary[0], type_hint=primary[1], confidence=0.99)
                if primary
                else None
            ),
            mentions=[
                EntityMention(
                    text=text,
                    normalized_text=text,
                    type_hint=entity_type,
                    confidence=0.95,
                    source=MentionSource.RULE,
                )
                for text, entity_type in (mentions or [])
            ],
        ),
        source=SourceMetadata(source_id="source-1"),
        quality=QualityMetadata(enrichment_status=EnrichmentStatus.SUCCESS),
        provenance=ProvenanceMetadata(
            enrichment_version="v1",
            schema_version="v1",
            prompt_version="v1",
            input_hash="sha256:" + ("0" * 64),
        ),
    )
    return EnrichedParentChunk(**base.model_dump(), enrichment=enrichment)


class EntityLinkingTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.registry = EntityRegistry.load()
        self.linker = EntityLinker(
            KnowledgeConfig(llm_enabled=False), registry=self.registry
        )

    async def test_aliases_resolve_to_one_canonical_artist(self) -> None:
        chunk = _enriched(
            "周杰倫又被称为周董，英文名常写作 Jay Chou。",
            mentions=[
                ("周杰倫", EntityTypeHint.ARTIST),
                ("周董", EntityTypeHint.ARTIST),
                ("Jay Chou", EntityTypeHint.ARTIST),
            ],
        )

        result = await self.linker.link(chunk)

        self.assertEqual([item.entity_id for item in result.linked_entities], ["artist:jay_chou"])
        self.assertEqual(result.linked_entities[0].canonical_name, "周杰伦")
        self.assertIn("周董", result.linked_entities[0].aliases)
        self.assertFalse(result.unresolved_mentions)

    async def test_artist_album_and_song_keep_distinct_entity_types(self) -> None:
        chunk = _enriched(
            "艺人周杰伦的专辑七里香收录歌曲晴天。",
            mentions=[
                ("周杰伦", EntityTypeHint.ARTIST),
                ("七里香", EntityTypeHint.ALBUM),
                ("晴天", EntityTypeHint.SONG),
            ],
        )

        result = await self.linker.link(chunk)

        self.assertEqual(
            {item.entity_id for item in result.linked_entities},
            {"artist:jay_chou", "album:qi_li_xiang", "song:qing_tian"},
        )

    async def test_ambiguous_jay_is_not_forced_to_album(self) -> None:
        chunk = _enriched(
            "Jay 在现场受到欢迎。",
            mentions=[("Jay", EntityTypeHint.UNKNOWN)],
        )

        result = await self.linker.link(chunk)

        self.assertFalse(result.linked_entities)
        self.assertEqual(result.unresolved_mentions[0].text, "Jay")
        self.assertEqual(
            result.unresolved_mentions[0].candidate_entity_ids, ["album:jay_2000"]
        )
        self.assertEqual(
            result.unresolved_mentions[0].reason,
            "registry_alias_requires_context",
        )

    async def test_context_can_resolve_an_ambiguous_album_name(self) -> None:
        chunk = _enriched(
            "这张专辑《Jay》于2000年发行。",
            mentions=[("Jay", EntityTypeHint.UNKNOWN)],
        )

        result = await self.linker.link(chunk)

        self.assertEqual([item.entity_id for item in result.linked_entities], ["album:jay_2000"])
        self.assertFalse(result.unresolved_mentions)

    async def test_entity_id_is_stable_across_chunks_and_aliases(self) -> None:
        first = _enriched(
            "周董创作了许多作品。",
            mentions=[("周董", EntityTypeHint.ARTIST)],
            chunk_id="parent-1",
        )
        second = _enriched(
            "Jay Chou 是音乐人。",
            mentions=[("Jay Chou", EntityTypeHint.ARTIST)],
            chunk_id="parent-2",
        )

        first_result = await self.linker.link(first)
        second_result = await self.linker.link(second)

        self.assertEqual(
            first_result.linked_entities[0].entity_id,
            second_result.linked_entities[0].entity_id,
        )


if __name__ == "__main__":
    unittest.main()
