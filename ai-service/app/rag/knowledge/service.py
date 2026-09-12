"""Corpus-level knowledge structuring service."""

from __future__ import annotations

import random
from dataclasses import dataclass
from pathlib import Path

from .config import KnowledgeConfig
from .corpus import KnowledgeCorpusReader
from .models import KnowledgeEnrichedChunk, KnowledgeStatus
from .pipeline import KnowledgePipeline
from .writer import KnowledgeDatasetWriter


@dataclass(frozen=True, slots=True)
class KnowledgeRunOutcome:
    chunks: list[KnowledgeEnrichedChunk]
    summary: dict[str, object]
    chunks_path: Path | None
    manifest_path: Path | None


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
        chunks = await self.pipeline.process_batch(
            source_chunks, existing_by_id=existing
        )
        chunks_path = None
        manifest_path = None
        if not validate_only:
            chunks_path, manifest_path = writer.write(
                chunks,
                preserve_existing=limit is not None and limit < total_available,
            )

        counts = {status.value: 0 for status in KnowledgeStatus}
        for chunk in chunks:
            counts[chunk.knowledge.quality.status.value] += 1
        rng = random.Random(sample_seed)
        sampled = rng.sample(chunks, min(sample_size, len(chunks))) if chunks else []
        summary: dict[str, object] = {
            "status": "validated" if validate_only else "completed",
            "entity_linking_version": self.config.entity_linking_version,
            "claim_extraction_version": self.config.claim_extraction_version,
            "claim_verification_version": self.config.claim_verification_version,
            "registry_version": self.config.registry_version,
            "llm_enabled": self.config.llm_enabled,
            "available": total_available,
            "total": len(chunks),
            "linked_entities": sum(
                chunk.knowledge.stats.entity_count for chunk in chunks
            ),
            "claims": sum(chunk.knowledge.stats.claim_count for chunk in chunks),
            **counts,
            "samples": [item.model_dump(mode="json") for item in sampled],
        }
        return KnowledgeRunOutcome(
            chunks=chunks,
            summary=summary,
            chunks_path=chunks_path,
            manifest_path=manifest_path,
        )
