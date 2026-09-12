"""Atomic claim extraction with local grounding validation and stable IDs."""

from __future__ import annotations

import asyncio
from collections.abc import Sequence

from pydantic import ValidationError

from app.rag.enrichment.models import EnrichedChunk

from ..config import KnowledgeConfig
from ..entity_linking.models import CanonicalEntity, EntityLinkingResult
from ..structured import strict_model_schema
from .ids import build_claim_id
from .models import (
    Claim,
    ClaimCandidate,
    ClaimExtractionOutput,
    ClaimExtractionResult,
    ClaimExtractionStatus,
    ClaimSource,
)
from .prompts import CLAIM_EXTRACTION_PROMPT_V1
from .rules import RuleClaimExtractor, is_subjective
from .runtime import ClaimExtractionRuntime, ExistingLLMClaimExtractionRuntime


class ClaimExtractor:
    def __init__(
        self,
        config: KnowledgeConfig,
        *,
        runtime: ClaimExtractionRuntime | None = None,
        rule_extractor: RuleClaimExtractor | None = None,
    ) -> None:
        self.config = config
        self.runtime = runtime or ExistingLLMClaimExtractionRuntime()
        self.rule_extractor = rule_extractor or RuleClaimExtractor()

    async def extract(
        self,
        chunk: EnrichedChunk,
        entities: EntityLinkingResult | Sequence[CanonicalEntity],
    ) -> ClaimExtractionResult:
        linked = (
            entities.linked_entities
            if isinstance(entities, EntityLinkingResult)
            else list(entities)
        )
        rule_candidates = self.rule_extractor.extract(chunk.content, linked)
        claims = [self._claim(item, chunk.chunk_id, ClaimSource.RULE) for item in rule_candidates]
        warnings: list[str] = []
        status = ClaimExtractionStatus.SUCCESS

        if self.config.llm_enabled and linked:
            try:
                output = await self._generate(chunk, linked)
                for candidate in output.claims:
                    self._validate_candidate(candidate, chunk, linked)
                    claims.append(self._claim(candidate, chunk.chunk_id, ClaimSource.LLM))
            except Exception as exc:
                warnings.append(self._error_code(exc))
                status = ClaimExtractionStatus.PARTIAL

        by_id: dict[str, Claim] = {}
        for claim in claims:
            by_id.setdefault(claim.claim_id, claim)
        return ClaimExtractionResult(
            claims=sorted(by_id.values(), key=lambda item: item.claim_id),
            status=status,
            warnings=warnings,
        )

    async def _generate(
        self, chunk: EnrichedChunk, entities: list[CanonicalEntity]
    ) -> ClaimExtractionOutput:
        payload: dict[str, object] = {
            "chunk_content": chunk.content,
            "title": chunk.metadata.get("title"),
            "section_path": chunk.section_path,
            "linked_entities": [item.model_dump(mode="json") for item in entities],
        }
        last_error: Exception | None = None
        for attempt in range(self.config.retry_count + 1):
            instructions = CLAIM_EXTRACTION_PROMPT_V1
            if attempt:
                instructions += "\nCorrect the previous invalid or ungrounded response."
            try:
                raw = await asyncio.wait_for(
                    self.runtime.generate(
                        payload,
                        instructions=instructions,
                        schema=strict_model_schema(ClaimExtractionOutput),
                        schema_name=f"music_claim_extraction_{self.config.prompt_version}",
                    ),
                    timeout=self.config.timeout_seconds,
                )
                output = ClaimExtractionOutput.model_validate_json(raw)
                for candidate in output.claims:
                    self._validate_candidate(candidate, chunk, entities)
                return output
            except Exception as exc:
                last_error = exc
        raise RuntimeError(self._error_code(last_error)) from last_error

    @staticmethod
    def _validate_candidate(
        candidate: ClaimCandidate,
        chunk: EnrichedChunk,
        entities: list[CanonicalEntity],
    ) -> None:
        entity_ids = {item.entity_id for item in entities}
        if candidate.subject_entity_id not in entity_ids:
            raise ValueError("claim_unknown_subject_entity")
        if (
            candidate.object_entity_id is not None
            and candidate.object_entity_id not in entity_ids
        ):
            raise ValueError("claim_unknown_object_entity")
        if candidate.evidence_span not in chunk.content:
            raise ValueError("claim_evidence_not_in_chunk")
        if is_subjective(candidate.evidence_span):
            raise ValueError("claim_subjective_evidence")

    @staticmethod
    def _claim(
        candidate: ClaimCandidate, source_chunk_id: str, source: ClaimSource
    ) -> Claim:
        return Claim(
            claim_id=build_claim_id(candidate, source_chunk_id),
            statement=candidate.statement,
            subject_entity_id=candidate.subject_entity_id,
            predicate=candidate.predicate,
            object_entity_id=candidate.object_entity_id,
            object_value=candidate.object_value,
            time=candidate.time,
            source_chunk_id=source_chunk_id,
            evidence_span=candidate.evidence_span,
            confidence=candidate.confidence,
            extraction_source=source,
        )

    @staticmethod
    def _error_code(error: Exception | None) -> str:
        if isinstance(error, asyncio.TimeoutError):
            return "claim_extraction_timeout"
        if isinstance(error, ValidationError):
            return "claim_extraction_invalid_output"
        if isinstance(error, (ValueError, RuntimeError)):
            return str(error)[:500]
        return "claim_extraction_request_failed"
