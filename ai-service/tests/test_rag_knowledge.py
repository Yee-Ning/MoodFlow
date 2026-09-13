from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from app.rag.chunk.models import (
    ChunkType,
    ParentChunk,
    TableChunkData,
    TableHeaderState,
)
from app.rag.enrichment.models import (
    ChunkEnrichment,
    ContentMetadata,
    EnrichedChildChunk,
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
from app.rag.knowledge import (
    ClaimExtractor,
    ClaimVerifier,
    EntityLinker,
    EntityRegistry,
    KnowledgeConfig,
    KnowledgePipeline,
    KnowledgePipelineService,
    KnowledgeQualityReporter,
    KnowledgeQASampler,
    KnowledgeReleaseBuilder,
    KnowledgeStatus,
    KnowledgeValidator,
    VerificationStatus,
)
from app.rag.clean.io import atomic_write_json, atomic_write_jsonl
from app.rag.knowledge.config import DEFAULT_KNOWLEDGE_CONFIG_PATH
from app.rag.knowledge.entity_linking.registry import DEFAULT_ENTITY_REGISTRY_PATH
from app.rag.knowledge.claims import Claim, ClaimPredicate, ClaimSource, ClaimTime
from app.rag.knowledge.writer import KnowledgeDatasetWriter
from app.rag.knowledge.ownership import deduplicate_claim_ownership
from app.rag.knowledge.claims.models import ClaimExtractionOutput
from app.rag.knowledge.entity_linking.models import EntityDisambiguationOutput
from app.rag.knowledge.structured import strict_model_schema
from app.rag.knowledge.verification.models import ClaimVerification


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


def _enriched_child(
    content: str,
    *,
    mentions: list[tuple[str, EntityTypeHint]],
) -> EnrichedChildChunk:
    enrichment = _enriched(content, mentions=mentions).enrichment
    return EnrichedChildChunk(
        doc_id="doc-1",
        chunk_id="child-1",
        parent_id="parent-1",
        chunk_index=0,
        chunk_type=ChunkType.NARRATIVE,
        section_path=["音乐资料"],
        breadcrumb="音乐资料",
        content=content,
        token_count=max(1, len(content)),
        metadata={},
        retrieval_content=content,
        enrichment=enrichment,
    )


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
        self.assertNotIn("Jay", result.linked_entities[0].aliases)
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

    async def test_v2_registry_resolves_frequent_album_and_lyricist(self) -> None:
        chunk = _enriched(
            "方文山参与创作专辑《范特西》。",
            mentions=[
                ("方文山", EntityTypeHint.PERSON),
                ("范特西", EntityTypeHint.ALBUM),
            ],
        )

        result = await self.linker.link(chunk)

        self.assertEqual(
            {item.entity_id for item in result.linked_entities},
            {"person:vincent_fang", "album:fantasy"},
        )

    async def test_v3_registry_extends_v2_with_reviewed_high_frequency_entities(
        self,
    ) -> None:
        chunk = _enriched(
            "蔡依林演唱《告白气球》。",
            mentions=[
                ("蔡依林", EntityTypeHint.ARTIST),
                ("告白气球", EntityTypeHint.SONG),
            ],
        )

        result = await self.linker.link(chunk)

        self.assertEqual(self.registry.version, "v3")
        self.assertEqual(
            {item.entity_id for item in result.linked_entities},
            {"artist:jolin_tsai", "song:love_confession"},
        )

    async def test_person_hint_accepts_artist_but_not_album(self) -> None:
        artist_chunk = _enriched(
            "吴宗宪是音乐人。",
            mentions=[("吴宗宪", EntityTypeHint.PERSON)],
        )
        album_chunk = _enriched(
            "叶惠美是周杰伦的母亲。",
            mentions=[("叶惠美", EntityTypeHint.PERSON)],
        )

        artist_result = await self.linker.link(artist_chunk)
        album_result = await self.linker.link(album_chunk)

        self.assertEqual(
            [item.entity_id for item in artist_result.linked_entities],
            ["artist:jacky_wu"],
        )
        self.assertNotIn(
            "album:ye_hui_mei",
            {item.entity_id for item in album_result.linked_entities},
        )
        self.assertEqual(
            next(
                item
                for item in album_result.unresolved_mentions
                if item.normalized_text == "叶惠美"
            ).reason,
            "registry_candidate_type_mismatch",
        )

    async def test_source_publisher_and_non_music_organization_are_not_candidates(
        self,
    ) -> None:
        metadata = {
            "document_metadata": {
                "source_metadata": {"publisher": "Wikipedia"}
            }
        }
        chunk = _enriched(
            "Wikipedia 收录了相关资料。",
            mentions=[("Wikipedia", EntityTypeHint.ORGANIZATION)],
            metadata=metadata,
        )

        result = await self.linker.link(chunk)

        self.assertFalse(result.linked_entities)
        self.assertFalse(result.unresolved_mentions)

    async def test_ambiguous_jay_is_not_forced_to_album(self) -> None:
        chunk = _enriched(
            "Jay 在现场受到欢迎。",
            mentions=[("Jay", EntityTypeHint.UNKNOWN)],
        )

        result = await self.linker.link(chunk)

        self.assertFalse(result.linked_entities)
        self.assertEqual(result.unresolved_mentions[0].text, "Jay")
        self.assertEqual(
            result.unresolved_mentions[0].candidate_entity_ids,
            ["album:jay_2000", "artist:jay_chou"],
        )
        self.assertEqual(
            result.unresolved_mentions[0].reason,
            "registry_ambiguous",
        )

    async def test_context_can_resolve_an_ambiguous_album_name(self) -> None:
        chunk = _enriched(
            "这张专辑《Jay》于2000年发行。",
            mentions=[("Jay", EntityTypeHint.UNKNOWN)],
        )

        result = await self.linker.link(chunk)

        self.assertEqual([item.entity_id for item in result.linked_entities], ["album:jay_2000"])
        self.assertFalse(result.unresolved_mentions)

    async def test_unique_artist_alias_is_not_derailed_by_nearby_song_word(self) -> None:
        chunk = _enriched("周杰倫是著名流行歌曲男歌手。")

        result = await self.linker.link(chunk)

        self.assertEqual([item.entity_id for item in result.linked_entities], ["artist:jay_chou"])
        self.assertFalse(result.unresolved_mentions)

    async def test_section_path_disambiguates_album_name(self) -> None:
        chunk = _enriched("2004 七里香").model_copy(
            update={"section_path": ["藝人專區", "專輯"]}
        )

        result = await self.linker.link(chunk)

        self.assertEqual(
            [item.entity_id for item in result.linked_entities],
            ["album:qi_li_xiang"],
        )
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


class StaticKnowledgeRuntime:
    def __init__(self, response: dict[str, object]) -> None:
        self.response = response
        self.calls = 0

    async def generate(self, payload, **kwargs) -> str:  # type: ignore[no-untyped-def]
        self.calls += 1
        return json.dumps(self.response, ensure_ascii=False)


class StructuredOutputSchemaTests(unittest.TestCase):
    def test_all_provider_object_fields_are_required(self) -> None:
        for model in (
            EntityDisambiguationOutput,
            ClaimExtractionOutput,
            ClaimVerification,
        ):
            with self.subTest(model=model.__name__):
                self._assert_strict(strict_model_schema(model))

    def _assert_strict(self, value: object) -> None:
        if isinstance(value, list):
            for item in value:
                self._assert_strict(item)
            return
        if not isinstance(value, dict):
            return
        self.assertNotIn("default", value)
        properties = value.get("properties")
        if isinstance(properties, dict):
            self.assertEqual(set(value.get("required", [])), set(properties))
            self.assertFalse(value.get("additionalProperties"))
        for child in value.values():
            self._assert_strict(child)


class ClaimExtractionTests(unittest.IsolatedAsyncioTestCase):
    async def _entities(
        self, chunk: EnrichedParentChunk
    ):  # type: ignore[no-untyped-def]
        return await EntityLinker(KnowledgeConfig(llm_enabled=False)).link(chunk)

    async def test_complex_sentence_splits_into_atomic_claims(self) -> None:
        evidence = "2000年，周杰伦带来专辑Jay；这张作品运用了R&B和Hip-Hop，也标志着他的乐坛起点。"
        chunk = _enriched(
            evidence,
            mentions=[
                ("周杰伦", EntityTypeHint.ARTIST),
                ("Jay", EntityTypeHint.ALBUM),
                ("R&B", EntityTypeHint.GENRE),
                ("Hip-Hop", EntityTypeHint.GENRE),
            ],
        )
        runtime = StaticKnowledgeRuntime(
            {
                "claims": [
                    {
                        "statement": "周杰伦于2000年出道",
                        "subject_entity_id": "artist:jay_chou",
                        "predicate": "debuted",
                        "object_entity_id": None,
                        "object_value": "出道",
                        "time": {"year": 2000, "date": None, "start_year": None, "end_year": None, "raw": "2000年"},
                        "evidence_span": evidence,
                        "confidence": 0.99,
                    },
                    {
                        "statement": "周杰伦于2000年发行专辑Jay",
                        "subject_entity_id": "artist:jay_chou",
                        "predicate": "released",
                        "object_entity_id": "album:jay_2000",
                        "object_value": None,
                        "time": {"year": 2000, "date": None, "start_year": None, "end_year": None, "raw": "2000年"},
                        "evidence_span": evidence,
                        "confidence": 0.98,
                    },
                    {
                        "statement": "专辑Jay融合R&B元素",
                        "subject_entity_id": "album:jay_2000",
                        "predicate": "contains_music_element",
                        "object_entity_id": "genre:r_and_b",
                        "object_value": None,
                        "time": None,
                        "evidence_span": evidence,
                        "confidence": 0.96,
                    },
                    {
                        "statement": "专辑Jay融合Hip-Hop元素",
                        "subject_entity_id": "album:jay_2000",
                        "predicate": "contains_music_element",
                        "object_entity_id": "genre:hip_hop",
                        "object_value": None,
                        "time": None,
                        "evidence_span": evidence,
                        "confidence": 0.96,
                    },
                ]
            }
        )
        entities = await self._entities(chunk)
        extractor = ClaimExtractor(KnowledgeConfig(), runtime=runtime)

        result = await extractor.extract(chunk, entities)
        llm_claims = [
            claim for claim in result.claims if claim.extraction_source == ClaimSource.LLM
        ]

        self.assertEqual(len(llm_claims), 4)
        self.assertEqual(len({claim.claim_id for claim in llm_claims}), 4)
        self.assertTrue(all(claim.evidence_span == evidence for claim in llm_claims))

    async def test_subjective_statement_produces_no_rule_claim(self) -> None:
        chunk = _enriched(
            "我认为周杰伦是最伟大的歌手。",
            mentions=[("周杰伦", EntityTypeHint.ARTIST)],
        )
        entities = await self._entities(chunk)

        result = await ClaimExtractor(KnowledgeConfig(llm_enabled=False)).extract(
            chunk, entities
        )

        self.assertFalse(result.claims)

    async def test_negated_release_is_not_emitted_as_positive_rule_claim(self) -> None:
        chunk = _enriched(
            "专辑《依然范特西》尚未发行。",
            mentions=[("依然范特西", EntityTypeHint.ALBUM)],
        )
        entities = await self._entities(chunk)

        result = await ClaimExtractor(KnowledgeConfig(llm_enabled=False)).extract(
            chunk, entities
        )

        self.assertFalse(
            any(claim.predicate == ClaimPredicate.RELEASED for claim in result.claims)
        )

    async def test_creative_roles_award_and_performance_are_extracted_locally(
        self,
    ) -> None:
        chunk = _enriched(
            "《青花瓷》由方文山作词，周杰伦作曲，并获得金曲奖后举办演唱会。",
            mentions=[
                ("青花瓷", EntityTypeHint.SONG),
                ("方文山", EntityTypeHint.PERSON),
                ("周杰伦", EntityTypeHint.ARTIST),
                ("金曲奖", EntityTypeHint.AWARD),
            ],
        )
        entities = await self._entities(chunk)

        result = await ClaimExtractor(KnowledgeConfig(llm_enabled=False)).extract(
            chunk, entities
        )

        predicates = {claim.predicate for claim in result.claims}
        self.assertIn(ClaimPredicate.WROTE_LYRICS_FOR, predicates)
        self.assertIn(ClaimPredicate.COMPOSED_FOR, predicates)
        self.assertIn(ClaimPredicate.WON_AWARD, predicates)
        self.assertIn(ClaimPredicate.PERFORMED_AT, predicates)

    async def test_creative_roles_bind_to_the_nearest_named_person(self) -> None:
        chunk = _enriched(
            "青花瓷是方文山作词、钟兴民编曲、周杰伦作曲的歌曲。",
            mentions=[
                ("青花瓷", EntityTypeHint.SONG),
                ("方文山", EntityTypeHint.PERSON),
                ("钟兴民", EntityTypeHint.PERSON),
                ("周杰伦", EntityTypeHint.ARTIST),
            ],
        )
        entities = await self._entities(chunk)

        result = await ClaimExtractor(KnowledgeConfig(llm_enabled=False)).extract(
            chunk, entities
        )

        roles = {
            (claim.predicate, claim.subject_entity_id)
            for claim in result.claims
            if claim.predicate
            in {
                ClaimPredicate.WROTE_LYRICS_FOR,
                ClaimPredicate.ARRANGED,
                ClaimPredicate.COMPOSED_FOR,
            }
        }
        self.assertEqual(
            roles,
            {
                (ClaimPredicate.WROTE_LYRICS_FOR, "person:vincent_fang"),
                (ClaimPredicate.ARRANGED, "person:chung_hing_man"),
                (ClaimPredicate.COMPOSED_FOR, "artist:jay_chou"),
            },
        )

    async def test_negated_award_and_performance_are_not_emitted(self) -> None:
        chunk = _enriched(
            "八度空间虽然获得金曲奖提名，但没有获奖；方文山以不参加演唱会作为条件。",
            mentions=[
                ("八度空间", EntityTypeHint.ALBUM),
                ("金曲奖", EntityTypeHint.AWARD),
                ("方文山", EntityTypeHint.PERSON),
            ],
        )
        entities = await self._entities(chunk)

        result = await ClaimExtractor(KnowledgeConfig(llm_enabled=False)).extract(
            chunk, entities
        )

        self.assertNotIn(
            ClaimPredicate.WON_AWARD, {claim.predicate for claim in result.claims}
        )
        self.assertNotIn(
            ClaimPredicate.PERFORMED_AT, {claim.predicate for claim in result.claims}
        )

    async def test_release_only_binds_the_work_governed_by_release_cue(self) -> None:
        chunk = _enriched(
            "专辑依然范特西的名字来自周杰伦于2001年发行的专辑范特西。",
            mentions=[
                ("依然范特西", EntityTypeHint.ALBUM),
                ("周杰伦", EntityTypeHint.ARTIST),
                ("范特西", EntityTypeHint.ALBUM),
            ],
        )
        entities = await self._entities(chunk)

        result = await ClaimExtractor(KnowledgeConfig(llm_enabled=False)).extract(
            chunk, entities
        )

        released_objects = {
            claim.object_entity_id
            for claim in result.claims
            if claim.predicate == ClaimPredicate.RELEASED
        }
        self.assertEqual(released_objects, {"album:fantasy"})

    async def test_release_does_not_use_an_artist_mentioned_after_the_work(self) -> None:
        chunk = _enriched(
            "2008年发行第9张专辑魔杰座，成为继王菲之后的新纪录。",
            mentions=[
                ("魔杰座", EntityTypeHint.ALBUM),
                ("王菲", EntityTypeHint.ARTIST),
            ],
        )
        entities = await self._entities(chunk)

        result = await ClaimExtractor(KnowledgeConfig(llm_enabled=False)).extract(
            chunk, entities
        )

        release = next(
            claim
            for claim in result.claims
            if claim.predicate == ClaimPredicate.RELEASED
        )
        self.assertEqual(release.subject_entity_id, "album:capricorn")
        self.assertIsNone(release.object_entity_id)

    async def test_navigation_concert_label_is_not_a_performance(self) -> None:
        chunk = _enriched(
            "周杰伦 音乐作品 创作作品 演唱会 奖项与提名",
            mentions=[("周杰伦", EntityTypeHint.ARTIST)],
        )
        entities = await self._entities(chunk)

        result = await ClaimExtractor(KnowledgeConfig(llm_enabled=False)).extract(
            chunk, entities
        )

        self.assertNotIn(
            ClaimPredicate.PERFORMED_AT, {claim.predicate for claim in result.claims}
        )

    async def test_award_table_requires_an_explicit_result_cell(self) -> None:
        content = (
            "| 颁奖典礼 | 奖项 | 获奖专辑或歌曲 |\n"
            "| 2002年度金曲奖 | 最佳作词人奖 | 《爱在西元前》 |"
        )
        chunk = _enriched(
            content,
            mentions=[
                ("爱在西元前", EntityTypeHint.SONG),
                ("最佳作词人奖", EntityTypeHint.AWARD),
            ],
        ).model_copy(
            update={
                "table_data": TableChunkData(
                    table_id="table-award-1",
                    header_state=TableHeaderState.EXPLICIT,
                    headers=["颁奖典礼", "奖项", "获奖专辑或歌曲"],
                    rows=[["2002年度金曲奖", "最佳作词人奖", "《爱在西元前》"]],
                    row_start=0,
                    row_end=1,
                    source_row_count=1,
                )
            }
        )
        entities = await self._entities(chunk)

        result = await ClaimExtractor(KnowledgeConfig(llm_enabled=False)).extract(
            chunk, entities
        )

        self.assertNotIn(
            ClaimPredicate.WON_AWARD, {claim.predicate for claim in result.claims}
        )

    async def test_winner_profile_birth_dates_bind_to_the_declared_winner(self) -> None:
        content = (
            "最佳编曲人奖 得奖者：钟兴民 得奖者简介：1966年1月26日生于台南。"
            "最佳专辑制作人奖 得奖者：周杰伦 得奖者简介：1979年1月18日生。"
        )
        chunk = _enriched(
            content,
            mentions=[
                ("钟兴民", EntityTypeHint.PERSON),
                ("周杰伦", EntityTypeHint.ARTIST),
            ],
        )
        entities = await self._entities(chunk)

        result = await ClaimExtractor(KnowledgeConfig(llm_enabled=False)).extract(
            chunk, entities
        )
        births = {
            (claim.subject_entity_id, claim.object_value)
            for claim in result.claims
            if claim.predicate == ClaimPredicate.BORN_ON
        }

        self.assertEqual(
            births,
            {
                ("person:chung_hing_man", "1966-01-26"),
                ("artist:jay_chou", "1979-01-18"),
            },
        )

    async def test_auction_clothing_is_not_a_performance_claim(self) -> None:
        chunk = _enriched(
            "在一场拍卖中，周杰伦的演唱会服饰是拍品之一。",
            mentions=[("周杰伦", EntityTypeHint.ARTIST)],
        )
        entities = await self._entities(chunk)

        result = await ClaimExtractor(KnowledgeConfig(llm_enabled=False)).extract(
            chunk, entities
        )

        self.assertNotIn(
            ClaimPredicate.PERFORMED_AT, {claim.predicate for claim in result.claims}
        )

    async def test_comparison_artist_is_not_attributed_to_the_current_concert(self) -> None:
        chunk = _enriched(
            "在日本武道馆连开两场演唱会，成为继王菲之后第二位在武道馆开唱的华人歌手。",
            mentions=[("王菲", EntityTypeHint.ARTIST)],
        )
        entities = await self._entities(chunk)

        result = await ClaimExtractor(KnowledgeConfig(llm_enabled=False)).extract(
            chunk, entities
        )

        self.assertNotIn(
            ClaimPredicate.PERFORMED_AT, {claim.predicate for claim in result.claims}
        )

    async def test_collaboration_question_is_not_a_fact_claim(self) -> None:
        chunk = _enriched(
            "那么究竟是怎样的契机让方文山和周杰伦有合作？",
            mentions=[
                ("方文山", EntityTypeHint.PERSON),
                ("周杰伦", EntityTypeHint.ARTIST),
            ],
        )
        entities = await self._entities(chunk)

        result = await ClaimExtractor(KnowledgeConfig(llm_enabled=False)).extract(
            chunk, entities
        )

        self.assertNotIn(
            ClaimPredicate.COLLABORATED_WITH,
            {claim.predicate for claim in result.claims},
        )

    async def test_track_lists_do_not_bridge_to_another_album(self) -> None:
        content = (
            "像是「霍元甲」收录在「十一月的萧邦」专辑中、"
            "「不能说的秘密」及「周大侠」收录在「我很忙」专辑。"
        )
        chunk = _enriched(
            content,
            mentions=[
                ("霍元甲", EntityTypeHint.SONG),
                ("十一月的萧邦", EntityTypeHint.ALBUM),
                ("我很忙", EntityTypeHint.ALBUM),
            ],
        )
        entities = await self._entities(chunk)

        result = await ClaimExtractor(KnowledgeConfig(llm_enabled=False)).extract(
            chunk, entities
        )
        track_pairs = {
            (claim.subject_entity_id, claim.object_entity_id)
            for claim in result.claims
            if claim.predicate == ClaimPredicate.INCLUDES_TRACK
        }

        self.assertEqual(track_pairs, {("album:novembers_chopin", "song:fearless")})

    async def test_single_column_table_does_not_use_header_only_as_evidence(self) -> None:
        content = "| 青花瓷 |\n| 收录于专辑《我很忙》 |"
        chunk = _enriched(
            content,
            mentions=[
                ("青花瓷", EntityTypeHint.SONG),
                ("我很忙", EntityTypeHint.ALBUM),
            ],
        ).model_copy(
            update={
                "table_data": TableChunkData(
                    table_id="table-track-header-1",
                    header_state=TableHeaderState.EXPLICIT,
                    headers=["青花瓷"],
                    rows=[["收录于专辑《我很忙》"]],
                    row_start=0,
                    row_end=1,
                    source_row_count=1,
                )
            }
        )
        entities = await self._entities(chunk)

        result = await ClaimExtractor(KnowledgeConfig(llm_enabled=False)).extract(
            chunk, entities
        )

        self.assertNotIn(
            ClaimPredicate.INCLUDES_TRACK, {claim.predicate for claim in result.claims}
        )

    async def test_table_row_is_used_only_when_exactly_grounded_in_content(self) -> None:
        content = "| 专辑 | 收录歌曲 |\n| 七里香 | 晴天 |"
        chunk = _enriched(
            content,
            mentions=[
                ("七里香", EntityTypeHint.ALBUM),
                ("晴天", EntityTypeHint.SONG),
            ],
        ).model_copy(
            update={
                "table_data": TableChunkData(
                    table_id="table-1",
                    header_state=TableHeaderState.EXPLICIT,
                    headers=["专辑", "收录歌曲"],
                    rows=[["七里香", "晴天"]],
                    row_start=0,
                    row_end=1,
                    source_row_count=1,
                )
            }
        )
        entities = await self._entities(chunk)

        result = await ClaimExtractor(KnowledgeConfig(llm_enabled=False)).extract(
            chunk, entities
        )

        track_claims = [
            claim
            for claim in result.claims
            if claim.predicate == ClaimPredicate.INCLUDES_TRACK
        ]
        self.assertEqual(len(track_claims), 1)
        self.assertEqual(track_claims[0].evidence_span, "| 七里香 | 晴天 |")

        verification = await ClaimVerifier(
            KnowledgeConfig(llm_enabled=False)
        ).verify(track_claims[0], chunk)
        self.assertEqual(verification.status, VerificationStatus.SUPPORTED)

    async def test_rule_claim_evidence_exists_verbatim_in_chunk(self) -> None:
        chunk = _enriched(
            "周杰伦于2000年出道。专辑七里香收录歌曲晴天。",
            mentions=[
                ("周杰伦", EntityTypeHint.ARTIST),
                ("七里香", EntityTypeHint.ALBUM),
                ("晴天", EntityTypeHint.SONG),
            ],
        )
        entities = await self._entities(chunk)

        result = await ClaimExtractor(KnowledgeConfig(llm_enabled=False)).extract(
            chunk, entities
        )

        self.assertGreaterEqual(len(result.claims), 2)
        self.assertTrue(
            all(claim.evidence_span in chunk.content for claim in result.claims)
        )

    async def test_claim_id_is_deterministic_for_same_source(self) -> None:
        chunk = _enriched(
            "周杰伦于2000年出道。",
            mentions=[("周杰伦", EntityTypeHint.ARTIST)],
        )
        entities = await self._entities(chunk)
        extractor = ClaimExtractor(KnowledgeConfig(llm_enabled=False))

        first = await extractor.extract(chunk, entities)
        second = await extractor.extract(chunk, entities)

        self.assertEqual(
            [claim.claim_id for claim in first.claims],
            [claim.claim_id for claim in second.claims],
        )

    async def test_reference_sentence_yields_four_rule_facts(self) -> None:
        chunk = _enriched(
            "2000年周杰伦发行首张专辑《Jay》，作品融合R&B和Hip-Hop元素。",
            mentions=[
                ("周杰伦", EntityTypeHint.ARTIST),
                ("Jay", EntityTypeHint.ALBUM),
                ("R&B", EntityTypeHint.GENRE),
                ("Hip-Hop", EntityTypeHint.GENRE),
            ],
        )
        entities = await self._entities(chunk)

        result = await ClaimExtractor(KnowledgeConfig(llm_enabled=False)).extract(
            chunk, entities
        )

        self.assertEqual(len(result.claims), 4)
        self.assertEqual(
            {claim.predicate for claim in result.claims},
            {
                ClaimPredicate.RELEASED,
                ClaimPredicate.FIRST_ALBUM_OF,
                ClaimPredicate.CONTAINS_MUSIC_ELEMENT,
            },
        )
        self.assertEqual(
            sum(
                claim.predicate == ClaimPredicate.CONTAINS_MUSIC_ELEMENT
                for claim in result.claims
            ),
            2,
        )


def _claim(
    chunk: EnrichedParentChunk,
    *,
    statement: str,
    evidence: str,
    source: ClaimSource = ClaimSource.LLM,
) -> Claim:
    return Claim(
        claim_id="claim:" + ("a" * 24),
        statement=statement,
        subject_entity_id="artist:jay_chou",
        predicate=ClaimPredicate.DEBUTED,
        object_value="出道",
        time=ClaimTime(year=1999, raw="1999年"),
        source_chunk_id=chunk.chunk_id,
        evidence_span=evidence,
        confidence=0.9,
        extraction_source=source,
    )


class ClaimVerificationTests(unittest.IsolatedAsyncioTestCase):
    async def test_hallucinated_claim_is_insufficient_without_source_support(self) -> None:
        chunk = _enriched(
            "周杰伦于2000年出道。",
            mentions=[("周杰伦", EntityTypeHint.ARTIST)],
        )
        claim = _claim(
            chunk,
            statement="周杰伦于1999年出道",
            evidence="周杰伦于2000年出道。",
        )

        verification = await ClaimVerifier(
            KnowledgeConfig(llm_enabled=False)
        ).verify(claim, chunk)

        self.assertEqual(
            verification.status, VerificationStatus.INSUFFICIENT_EVIDENCE
        )
        self.assertIsNone(verification.evidence_span)
        self.assertEqual(verification.reason, "claim_time_not_grounded")

    async def test_explicit_negation_is_contradicted(self) -> None:
        content = "周杰伦并未发行专辑Jay。"
        chunk = _enriched(
            content,
            mentions=[
                ("周杰伦", EntityTypeHint.ARTIST),
                ("Jay", EntityTypeHint.ALBUM),
            ],
        )
        claim = Claim(
            claim_id="claim:" + ("b" * 24),
            statement="周杰伦发行了专辑Jay",
            subject_entity_id="artist:jay_chou",
            predicate=ClaimPredicate.RELEASED,
            object_entity_id="album:jay_2000",
            source_chunk_id=chunk.chunk_id,
            evidence_span=content,
            confidence=0.9,
            extraction_source=ClaimSource.RULE,
        )

        verification = await ClaimVerifier(
            KnowledgeConfig(llm_enabled=False)
        ).verify(claim, chunk)

        self.assertEqual(verification.status, VerificationStatus.CONTRADICTED)
        self.assertEqual(verification.evidence_span, content)


class FailingEntityLinker:
    def __init__(self) -> None:
        self.delegate = EntityLinker(KnowledgeConfig(llm_enabled=False))

    async def link(self, chunk):  # type: ignore[no-untyped-def]
        if "触发失败" in chunk.content:
            raise RuntimeError("isolated_entity_link_failure")
        return await self.delegate.link(chunk)


class ExplodingKnowledgePipeline:
    async def process_batch(self, *args, **kwargs):  # type: ignore[no-untyped-def]
        raise AssertionError("validate-only must not execute knowledge pipeline")


class StaticKnowledgeReader:
    def __init__(self, chunks):  # type: ignore[no-untyped-def]
        self.chunks = chunks

    def load_all(self):  # type: ignore[no-untyped-def]
        return self.chunks


class KnowledgePipelineTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.config = KnowledgeConfig(llm_enabled=False, max_concurrency=2)

    async def test_pipeline_preserves_enriched_chunk_and_adds_knowledge(self) -> None:
        chunk = _enriched(
            "周杰伦于2000年出道。",
            mentions=[("周杰伦", EntityTypeHint.ARTIST)],
        )

        result = await KnowledgePipeline(self.config).process(chunk)

        self.assertEqual(
            result.model_dump(mode="json", exclude={"knowledge"}),
            chunk.model_dump(mode="json"),
        )
        self.assertEqual(result.knowledge.quality.status, KnowledgeStatus.SUCCESS)
        self.assertEqual(result.knowledge.stats.entity_count, 1)
        self.assertEqual(result.knowledge.stats.claim_count, 1)
        self.assertEqual(
            result.knowledge.claims[0].verification.status,
            VerificationStatus.SUPPORTED,
        )

    async def test_successful_unchanged_result_is_skipped(self) -> None:
        chunk = _enriched(
            "周杰伦于2000年出道。",
            mentions=[("周杰伦", EntityTypeHint.ARTIST)],
        )
        pipeline = KnowledgePipeline(self.config)
        first = await pipeline.process(chunk)

        second = await pipeline.process(first)

        self.assertEqual(second.knowledge.quality.status, KnowledgeStatus.SKIPPED)
        self.assertEqual(
            second.knowledge.provenance.knowledge_input_hash,
            first.knowledge.provenance.knowledge_input_hash,
        )
        self.assertIn("unchanged_input_and_versions", second.knowledge.quality.warnings)

    async def test_batch_failure_is_isolated_to_one_chunk(self) -> None:
        good = _enriched(
            "周杰伦于2000年出道。",
            mentions=[("周杰伦", EntityTypeHint.ARTIST)],
            chunk_id="good",
        )
        bad = _enriched("这条内容触发失败。", chunk_id="bad")
        pipeline = KnowledgePipeline(
            self.config,
            entity_linker=FailingEntityLinker(),  # type: ignore[arg-type]
        )

        results = await pipeline.process_batch([good, bad])

        self.assertEqual(results[0].knowledge.quality.status, KnowledgeStatus.SUCCESS)
        self.assertEqual(results[1].knowledge.quality.status, KnowledgeStatus.FAILED)
        self.assertIn("isolated_entity_link_failure", results[1].knowledge.quality.errors)

    async def test_writer_round_trip_keeps_full_knowledge_structure(self) -> None:
        chunk = _enriched(
            "周杰伦于2000年出道。",
            mentions=[("周杰伦", EntityTypeHint.ARTIST)],
        )
        result = await KnowledgePipeline(self.config).process(chunk)
        project_root = Path(__file__).resolve().parents[1]
        with tempfile.TemporaryDirectory(dir=project_root) as temporary:
            writer = KnowledgeDatasetWriter(project_root, Path(temporary))

            chunks_path, manifest_path = writer.write([result])
            loaded = writer.load_existing()

            self.assertTrue(chunks_path.is_file())
            self.assertTrue(manifest_path.is_file())
            self.assertEqual(loaded[result.chunk_id], result)

    async def test_validate_only_reads_persisted_rows_without_running_pipeline(
        self,
    ) -> None:
        source = _enriched(
            "周杰伦于2000年出道。",
            mentions=[("周杰伦", EntityTypeHint.ARTIST)],
        )
        result = await KnowledgePipeline(self.config).process(source)
        project_root = Path(__file__).resolve().parents[1]
        with tempfile.TemporaryDirectory(dir=project_root) as temporary:
            output_root = Path(temporary) / "knowledge" / "v1"
            KnowledgeDatasetWriter(project_root, output_root).write([result])
            service = KnowledgePipelineService(
                project_root,
                self.config,
                enriched_path=Path(temporary) / "unused.jsonl",
                output_root=output_root,
                pipeline=ExplodingKnowledgePipeline(),  # type: ignore[arg-type]
            )
            service.reader = StaticKnowledgeReader([source])  # type: ignore[assignment]

            outcome = await service.run(validate_only=True, sample_size=0)

            self.assertEqual(outcome.summary["status"], "validated")
            self.assertEqual(outcome.summary["total"], 1)
            self.assertEqual(
                outcome.summary["claims"], result.knowledge.stats.claim_count
            )
            self.assertIsNone(outcome.chunks_path)

    async def test_knowledge_validator_rejects_inconsistent_stats(self) -> None:
        source = _enriched(
            "周杰伦于2000年出道。",
            mentions=[("周杰伦", EntityTypeHint.ARTIST)],
        )
        result = await KnowledgePipeline(self.config).process(source)
        invalid_knowledge = result.knowledge.model_copy(
            update={
                "stats": result.knowledge.stats.model_copy(
                    update={"claim_count": result.knowledge.stats.claim_count + 1}
                )
            }
        )
        invalid = result.model_copy(update={"knowledge": invalid_knowledge})

        with self.assertRaisesRegex(ValueError, "knowledge_stats_mismatch"):
            KnowledgeValidator().validate(source, invalid)

    async def test_duplicate_parent_child_claim_is_owned_by_child(self) -> None:
        content = "周杰伦于2000年出道。"
        mentions = [("周杰伦", EntityTypeHint.ARTIST)]
        parent = _enriched(content, mentions=mentions)
        child = _enriched_child(content, mentions=mentions)
        pipeline = KnowledgePipeline(self.config)
        parent_result = await pipeline.process(parent)
        child_result = await pipeline.process(child)

        results = deduplicate_claim_ownership([parent_result, child_result])

        self.assertEqual(results[0].knowledge.stats.claim_count, 0)
        self.assertEqual(results[1].knowledge.stats.claim_count, 1)

    async def test_quality_report_writes_unresolved_registry_candidates(self) -> None:
        source = _enriched(
            "未知音乐人参与合作。",
            mentions=[("未知音乐人", EntityTypeHint.ARTIST)],
        )
        result = await KnowledgePipeline(self.config).process(source)
        project_root = Path(__file__).resolve().parents[1]
        with tempfile.TemporaryDirectory(dir=project_root) as temporary:
            reporter = KnowledgeQualityReporter(project_root, Path(temporary))

            report_path, candidates_path, report = reporter.write([result])

            self.assertTrue(report_path.is_file())
            self.assertTrue(candidates_path.is_file())
            self.assertEqual(
                report["entity_linking"]["unresolved_occurrences"],  # type: ignore[index]
                1,
            )
            self.assertTrue(
                report["quality_gates"][  # type: ignore[index]
                    "core_entity_resolution_target_met"
                ]
            )
            candidate = json.loads(candidates_path.read_text(encoding="utf-8"))
            self.assertEqual(candidate["normalized_text"], "未知音乐人")


class KnowledgeReleaseTests(unittest.IsolatedAsyncioTestCase):
    async def _fixture(
        self, root: Path
    ) -> tuple[KnowledgeReleaseBuilder, Path, Path]:
        project_root = Path(__file__).resolve().parents[1]
        source = _enriched(
            "2000年周杰伦发行首张专辑《Jay》。",
            mentions=[
                ("周杰伦", EntityTypeHint.ARTIST),
                ("Jay", EntityTypeHint.ALBUM),
            ],
        )
        result = await KnowledgePipeline(
            KnowledgeConfig(llm_enabled=False)
        ).process(source)
        enriched_path = root / "enriched" / "enriched_chunks.jsonl"
        source_root = root / "knowledge" / "v1"
        review_root = root / "knowledge" / "review-v2"
        output_root = root / "knowledge" / "v2"
        atomic_write_jsonl(enriched_path, [source.model_dump(mode="json")])
        KnowledgeDatasetWriter(project_root, source_root).write([result])
        KnowledgeQualityReporter(project_root, source_root).write([result])
        builder = KnowledgeReleaseBuilder(
            project_root,
            source_root=source_root,
            output_root=output_root,
            review_root=review_root,
            enriched_path=enriched_path,
            config_path=DEFAULT_KNOWLEDGE_CONFIG_PATH,
            registry_path=DEFAULT_ENTITY_REGISTRY_PATH,
            claim_sample_size=1,
            entity_sample_size=1,
        )
        return builder, review_root, output_root

    async def test_qa_sampler_is_deterministic_and_keeps_review_context(self) -> None:
        source = _enriched(
            "周杰伦于2000年出道。",
            mentions=[("周杰伦", EntityTypeHint.ARTIST)],
        )
        result = await KnowledgePipeline(
            KnowledgeConfig(llm_enabled=False)
        ).process(source)
        sampler = KnowledgeQASampler(claim_sample_size=1, entity_sample_size=1)

        first_claims = sampler.claim_rows([result])
        second_claims = sampler.claim_rows([result])
        entities = sampler.entity_rows([result])

        self.assertEqual(first_claims, second_claims)
        self.assertEqual(first_claims[0]["review_status"], "pending")
        self.assertEqual(first_claims[0]["evidence_span"], source.content)
        self.assertEqual(entities[0]["matched_surfaces"], ["周杰伦"])
        self.assertIn("周杰伦", entities[0]["context_excerpt"])

    async def test_release_requires_bound_review_attestation(self) -> None:
        project_root = Path(__file__).resolve().parents[1]
        with tempfile.TemporaryDirectory(dir=project_root) as temporary:
            root = Path(temporary)
            builder, _, _ = await self._fixture(root)
            builder.prepare_review()

            with self.assertRaisesRegex(ValueError, "review_missing"):
                builder.finalize()

    async def test_release_is_immutable_and_idempotent(self) -> None:
        project_root = Path(__file__).resolve().parents[1]
        with tempfile.TemporaryDirectory(dir=project_root) as temporary:
            root = Path(temporary)
            builder, review_root, output_root = await self._fixture(root)
            prepared = builder.prepare_review()
            sampling = json.loads(
                (review_root / "qa_sampling_manifest.json").read_text(
                    encoding="utf-8"
                )
            )
            atomic_write_json(
                review_root / "qa_review_attestation.json",
                {
                    "attestation_version": "v1",
                    "reviewer": "test-reviewer",
                    "review_status": "approved",
                    "claim_sample_sha256": sampling["claim_sample_sha256"],
                    "entity_link_sample_sha256": sampling[
                        "entity_link_sample_sha256"
                    ],
                    "claim_reviewed_count": sampling["claim_sample_count"],
                    "entity_link_reviewed_count": sampling[
                        "entity_link_sample_count"
                    ],
                    "generation_mode": "rule_only",
                    "rejected_sample_ids": [],
                    "notes": "fixture review",
                },
            )

            created = builder.finalize()
            unchanged = builder.finalize()

            self.assertEqual(prepared.status, "review_prepared")
            self.assertEqual(created.status, "created")
            self.assertEqual(unchanged.status, "unchanged")
            self.assertEqual(created.release_id, unchanged.release_id)
            self.assertTrue((output_root / "knowledge_release_manifest.json").is_file())
            release = json.loads(
                (output_root / "knowledge_release_manifest.json").read_text(
                    encoding="utf-8"
                )
            )
            self.assertTrue(release["quality"]["pre_embedding_release_ready"])
            self.assertIn("embedding", release["excluded_stages"])

            atomic_write_json(output_root / "knowledge_quality_report.json", {})
            with self.assertRaisesRegex(ValueError, "knowledge_release_conflict"):
                builder.finalize()


if __name__ == "__main__":
    unittest.main()
