"""Deterministic review packets for the pre-embedding knowledge dataset."""

from __future__ import annotations

import hashlib
import json
from collections import defaultdict
from pathlib import Path

from app.rag.clean.io import atomic_write_json, atomic_write_jsonl

from .entity_linking.registry import normalize_alias
from .models import KnowledgeEnrichedChunk


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return "sha256:" + digest.hexdigest()


class KnowledgeQASampler:
    """Build stable, stratified claim and linked-entity review samples."""

    def __init__(self, *, claim_sample_size: int = 100, entity_sample_size: int = 100):
        if claim_sample_size < 1 or entity_sample_size < 1:
            raise ValueError("knowledge_qa_sample_size_must_be_positive")
        self.claim_sample_size = claim_sample_size
        self.entity_sample_size = entity_sample_size

    def claim_rows(self, chunks: list[KnowledgeEnrichedChunk]) -> list[dict[str, object]]:
        rows: list[dict[str, object]] = []
        for chunk in chunks:
            for verified in chunk.knowledge.claims:
                claim = verified.claim
                rows.append(
                    {
                        "sample_id": f"claim-review:{claim.claim_id.removeprefix('claim:')}",
                        "sample_kind": "claim",
                        "document_id": chunk.doc_id,
                        "chunk_id": chunk.chunk_id,
                        "chunk_type": chunk.chunk_type.value,
                        "section_path": chunk.section_path,
                        "statement": claim.statement,
                        "subject_entity_id": claim.subject_entity_id,
                        "predicate": claim.predicate.value,
                        "object_entity_id": claim.object_entity_id,
                        "object_value": claim.object_value,
                        "time": claim.time.model_dump(mode="json") if claim.time else None,
                        "evidence_span": claim.evidence_span,
                        "claim_confidence": claim.confidence,
                        "extraction_source": claim.extraction_source.value,
                        "verification": verified.verification.model_dump(mode="json"),
                        "review_status": "pending",
                        "review_notes": None,
                    }
                )
        return self._stratified(rows, "predicate", self.claim_sample_size)

    def entity_rows(self, chunks: list[KnowledgeEnrichedChunk]) -> list[dict[str, object]]:
        rows: list[dict[str, object]] = []
        for chunk in chunks:
            context = "\n".join(
                value
                for value in [
                    str(chunk.metadata.get("title") or ""),
                    " > ".join(chunk.section_path),
                    chunk.content,
                ]
                if value
            )
            mention_surfaces = self._mention_surfaces(chunk)
            for entity in chunk.knowledge.entities.linked_entities:
                aliases = {normalize_alias(value) for value in entity.aliases}
                aliases.add(normalize_alias(entity.canonical_name))
                matched = sorted(
                    {
                        surface
                        for surface in mention_surfaces
                        if normalize_alias(surface) in aliases
                    }
                    | {
                        surface
                        for surface in [entity.canonical_name, *entity.aliases]
                        if surface and surface.casefold() in context.casefold()
                    },
                    key=lambda value: (value.casefold(), value),
                )
                rows.append(
                    {
                        "sample_id": self._sample_id(
                            "entity-review", chunk.chunk_id, entity.entity_id
                        ),
                        "sample_kind": "entity_link",
                        "document_id": chunk.doc_id,
                        "chunk_id": chunk.chunk_id,
                        "chunk_type": chunk.chunk_type.value,
                        "section_path": chunk.section_path,
                        "entity_id": entity.entity_id,
                        "entity_type": entity.entity_type.value,
                        "canonical_name": entity.canonical_name,
                        "matched_surfaces": matched,
                        "confidence": entity.confidence,
                        "context_excerpt": self._excerpt(context, matched),
                        "review_status": "pending",
                        "review_notes": None,
                    }
                )
        return self._stratified(rows, "entity_type", self.entity_sample_size)

    def write(
        self, chunks: list[KnowledgeEnrichedChunk], output_root: Path
    ) -> tuple[Path, Path, Path, dict[str, object]]:
        output_root.mkdir(parents=True, exist_ok=True)
        claim_path = output_root / "claim_review_sample.jsonl"
        entity_path = output_root / "entity_link_review_sample.jsonl"
        manifest_path = output_root / "qa_sampling_manifest.json"
        claim_rows = self.claim_rows(chunks)
        entity_rows = self.entity_rows(chunks)
        atomic_write_jsonl(claim_path, claim_rows)
        atomic_write_jsonl(entity_path, entity_rows)
        manifest: dict[str, object] = {
            "qa_sampling_version": "v1",
            "selection": "deterministic_stratified_then_content_hash",
            "claim_population": sum(
                len(chunk.knowledge.claims) for chunk in chunks
            ),
            "claim_sample_count": len(claim_rows),
            "claim_sample_sha256": sha256_file(claim_path),
            "entity_link_population": sum(
                len(chunk.knowledge.entities.linked_entities) for chunk in chunks
            ),
            "entity_link_sample_count": len(entity_rows),
            "entity_link_sample_sha256": sha256_file(entity_path),
            "review_status": "pending",
        }
        atomic_write_json(manifest_path, manifest)
        return claim_path, entity_path, manifest_path, manifest

    @staticmethod
    def _mention_surfaces(chunk: KnowledgeEnrichedChunk) -> set[str]:
        surfaces = {item.text for item in chunk.enrichment.entities.mentions}
        primary = chunk.enrichment.entities.primary_subject
        if primary is not None:
            surfaces.add(primary.text)
        return surfaces

    @staticmethod
    def _excerpt(context: str, surfaces: list[str], *, radius: int = 120) -> str:
        positions = [
            context.casefold().find(surface.casefold())
            for surface in surfaces
            if surface and context.casefold().find(surface.casefold()) >= 0
        ]
        position = min(positions) if positions else 0
        start = max(0, position - radius)
        end = min(len(context), position + radius)
        return context[start:end].strip()

    @classmethod
    def _stratified(
        cls,
        rows: list[dict[str, object]],
        group_key: str,
        limit: int,
    ) -> list[dict[str, object]]:
        if len(rows) <= limit:
            return sorted(rows, key=lambda row: str(row["sample_id"]))
        grouped: dict[str, list[dict[str, object]]] = defaultdict(list)
        for row in rows:
            grouped[str(row[group_key])].append(row)
        selected: list[dict[str, object]] = []
        selected_ids: set[str] = set()
        for group in sorted(grouped):
            candidate = min(grouped[group], key=cls._stable_row_key)
            if len(selected) == limit:
                break
            selected.append(candidate)
            selected_ids.add(str(candidate["sample_id"]))
        remaining = [
            row for row in rows if str(row["sample_id"]) not in selected_ids
        ]
        selected.extend(
            sorted(remaining, key=cls._stable_row_key)[: limit - len(selected)]
        )
        return sorted(selected, key=lambda row: str(row["sample_id"]))

    @staticmethod
    def _stable_row_key(row: dict[str, object]) -> tuple[str, str]:
        serialized = json.dumps(
            row, ensure_ascii=False, sort_keys=True, separators=(",", ":")
        )
        digest = hashlib.sha256(serialized.encode("utf-8")).hexdigest()
        return digest, str(row["sample_id"])

    @staticmethod
    def _sample_id(prefix: str, *parts: str) -> str:
        material = "\x1f".join(parts).encode("utf-8")
        return f"{prefix}:{hashlib.sha256(material).hexdigest()[:24]}"
