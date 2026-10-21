"""Offline CLI and cache regression coverage for the approved Stage 2 scope."""

import json
from unittest.mock import AsyncMock

import pytest

import run_knowledge
from app.rag.enrichment.config import EnrichmentConfig
from app.rag.enrichment.models import EnrichmentStatus, SemanticStatus
from app.rag.enrichment.pipeline import MetadataEnrichmentPipeline
from app.rag.enrichment.semantic_enricher import SemanticEnricher
from app.rag.knowledge import KnowledgeConfig, KnowledgePipeline, KnowledgeStatus
from app.rag.knowledge.claims.models import ClaimExtractionResult
from app.rag.knowledge.config import DEFAULT_KNOWLEDGE_CONFIG_PATH
from app.rag.knowledge.entity_linking.models import EntityLinkingResult
from tests.test_metadata_enrichment import StaticRuntime, _parent, _semantic_output
from tests.test_rag_knowledge import _enriched


@pytest.mark.parametrize(
    ("config_name", "registry_version"),
    [(None, "v3"), ("v14.yaml", "v4"), ("v18.yaml", "v5")],
)
def test_knowledge_cli_loads_configured_registry(
    tmp_path, capsys, config_name, registry_version
):
    source = tmp_path / "enriched.jsonl"
    source.write_text(
        _enriched("这是离线夹具，没有模型调用。").model_dump_json() + "\n",
        encoding="utf-8",
    )
    args = [
        "--enriched-path",
        str(source),
        "--output-root",
        str(tmp_path / "output"),
        "--rule-only",
        "--sample-size",
        "0",
    ]
    if config_name is not None:
        args += ["--config", str(DEFAULT_KNOWLEDGE_CONFIG_PATH.with_name(config_name))]
    assert run_knowledge.main(args, project_root=tmp_path) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["registry_version"] == registry_version
    persisted = json.loads(
        (tmp_path / "output/knowledge_enriched_chunks.jsonl").read_text(
            encoding="utf-8"
        )
    )
    assert persisted["knowledge"]["provenance"]["registry_version"] == registry_version


def test_knowledge_cli_rejects_missing_registry(tmp_path, capsys):
    config = tmp_path / "config.yaml"
    config.write_text("registry_version: v999\nllm_enabled: false\n", encoding="utf-8")
    result = run_knowledge.main(
        ["--config", str(config), "--enriched-path", str(tmp_path / "input.jsonl")],
        project_root=tmp_path,
    )
    assert result == 2
    assert "entity_registry_invalid" in json.loads(capsys.readouterr().out)["error"]


@pytest.mark.asyncio
@pytest.mark.parametrize("initial_mode", [False, True])
async def test_enrichment_mode_switch_reprocesses_and_same_mode_skips(initial_mode):
    source = _parent("离线音乐资料。")
    runtime = StaticRuntime([_semantic_output()])

    def pipeline(mode):
        config = EnrichmentConfig(llm_enabled=mode)
        return MetadataEnrichmentPipeline(
            config, semantic_enricher=SemanticEnricher(config, runtime=runtime)
        )

    first = await pipeline(initial_mode).enrich_chunk(source)
    second_pipeline = pipeline(not initial_mode)
    second = await second_pipeline.enrich_chunk(source, existing=first)
    third = await second_pipeline.enrich_chunk(source, existing=second)
    assert (
        first.enrichment.provenance.input_hash
        != second.enrichment.provenance.input_hash
    )
    assert second.enrichment.quality.enrichment_status == EnrichmentStatus.SUCCESS
    expected = SemanticStatus.DISABLED if initial_mode else SemanticStatus.SUCCESS
    assert second.enrichment.quality.semantic_status == expected
    assert third.enrichment.quality.enrichment_status == EnrichmentStatus.SKIPPED
    assert runtime.calls == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("initial_mode", [False, True])
async def test_knowledge_mode_switch_reprocesses_and_same_mode_skips(initial_mode):
    source = _enriched("离线音乐资料。")
    linker = AsyncMock()
    linker.link.return_value = EntityLinkingResult()
    extractor = AsyncMock()
    extractor.extract.return_value = ClaimExtractionResult()

    def pipeline(mode):
        return KnowledgePipeline(
            KnowledgeConfig(llm_enabled=mode),
            entity_linker=linker,
            claim_extractor=extractor,
        )

    first = await pipeline(initial_mode).process(source)
    second_pipeline = pipeline(not initial_mode)
    second = await second_pipeline.process(source, existing=first)
    third = await second_pipeline.process(source, existing=second)
    assert (
        first.knowledge.provenance.knowledge_input_hash
        != second.knowledge.provenance.knowledge_input_hash
    )
    assert second.knowledge.quality.status == KnowledgeStatus.SUCCESS
    assert third.knowledge.quality.status == KnowledgeStatus.SKIPPED
    assert linker.link.await_count == extractor.extract.await_count == 2
