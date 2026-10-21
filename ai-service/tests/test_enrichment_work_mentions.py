from unittest.mock import AsyncMock, patch

import pytest

from app.rag.enrichment import (
    EnrichmentConfig,
    MetadataEnrichmentPipeline,
    RuleEnricher,
    SemanticEnricher,
)
from app.rag.enrichment.hashing import enrichment_input_hash
from app.rag.enrichment.inheritance import MetadataInheritanceResolver
from app.rag.enrichment.models import EntityTypeHint, MentionSource
from app.rag.knowledge.entity_linking.models import EntityRegistryDocument
from app.rag.knowledge.entity_linking.registry import EntityRegistry
from tests.test_metadata_enrichment import _child, _metadata, _parent


VARIANTS = ["《范特西》", "范特西", "专辑范特西", "周杰伦的范特西"]


def _registry(*aliases):
    return EntityRegistry(
        EntityRegistryDocument.model_validate(
            {
                "registry_version": "v9",
                "entities": [
                    {
                        "entity_id": "album:example",
                        "entity_type": "album",
                        "canonical_name": "试作",
                        "aliases": list(aliases),
                    },
                    {
                        "entity_id": "artist:example",
                        "entity_type": "artist",
                        "canonical_name": "仅是艺人",
                        "aliases": [],
                    },
                ],
            }
        )
    )


@pytest.mark.parametrize("content", VARIANTS)
@pytest.mark.parametrize("make_chunk", [_parent, _child], ids=["parent", "child"])
def test_requested_work_variants_are_rule_mentions(content, make_chunk):
    chunk = make_chunk(content)
    before = chunk.model_dump(mode="json")
    result = RuleEnricher().enrich(chunk)
    (work,) = [m for m in result.entities.mentions if m.text == "范特西"]
    assert work.source is MentionSource.RULE
    assert work.type_hint is (
        EntityTypeHint.ALBUM if content.startswith("专辑") else EntityTypeHint.UNKNOWN
    )
    assert work.normalized_text == "范特西"
    assert work.confidence == (0.65 if "《" in content else 0.85)
    assert chunk.model_dump(mode="json") == before


@pytest.mark.parametrize("content", ["《未知新作品》", "〈未知新作品〉"])
def test_quoted_work_rule_still_accepts_titles_outside_registry(content):
    assert any(
        m.text == "未知新作品"
        for m in RuleEnricher().enrich(_parent(content)).entities.mentions
    )


@pytest.mark.parametrize("content", ["Fantasy", "fantasy", "FANTASY"])
def test_latin_work_aliases_match_case_insensitively(content):
    result = RuleEnricher().enrich(_parent(content))
    (work,) = [
        m for m in result.entities.mentions if m.normalized_text.casefold() == "fantasy"
    ]
    assert work.text == content and work.type_hint is EntityTypeHint.UNKNOWN


@pytest.mark.parametrize(
    "content", ["Fantasyland", "preFantasy", "Fantasy_edition", "xFantasy9"]
)
def test_latin_work_aliases_do_not_match_inside_words(content):
    assert not any(
        m.text.casefold() == "fantasy"
        for m in RuleEnricher().enrich(_parent(content)).entities.mentions
    )


@pytest.mark.parametrize(
    "content", ["依然范特西", "周杰伦的依然范特西", "《依然范特西》"]
)
def test_longest_title_owns_its_span_without_shorter_nested_title(content):
    result = RuleEnricher().enrich(_parent(content))
    assert any(m.text == "依然范特西" for m in result.entities.mentions)
    assert not any(m.text == "范特西" for m in result.entities.mentions)


def test_ambiguous_work_surface_remains_unknown_until_entity_linking():
    result = RuleEnricher().enrich(_parent("七里香"))
    (work,) = [m for m in result.entities.mentions if m.text == "七里香"]
    assert work.type_hint is EntityTypeHint.UNKNOWN


@pytest.mark.parametrize(
    "content,expected",
    [
        ("专辑范特西", EntityTypeHint.ALBUM),
        ("專輯：范特西", EntityTypeHint.ALBUM),
        ("歌曲晴天", EntityTypeHint.SONG),
        ("单曲 晴天", EntityTypeHint.SONG),
        ("album Fantasy", EntityTypeHint.ALBUM),
        ("song Fantasy", EntityTypeHint.SONG),
    ],
)
def test_only_adjacent_explicit_cues_supply_work_type_hints(content, expected):
    result = RuleEnricher().enrich(_parent(content))
    (work,) = [m for m in result.entities.mentions if m.source is MentionSource.RULE]
    assert work.type_hint is expected


def test_registry_matching_does_not_resolve_entities_or_add_non_work_aliases():
    registry = _registry("新名")
    with patch.object(
        registry, "resolve", side_effect=AssertionError("entity_resolution_called")
    ):
        result = RuleEnricher(registry=registry).enrich(_parent("仅是艺人与新名。"))
    assert any(
        m.text == "新名" and m.type_hint is EntityTypeHint.UNKNOWN
        for m in result.entities.mentions
    )
    assert not any(m.text == "仅是艺人" for m in result.entities.mentions)


def test_metadata_mention_priority_and_deduplication_are_preserved():
    metadata = _metadata()
    metadata["entity_mentions"] = [
        {"text": "范特西", "type_hint": "album", "confidence": 1.0}
    ]
    result = RuleEnricher().enrich(_parent("《范特西》及范特西", metadata=metadata))
    (work,) = [m for m in result.entities.mentions if m.text == "范特西"]
    assert (
        work.source is MentionSource.METADATA and work.type_hint is EntityTypeHint.ALBUM
    )


def test_inherited_album_metadata_can_supply_an_unquoted_unknown_title():
    metadata = _metadata(artist=None)
    metadata["document_metadata"]["source_metadata"]["album"] = "未知无引号作品"
    result = RuleEnricher().enrich(_parent("未知无引号作品", metadata=metadata))
    assert any(
        m.text == "未知无引号作品" and m.source is MentionSource.METADATA
        for m in result.entities.mentions
    )


def test_unknown_unquoted_text_is_not_guessed_as_a_work():
    assert not any(
        m.text == "未知无引号作品"
        for m in RuleEnricher().enrich(_parent("未知无引号作品")).entities.mentions
    )


def test_many_work_mentions_respect_existing_contract_and_keep_metadata_first():
    content = "、".join(f"《新作品{index}》" for index in range(110))
    mentions = RuleEnricher().enrich(_parent(content)).entities.mentions
    assert len(mentions) == 100 and mentions[0].text == "周杰伦"
    assert mentions[0].source is MentionSource.METADATA


def test_vocabulary_fingerprint_is_stable_and_tracks_work_aliases():
    first = RuleEnricher(registry=_registry("Title", "TITLE"))
    reordered = RuleEnricher(registry=_registry("TITLE", "Title"))
    changed = RuleEnricher(registry=_registry("Title", "新名"))
    assert first.fingerprint() == reordered.fingerprint()
    assert first.fingerprint() != changed.fingerprint()


@pytest.mark.asyncio
async def test_rule_only_pipeline_preserves_source_without_model_calls():
    chunk = _parent("周杰伦的范特西")
    config = EnrichmentConfig(llm_enabled=False)
    runtime = AsyncMock()
    pipeline = MetadataEnrichmentPipeline(
        config, semantic_enricher=SemanticEnricher(config, runtime=runtime)
    )
    enriched = await pipeline.enrich_chunk(chunk)
    assert enriched.enrichment.quality.index_ready
    assert enriched.model_dump(exclude={"enrichment"}) == chunk.model_dump()
    assert any(m.text == "范特西" for m in enriched.enrichment.entities.mentions)
    runtime.generate.assert_not_called()


@pytest.mark.asyncio
async def test_changed_work_vocabulary_reprocesses_cached_enrichment_then_skips():
    chunk = _parent("新名")
    config = EnrichmentConfig(llm_enabled=False)
    first = await MetadataEnrichmentPipeline(
        config, rule_enricher=RuleEnricher(registry=_registry())
    ).enrich_chunk(chunk)
    assert not any(m.text == "新名" for m in first.enrichment.entities.mentions)
    updated = MetadataEnrichmentPipeline(
        config, rule_enricher=RuleEnricher(registry=_registry("新名"))
    )
    second = await updated.enrich_chunk(chunk, existing=first)
    assert (
        second.enrichment.provenance.input_hash
        != first.enrichment.provenance.input_hash
    )
    assert second.enrichment.quality.enrichment_status.value == "success"
    assert any(m.text == "新名" for m in second.enrichment.entities.mentions)
    third = await updated.enrich_chunk(chunk, existing=second)
    assert third.enrichment.quality.enrichment_status.value == "skipped"


@pytest.mark.asyncio
async def test_legacy_hash_does_not_skip_new_mention_rules():
    chunk = _parent("范特西")
    config = EnrichmentConfig(llm_enabled=False)
    pipeline = MetadataEnrichmentPipeline(config)
    current = await pipeline.enrich_chunk(chunk)
    context = MetadataInheritanceResolver().resolve(chunk)
    old_hash = enrichment_input_hash(
        chunk,
        title=context.title,
        inherited_metadata=context.inherited_metadata,
        parent_primary_subject=None,
        enrichment_version="v1",
        llm_enabled=False,
    )
    legacy = current.model_copy(
        update={
            "enrichment": current.enrichment.model_copy(
                update={
                    "entities": current.enrichment.entities.model_copy(
                        update={"mentions": []}
                    ),
                    "provenance": current.enrichment.provenance.model_copy(
                        update={"input_hash": old_hash}
                    ),
                }
            )
        }
    )
    refreshed = await pipeline.enrich_chunk(chunk, existing=legacy)
    assert refreshed.enrichment.quality.enrichment_status.value == "success"
    assert any(m.text == "范特西" for m in refreshed.enrichment.entities.mentions)
