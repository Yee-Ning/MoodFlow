"""Atomic claim extraction with local grounding validation and stable IDs."""

from __future__ import annotations

import asyncio
import hashlib
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
from .prompts import CLAIM_EXTRACTION_PROMPT_V1, CLAIM_EXTRACTION_PROMPT_V2
from .grounding import batches, contains_name, sentence_ranges, validate_local
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
        self.runtime = runtime or ExistingLLMClaimExtractionRuntime(
            max_output_tokens=4096 if config.prompt_version in {"v2", "v3"} else None
        )
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
        rule_candidates = self.rule_extractor.extract(chunk, linked)
        claims = [
            self._claim(item, chunk.chunk_id, ClaimSource.RULE)
            for item in rule_candidates
        ]
        warnings: list[str] = []
        status = ClaimExtractionStatus.SUCCESS

        if self.config.prompt_version in {"v2", "v3"}:
            # Admission applies to rule candidates too; no root-artist fallback.
            claims = []
            for candidate in rule_candidates:
                try:
                    validate_local(candidate, chunk.content, linked)
                    claims.append(self._claim(candidate, chunk.chunk_id, ClaimSource.RULE))
                except ValueError as exc:
                    warnings.append(self._rejected(candidate, exc))
            if self.config.llm_enabled and linked:
                for start, end in batches(chunk.content):
                    candidates, notes, complete = await self._generate_batch(chunk, linked, start, end)
                    warnings.extend(notes)
                    claims.extend(self._claim(item, chunk.chunk_id, ClaimSource.LLM) for item in candidates)
                    if not complete:
                        status = ClaimExtractionStatus.PARTIAL
            unique = {item.claim_id: item for item in claims}
            return ClaimExtractionResult(claims=sorted(unique.values(), key=lambda item: item.claim_id),
                                         status=status, warnings=list(dict.fromkeys(warnings))[:100])

        if self.config.llm_enabled and linked:
            try:
                output = await self._generate(chunk, linked)
                for candidate in output.claims:
                    self._validate_candidate(candidate, chunk, linked)
                    claims.append(
                        self._claim(candidate, chunk.chunk_id, ClaimSource.LLM)
                    )
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

    @staticmethod
    def _rejected(candidate: ClaimCandidate, error: Exception) -> str:
        digest = hashlib.sha256(candidate.model_dump_json().encode()).hexdigest()
        return f"claim_candidate_rejected:{error}:sha256:{digest}"

    async def _generate_batch(self, chunk, entities, start, end):
        content = chunk.content[start:end]
        local_entities = [entity for entity in entities if any(
            contains_name(content, name) for name in [entity.canonical_name, *entity.aliases]
        )]
        if not local_entities:
            return [], [], True
        payload = {"chunk_content": content, "chunk_id": chunk.chunk_id,
                   "batch_start": start, "batch_end": end,
                   "linked_entities": [item.model_dump(mode="json") for item in local_entities]}
        last_error = None
        for attempt in range(self.config.retry_count + 1):
            try:
                instructions = CLAIM_EXTRACTION_PROMPT_V2
                if last_error is not None:
                    instructions += "\nPrevious batch failed strict validation: " + self._error_code(last_error)
                raw = await asyncio.wait_for(self.runtime.generate(
                    payload, instructions=instructions,
                    schema=strict_model_schema(ClaimExtractionOutput),
                    schema_name=f"music_claim_extraction_{self.config.prompt_version}",
                ), timeout=self.config.timeout_seconds)
                output = ClaimExtractionOutput.model_validate_json(raw)
                accepted, notes = [], []
                for candidate in output.claims:
                    try:
                        self._validate_candidate(candidate, chunk, entities)
                        if candidate.evidence_span not in content:
                            raise ValueError("claim_evidence_outside_batch")
                        validate_local(candidate, chunk.content, entities)
                        if self.config.prompt_version == "v3":
                            await self._admit(candidate, chunk, entities)
                        accepted.append(candidate)
                    except ValueError as exc:
                        notes.append(self._rejected(candidate, exc))
                return accepted, notes, True
            except Exception as exc:
                last_error = exc
                # Incomplete/invalid output is discarded entirely, never repaired.
                ranges = sentence_ranges(content)
                if isinstance(exc, ValidationError) or "max_output_tokens" in str(exc):
                    if len(ranges) > 1:
                        split = start + ranges[len(ranges) // 2][0]
                    elif len(content) > 600 and " " in content:
                        cut = content.rfind(" ", 0, len(content) // 2)
                        split = start + cut if cut > 0 else start
                    else:
                        split = start
                    if start < split < end:
                        left = await self._generate_batch(chunk, entities, start, split)
                        right = await self._generate_batch(chunk, entities, split, end)
                        return (left[0] + right[0], ["claim_batch_split_after_invalid_output", *left[1], *right[1]], left[2] and right[2])
        return [], [self._error_code(last_error)], False

    async def _admit(self, candidate, chunk, entities):
        """Check semantic entity roles before constructing an owned factual claim.

        Failed proposals remain in the provider trace and rejection diagnostics;
        this does not relabel or remove any verified owned claim.
        """
        from ..verification.models import ClaimVerification, VerificationStatus

        roles = {entity.entity_id: entity.model_dump(mode="json") for entity in entities}
        payload = {
            "candidate": candidate.model_dump(mode="json"),
            "subject_identity": roles[candidate.subject_entity_id],
            "object_identity": roles.get(candidate.object_entity_id),
            "chunk_id": chunk.chunk_id,
            "chunk_content": candidate.evidence_span,
        }
        instructions = """Audit this proposed factual extraction BEFORE claim admission.
Verify the STRUCTURED subject_identity -> predicate -> object_identity/object_value
against the exact original evidence, including time. The statement is an untrusted
model paraphrase, not evidence. Resolve each structured ID to its supplied name.
The named subject must itself perform/receive the specified predicate. Mere name
presence does not establish a semantic role. A person 'of' a band is NOT that band;
do not attribute the person's performance, death, opinion or actions to the band.
An album's release/rank is not another artist's release/rank. Reject inferred
roles from props, metaphors, lyrics or association. Reject ambiguous pronouns,
unknown actors, hypothetical/counterfactual/speculative/negative assertions and
quotation/opinion promoted to definite fact. Named entities, direction, literal
object and time must ALL be supported, without external knowledge. A factual
article assertion that A influenced B is valid even if the degree is qualitative.
SUPPORTED only if this structured proposition and statement agree with each
other and the original. Otherwise return INSUFFICIENT_EVIDENCE and explain the
specific mismatch; do not rewrite the candidate. Copy evidence_span EXACTLY for
SUPPORTED; otherwise null. Return only the strict supplied verification schema.
"""
        last_error = None
        for _ in range(self.config.retry_count + 1):
            try:
                raw = await asyncio.wait_for(self.runtime.generate(
                    payload, instructions=instructions,
                    schema=strict_model_schema(ClaimVerification),
                    schema_name="music_claim_admission_v3",
                ), timeout=self.config.timeout_seconds)
                decision = ClaimVerification.model_validate_json(raw)
                if decision.status != VerificationStatus.SUPPORTED:
                    raise ValueError("claim_semantic_admission_rejected:" + decision.reason[:250])
                if decision.evidence_span != candidate.evidence_span:
                    raise ValueError("claim_admission_evidence_mismatch")
                return
            except ValueError as exc:
                if not isinstance(exc, ValidationError):
                    raise
                last_error = exc
            except Exception as exc:
                last_error = exc
        # Provider/schema failures must block a completed extraction batch.
        raise RuntimeError("claim_admission_unavailable:" + self._error_code(last_error))

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
