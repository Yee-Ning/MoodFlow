"""Corpus-level Chunk V1 -> Metadata Enrichment orchestration."""

from __future__ import annotations

import random
from dataclasses import dataclass
from pathlib import Path

from app.rag.chunk.models import ChunkedDocument

from .config import EnrichmentConfig
from .corpus import EnrichmentCorpusReader
from .models import BaseChunk, EnrichedChunk, EnrichmentStatus
from .pipeline import MetadataEnrichmentPipeline
from .validator import EnrichmentValidator
from .writer import EnrichmentDatasetWriter


@dataclass(frozen=True, slots=True)
class EnrichmentRunOutcome:
    chunks: list[EnrichedChunk]
    summary: dict[str, object]
    chunks_path: Path | None
    manifest_path: Path | None


class MetadataEnrichmentService:
    def __init__(
        self,
        project_root: Path,
        config: EnrichmentConfig,
        *,
        chunked_root: Path,
        chunked_manifest_path: Path | None = None,
        output_root: Path | None = None,
        pipeline: MetadataEnrichmentPipeline | None = None,
    ) -> None:
        self.project_root = project_root.resolve()
        self.config = config
        self.reader = EnrichmentCorpusReader(
            self.project_root,
            chunked_root=chunked_root,
            manifest_path=chunked_manifest_path,
        )
        self.output_root = (
            output_root.resolve()
            if output_root is not None
            else self.project_root / "data" / "enriched" / config.enrichment_version
        )
        self.pipeline = pipeline or MetadataEnrichmentPipeline(config)

    async def run(
        self,
        *,
        limit: int | None = None,
        validate_only: bool = False,
        retry_failed_only: bool = False,
        sample_size: int = 3,
        sample_seed: int = 17,
    ) -> EnrichmentRunOutcome:
        documents = self.reader.load_all()
        source_chunks = self._flatten(documents)
        total_available = len(source_chunks)
        if limit is not None:
            if limit < 1:
                raise ValueError("enrichment limit must be positive")
            source_chunks = source_chunks[:limit]

        writer = EnrichmentDatasetWriter(self.project_root, self.output_root)
        existing = writer.load_existing()
        chunks_path = None
        manifest_path = None
        if validate_only:
            chunks = self._validate_existing(
                source_chunks, existing, full=limit is None
            )
        else:
            if retry_failed_only:
                source_chunks = [
                    chunk
                    for chunk in source_chunks
                    if (
                        chunk.chunk_id in existing
                        and existing[
                            chunk.chunk_id
                        ].enrichment.quality.enrichment_status
                        == EnrichmentStatus.FAILED
                    )
                ]
            chunks = await self.pipeline.enrich_batch(
                source_chunks,
                existing_by_id=existing,
            )
            chunks_path, manifest_path = writer.write(
                chunks,
                preserve_existing=(
                    retry_failed_only or (limit is not None and limit < total_available)
                ),
            )

        counts = {status.value: 0 for status in EnrichmentStatus}
        for chunk in chunks:
            counts[chunk.enrichment.quality.enrichment_status.value] += 1
        rng = random.Random(sample_seed)
        sampled = rng.sample(chunks, min(sample_size, len(chunks))) if chunks else []
        summary: dict[str, object] = {
            "status": "validated" if validate_only else "completed",
            "enrichment_version": self.config.enrichment_version,
            "schema_version": self.config.schema_version,
            "prompt_version": self.config.prompt_version,
            "llm_enabled": self.config.llm_enabled,
            "available": total_available,
            "total": len(chunks),
            **counts,
            "samples": [item.model_dump(mode="json") for item in sampled],
        }
        return EnrichmentRunOutcome(
            chunks=chunks,
            summary=summary,
            chunks_path=chunks_path,
            manifest_path=manifest_path,
        )

    @staticmethod
    def _validate_existing(
        source_chunks: list[BaseChunk],
        existing: dict[str, EnrichedChunk],
        *,
        full: bool,
    ) -> list[EnrichedChunk]:
        source_ids = {chunk.chunk_id for chunk in source_chunks}
        missing = source_ids.difference(existing)
        if missing:
            raise ValueError(f"enriched_validation_missing_chunks:{len(missing)}")
        if full:
            extra = set(existing).difference(source_ids)
            if extra:
                raise ValueError(f"enriched_validation_extra_chunks:{len(extra)}")
        validator = EnrichmentValidator()
        chunks: list[EnrichedChunk] = []
        for source in source_chunks:
            enriched = existing[source.chunk_id]
            validator.validate(source, enriched)
            chunks.append(enriched)
        return chunks

    @staticmethod
    def _flatten(documents: list[ChunkedDocument]) -> list[BaseChunk]:
        result: list[BaseChunk] = []
        for document in documents:
            children_by_parent: dict[str, list[BaseChunk]] = {}
            for child in document.children:
                children_by_parent.setdefault(child.parent_id, []).append(child)
            for parent in document.parents:
                result.append(parent)
                result.extend(
                    sorted(
                        children_by_parent.get(parent.chunk_id, []),
                        key=lambda item: item.chunk_index,
                    )
                )
        return result
