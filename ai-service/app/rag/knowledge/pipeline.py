"""Failure-isolated EnrichedChunk -> KnowledgeEnrichedChunk orchestration."""

from __future__ import annotations

import asyncio
from collections.abc import Mapping, Sequence

from app.rag.chunk.models import ChildChunk
from app.rag.enrichment.models import (
    EnrichedChildChunk,
    EnrichedChunk,
    EnrichedParentChunk,
)

from .claims import ClaimExtractor
from .claims.models import ClaimExtractionStatus
from .config import KnowledgeConfig
from .entity_linking import EntityLinker
from .entity_linking.models import EntityLinkingStatus
from .hashing import knowledge_input_hash
from .models import (
    KnowledgeEnrichedChildChunk,
    KnowledgeEnrichedChunk,
    KnowledgeEnrichedParentChunk,
    KnowledgeEnrichment,
    KnowledgeEntities,
    KnowledgeProvenance,
    KnowledgeQuality,
    KnowledgeStats,
    KnowledgeStatus,
)
from .verification import ClaimVerifier, VerifiedClaim


class KnowledgePipeline:
    def __init__(
        self,
        config: KnowledgeConfig | None = None,
        *,
        entity_linker: EntityLinker | None = None,
        claim_extractor: ClaimExtractor | None = None,
        claim_verifier: ClaimVerifier | None = None,
    ) -> None:
        self.config = config or KnowledgeConfig()
        self.entity_linker = entity_linker or EntityLinker(self.config)
        self.claim_extractor = claim_extractor or ClaimExtractor(self.config)
        self.claim_verifier = claim_verifier or ClaimVerifier(self.config)

    async def process(
        self,
        chunk: EnrichedChunk | KnowledgeEnrichedChunk,
        *,
        existing: KnowledgeEnrichedChunk | None = None,
    ) -> KnowledgeEnrichedChunk:
        base = self._base_chunk(chunk)
        prior = existing or (chunk if self._is_knowledge_enriched(chunk) else None)
        provenance = self._provenance(base)
        if prior is not None and self._can_skip(prior, provenance):
            knowledge = prior.knowledge.model_copy(
                update={
                    "quality": prior.knowledge.quality.model_copy(
                        update={
                            "status": KnowledgeStatus.SKIPPED,
                            "warnings": self._dedupe(
                                [
                                    *prior.knowledge.quality.warnings,
                                    "unchanged_input_and_versions",
                                ]
                            ),
                        }
                    )
                }
            )
            result = self._attach(base, knowledge)
            self._validate_preservation(base, result)
            return result

        try:
            entity_result = await self.entity_linker.link(base)
            claim_result = await self.claim_extractor.extract(base, entity_result)
            verified: list[VerifiedClaim] = []
            verification_warnings: list[str] = []
            for claim in claim_result.claims:
                verification = await self.claim_verifier.verify(claim, base)
                if verification.reason.startswith("verification_unavailable:"):
                    verification_warnings.append(verification.reason)
                verified.append(VerifiedClaim(claim=claim, verification=verification))

            entities = KnowledgeEntities(
                linked_entities=entity_result.linked_entities,
                unresolved_mentions=entity_result.unresolved_mentions,
            )
            warnings = self._dedupe(
                [
                    *entity_result.warnings,
                    *claim_result.warnings,
                    *verification_warnings,
                ]
            )
            errors = self._dedupe([*entity_result.errors, *claim_result.errors])
            is_partial = (
                entity_result.status != EntityLinkingStatus.SUCCESS
                or claim_result.status != ClaimExtractionStatus.SUCCESS
                or bool(verification_warnings)
            )
            knowledge = KnowledgeEnrichment(
                entities=entities,
                claims=verified,
                stats=KnowledgeStats.build(entities, verified),
                quality=KnowledgeQuality(
                    status=(
                        KnowledgeStatus.PARTIAL
                        if is_partial
                        else KnowledgeStatus.SUCCESS
                    ),
                    warnings=warnings,
                    errors=errors,
                ),
                provenance=provenance,
            )
            result = self._attach(base, knowledge)
            self._validate_preservation(base, result)
            return result
        except Exception as exc:
            result = self._failed(base, provenance, exc)
            self._validate_preservation(base, result)
            return result

    async def process_batch(
        self,
        chunks: Sequence[EnrichedChunk | KnowledgeEnrichedChunk],
        *,
        existing_by_id: Mapping[str, KnowledgeEnrichedChunk] | None = None,
    ) -> list[KnowledgeEnrichedChunk]:
        existing_by_id = existing_by_id or {}
        semaphore = asyncio.Semaphore(self.config.max_concurrency)

        async def run_one(
            value: EnrichedChunk | KnowledgeEnrichedChunk,
        ) -> KnowledgeEnrichedChunk:
            async with semaphore:
                return await self.process(
                    value, existing=existing_by_id.get(value.chunk_id)
                )

        results: list[KnowledgeEnrichedChunk] = []
        for start in range(0, len(chunks), self.config.batch_size):
            batch = chunks[start : start + self.config.batch_size]
            results.extend(await asyncio.gather(*(run_one(item) for item in batch)))
        return results

    def _provenance(self, chunk: EnrichedChunk) -> KnowledgeProvenance:
        return KnowledgeProvenance(
            entity_linking_version=self.config.entity_linking_version,
            claim_extraction_version=self.config.claim_extraction_version,
            claim_verification_version=self.config.claim_verification_version,
            registry_version=self.config.registry_version,
            prompt_version=self.config.prompt_version,
            knowledge_input_hash=knowledge_input_hash(chunk, self.config),
        )

    @staticmethod
    def _can_skip(
        prior: KnowledgeEnrichedChunk, provenance: KnowledgeProvenance
    ) -> bool:
        old = prior.knowledge.provenance
        return (
            prior.knowledge.quality.status
            in {KnowledgeStatus.SUCCESS, KnowledgeStatus.SKIPPED}
            and old == provenance
        )

    @staticmethod
    def _attach(
        base: EnrichedChunk, knowledge: KnowledgeEnrichment
    ) -> KnowledgeEnrichedChunk:
        payload = base.model_dump(mode="python", exclude={"knowledge"})
        payload["knowledge"] = knowledge
        if isinstance(base, ChildChunk):
            return KnowledgeEnrichedChildChunk.model_validate(payload)
        return KnowledgeEnrichedParentChunk.model_validate(payload)

    @staticmethod
    def _base_chunk(
        value: EnrichedChunk | KnowledgeEnrichedChunk,
    ) -> EnrichedChunk:
        payload = value.model_dump(mode="python", exclude={"knowledge"})
        if isinstance(value, ChildChunk):
            return EnrichedChildChunk.model_validate(payload)
        return EnrichedParentChunk.model_validate(payload)

    @staticmethod
    def _is_knowledge_enriched(value: object) -> bool:
        return isinstance(
            value, (KnowledgeEnrichedParentChunk, KnowledgeEnrichedChildChunk)
        )

    def _failed(
        self,
        base: EnrichedChunk,
        provenance: KnowledgeProvenance,
        error: Exception,
    ) -> KnowledgeEnrichedChunk:
        entities = KnowledgeEntities()
        knowledge = KnowledgeEnrichment(
            entities=entities,
            claims=[],
            stats=KnowledgeStats.build(entities, []),
            quality=KnowledgeQuality(
                status=KnowledgeStatus.FAILED,
                warnings=["knowledge_pipeline_failed"],
                errors=[self._safe_error(error)],
            ),
            provenance=provenance,
        )
        return self._attach(base, knowledge)

    @staticmethod
    def _validate_preservation(
        source: EnrichedChunk, result: KnowledgeEnrichedChunk
    ) -> None:
        source_payload = source.model_dump(mode="json", exclude={"knowledge"})
        result_payload = result.model_dump(mode="json", exclude={"knowledge"})
        if source_payload != result_payload:
            raise ValueError("enriched_chunk_mutated")

    @staticmethod
    def _dedupe(values: list[str]) -> list[str]:
        return list(dict.fromkeys(item[:500] for item in values if item))[:200]

    @staticmethod
    def _safe_error(error: Exception) -> str:
        message = str(error).splitlines()[0].strip()
        return (message or type(error).__name__)[:500]
