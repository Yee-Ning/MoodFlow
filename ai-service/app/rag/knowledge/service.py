"""Corpus-level knowledge structuring service."""

from __future__ import annotations

import random
from dataclasses import dataclass
from pathlib import Path

from app.rag.enrichment.models import EnrichedChunk

from .config import KnowledgeConfig
from .corpus import KnowledgeCorpusReader
from .models import KnowledgeEnrichedChunk, KnowledgeStatus
from .ownership import deduplicate_claim_ownership
from .child_grounding import ground_parent_claims
from .pipeline import KnowledgePipeline
from .report import KnowledgeQualityReporter
from .validator import KnowledgeValidator
from .writer import KnowledgeDatasetWriter


@dataclass(frozen=True, slots=True)
class KnowledgeRunOutcome:
    chunks: list[KnowledgeEnrichedChunk]
    summary: dict[str, object]
    chunks_path: Path | None
    manifest_path: Path | None
    report_path: Path | None
    candidates_path: Path | None


class KnowledgePipelineService:
    def __init__(
        self,
        project_root: Path,
        config: KnowledgeConfig,
        *,
        enriched_path: Path,
        output_root: Path | None = None,
        pipeline: KnowledgePipeline | None = None,
    ) -> None:
        self.project_root = project_root.resolve()
        self.config = config
        self.reader = KnowledgeCorpusReader(
            self.project_root, enriched_path=enriched_path
        )
        self.output_root = (
            output_root.resolve()
            if output_root is not None
            else self.project_root / "data" / "knowledge" / "v1"
        )
        self.pipeline = pipeline or KnowledgePipeline(config)

    async def run(
        self,
        *,
        limit: int | None = None,
        validate_only: bool = False,
        sample_size: int = 3,
        sample_seed: int = 17,
    ) -> KnowledgeRunOutcome:
        source_chunks = self.reader.load_all()
        total_available = len(source_chunks)
        if limit is not None:
            if limit < 1:
                raise ValueError("knowledge limit must be positive")
            source_chunks = source_chunks[:limit]

        writer = KnowledgeDatasetWriter(self.project_root, self.output_root)
        existing = writer.load_existing()
        chunks_path = None
        manifest_path = None
        report_path = None
        candidates_path = None
        reporter = KnowledgeQualityReporter(self.project_root, self.output_root)
        if validate_only:
            chunks = self._validate_existing(
                source_chunks, existing, full=limit is None
            )
            quality_report = reporter.build(chunks)
        else:
            chunks = await self.pipeline.process_batch(
                source_chunks, existing_by_id=existing
            )
            if self.config.prompt_version in {"v2", "v3", "v4"}:
                chunks = ground_parent_claims(chunks)
            chunks = deduplicate_claim_ownership(chunks)
            validator = KnowledgeValidator()
            for source, chunk in zip(source_chunks, chunks, strict=True):
                validator.validate(source, chunk)
            validator.validate_parent_references(chunks)
            chunks_path, manifest_path = writer.write(
                chunks,
                preserve_existing=limit is not None and limit < total_available,
            )
            report_path, candidates_path, quality_report = reporter.write(chunks)

        counts = {status.value: 0 for status in KnowledgeStatus}
        for chunk in chunks:
            counts[chunk.knowledge.quality.status.value] += 1
        rng = random.Random(sample_seed)
        sampled = rng.sample(chunks, min(sample_size, len(chunks))) if chunks else []
        entity_quality = quality_report["entity_linking"]
        claim_quality = quality_report["claims"]
        gates = quality_report["quality_gates"]
        summary: dict[str, object] = {
            "status": "validated" if validate_only else "completed",
            "entity_linking_version": self.config.entity_linking_version,
            "claim_extraction_version": self.config.claim_extraction_version,
            "claim_verification_version": self.config.claim_verification_version,
            "registry_version": self.config.registry_version,
            "llm_enabled": self.config.llm_enabled,
            "available": total_available,
            "total": len(chunks),
            "linked_entities": entity_quality["linked_occurrences"],
            "claims": claim_quality["claim_count"],
            "unresolved_mentions": entity_quality["unresolved_occurrences"],
            "unique_unresolved_mentions": entity_quality["unique_unresolved_mentions"],
            "resolved_occurrence_ratio": entity_quality["resolved_occurrence_ratio"],
            "registry_candidate_resolution_ratio": entity_quality[
                "registry_candidate_resolution_ratio"
            ],
            "verification_status": claim_quality["verification_status"],
            "pre_embedding_ready": gates["pre_embedding_ready"],
            **counts,
            "samples": [item.model_dump(mode="json") for item in sampled],
        }
        return KnowledgeRunOutcome(
            chunks=chunks,
            summary=summary,
            chunks_path=chunks_path,
            manifest_path=manifest_path,
            report_path=report_path,
            candidates_path=candidates_path,
        )

    @staticmethod
    def _validate_existing(
        source_chunks: list[EnrichedChunk],
        existing: dict[str, KnowledgeEnrichedChunk],
        *,
        full: bool,
    ) -> list[KnowledgeEnrichedChunk]:
        source_ids = {chunk.chunk_id for chunk in source_chunks}
        missing = source_ids.difference(existing)
        if missing:
            raise ValueError(f"knowledge_validation_missing_chunks:{len(missing)}")
        if full:
            extra = set(existing).difference(source_ids)
            if extra:
                raise ValueError(f"knowledge_validation_extra_chunks:{len(extra)}")
        validator = KnowledgeValidator()
        chunks: list[KnowledgeEnrichedChunk] = []
        for source in source_chunks:
            knowledge_chunk = existing[source.chunk_id]
            validator.validate(source, knowledge_chunk)
            chunks.append(knowledge_chunk)
        validator.validate_parent_references(chunks)
        return chunks
