"""Async orchestration for rule-first, failure-isolated metadata enrichment."""

from __future__ import annotations

import asyncio
from collections.abc import Mapping, Sequence

from .config import EnrichmentConfig
from .hashing import enrichment_input_hash
from .inheritance import MetadataInheritanceResolver
from .merger import MetadataMerger
from .models import (
    BaseChunk,
    ChildChunk,
    ChunkEnrichment,
    ContentMetadata,
    EnrichedChildChunk,
    EnrichedChunk,
    EnrichedParentChunk,
    EnrichmentStatus,
    EntityMetadata,
    ParentChunk,
    ProvenanceMetadata,
    QualityMetadata,
    SemanticMetadata,
    SourceMetadata,
    TemporalMetadata,
)
from .normalizer import EnrichmentNormalizer, deduplicate_strings
from .rule_enricher import RuleEnricher
from .semantic_enricher import SemanticEnricher
from .validator import EnrichmentValidator


class MetadataEnrichmentPipeline:
    def __init__(
        self,
        config: EnrichmentConfig | None = None,
        *,
        inheritance: MetadataInheritanceResolver | None = None,
        rule_enricher: RuleEnricher | None = None,
        semantic_enricher: SemanticEnricher | None = None,
        normalizer: EnrichmentNormalizer | None = None,
        merger: MetadataMerger | None = None,
        validator: EnrichmentValidator | None = None,
    ) -> None:
        self.config = config or EnrichmentConfig()
        self.normalizer = normalizer or EnrichmentNormalizer()
        self.inheritance = inheritance or MetadataInheritanceResolver()
        self.rule_enricher = rule_enricher or RuleEnricher()
        self.semantic_enricher = semantic_enricher or SemanticEnricher(
            self.config, normalizer=self.normalizer
        )
        self.merger = merger or MetadataMerger(self.normalizer)
        self.validator = validator or EnrichmentValidator()

    async def enrich_chunk(
        self,
        chunk: BaseChunk | EnrichedChunk,
        *,
        parent: EnrichedParentChunk | None = None,
        existing: EnrichedChunk | None = None,
    ) -> EnrichedChunk:
        base = self._base_chunk(chunk)
        prior = existing or (chunk if self._is_enriched(chunk) else None)
        provenance = self._fallback_provenance(base)
        source = self._fallback_source(base)
        try:
            context = self.inheritance.resolve(base, parent=parent)
            source = context.source
            provenance = ProvenanceMetadata(
                enrichment_version=self.config.enrichment_version,
                schema_version=self.config.schema_version,
                prompt_version=self.config.prompt_version,
                input_hash=enrichment_input_hash(
                    base,
                    title=context.title,
                    inherited_metadata=context.inherited_metadata,
                    parent_primary_subject=context.parent_primary_subject,
                    enrichment_version=self.config.enrichment_version,
                ),
            )
            if prior is not None and self._can_skip(prior, provenance):
                skipped = prior.enrichment.model_copy(
                    update={
                        "quality": prior.enrichment.quality.model_copy(
                            update={
                                "enrichment_status": EnrichmentStatus.SKIPPED,
                                "warnings": deduplicate_strings(
                                    [
                                        *prior.enrichment.quality.warnings,
                                        "unchanged_input_and_version",
                                    ],
                                    limit=100,
                                ),
                            }
                        )
                    }
                )
                result = self._attach(base, skipped)
                self.validator.validate(base, result)
                return result

            rule = self.normalizer.normalize_rule(
                self.rule_enricher.enrich(base, context)
            )
            semantic = None
            warnings: list[str] = []
            errors: list[str] = []
            status = EnrichmentStatus.SUCCESS
            if self.config.llm_enabled:
                try:
                    semantic = await self.semantic_enricher.enrich(base, context)
                    status = EnrichmentStatus.SUCCESS
                except Exception as exc:
                    status = EnrichmentStatus.PARTIAL
                    errors.append(self._safe_error(exc))
                    warnings.append("semantic_enrichment_unavailable")
            else:
                warnings.append("semantic_enrichment_disabled")
            enrichment = self.merger.merge(
                rule,
                semantic,
                provenance=provenance,
                status=status,
                warnings=warnings,
                errors=errors,
            )
            result = self._attach(base, enrichment)
            self.validator.validate(base, result)
            return result
        except Exception as exc:
            return self._failed(base, source, provenance, exc)

    async def enrich_batch(
        self,
        chunks: Sequence[BaseChunk | EnrichedChunk],
        *,
        existing_by_id: Mapping[str, EnrichedChunk] | None = None,
    ) -> list[EnrichedChunk]:
        existing_by_id = existing_by_id or {}
        semaphore = asyncio.Semaphore(self.config.max_concurrency)
        indexed = list(enumerate(chunks))
        results: dict[int, EnrichedChunk] = {}
        enriched_parents: dict[str, EnrichedParentChunk] = {}

        async def run_one(
            index: int,
            value: BaseChunk | EnrichedChunk,
            parent: EnrichedParentChunk | None,
        ) -> tuple[int, EnrichedChunk]:
            async with semaphore:
                result = await self.enrich_chunk(
                    value,
                    parent=parent,
                    existing=existing_by_id.get(value.chunk_id),
                )
                return index, result

        parent_rows = [item for item in indexed if self._is_parent(item[1])]
        for batch in self._batches(parent_rows):
            completed = await asyncio.gather(
                *(run_one(index, value, None) for index, value in batch)
            )
            for index, result in completed:
                results[index] = result
                if isinstance(result, EnrichedParentChunk):
                    enriched_parents[result.chunk_id] = result

        child_rows = [item for item in indexed if not self._is_parent(item[1])]
        for batch in self._batches(child_rows):
            completed = await asyncio.gather(
                *(
                    run_one(
                        index,
                        value,
                        enriched_parents.get(value.parent_id),
                    )
                    for index, value in batch
                )
            )
            for index, result in completed:
                results[index] = result
        return [results[index] for index in range(len(chunks))]

    def _failed(
        self,
        base: BaseChunk,
        source: SourceMetadata,
        provenance: ProvenanceMetadata,
        error: Exception,
    ) -> EnrichedChunk:
        enrichment = ChunkEnrichment(
            content=ContentMetadata(),
            semantic=SemanticMetadata(),
            temporal=TemporalMetadata(),
            entities=EntityMetadata(),
            source=source,
            quality=QualityMetadata(
                enrichment_status=EnrichmentStatus.FAILED,
                warnings=["enrichment_pipeline_failed"],
                errors=[self._safe_error(error)],
            ),
            provenance=provenance,
        )
        return self._attach(base, enrichment)

    @staticmethod
    def _can_skip(prior: EnrichedChunk, provenance: ProvenanceMetadata) -> bool:
        old = prior.enrichment.provenance
        return (
            prior.enrichment.quality.enrichment_status
            in {EnrichmentStatus.SUCCESS, EnrichmentStatus.SKIPPED}
            and old.input_hash == provenance.input_hash
            and old.enrichment_version == provenance.enrichment_version
            and old.schema_version == provenance.schema_version
            and old.prompt_version == provenance.prompt_version
        )

    def _fallback_provenance(self, base: BaseChunk) -> ProvenanceMetadata:
        title = base.metadata.get("title")
        return ProvenanceMetadata(
            enrichment_version=self.config.enrichment_version,
            schema_version=self.config.schema_version,
            prompt_version=self.config.prompt_version,
            input_hash=enrichment_input_hash(
                base,
                title=title if isinstance(title, str) else None,
                inherited_metadata=dict(base.metadata),
                parent_primary_subject=None,
                enrichment_version=self.config.enrichment_version,
            ),
        )

    @staticmethod
    def _fallback_source(base: BaseChunk) -> SourceMetadata:
        source_id = base.metadata.get("source_id")
        return SourceMetadata(
            source_id=(
                source_id
                if isinstance(source_id, str) and len(source_id) <= 128
                else None
            )
        )

    @staticmethod
    def _attach(base: BaseChunk, enrichment: ChunkEnrichment) -> EnrichedChunk:
        payload = base.model_dump(mode="python", exclude={"enrichment"})
        payload["enrichment"] = enrichment
        if isinstance(base, ChildChunk):
            return EnrichedChildChunk.model_validate(payload)
        return EnrichedParentChunk.model_validate(payload)

    @staticmethod
    def _base_chunk(value: BaseChunk | EnrichedChunk) -> BaseChunk:
        payload = value.model_dump(mode="python", exclude={"enrichment"})
        if isinstance(value, ChildChunk):
            return ChildChunk.model_validate(payload)
        return ParentChunk.model_validate(payload)

    @staticmethod
    def _is_enriched(value: object) -> bool:
        return isinstance(value, (EnrichedParentChunk, EnrichedChildChunk))

    @staticmethod
    def _is_parent(value: BaseChunk | EnrichedChunk) -> bool:
        return isinstance(value, ParentChunk)

    def _batches(
        self, values: list[tuple[int, BaseChunk | EnrichedChunk]]
    ) -> list[list[tuple[int, BaseChunk | EnrichedChunk]]]:
        size = self.config.batch_size
        return [values[index : index + size] for index in range(0, len(values), size)]

    @staticmethod
    def _safe_error(error: Exception) -> str:
        message = str(error).splitlines()[0].strip()
        return (message or type(error).__name__)[:500]
