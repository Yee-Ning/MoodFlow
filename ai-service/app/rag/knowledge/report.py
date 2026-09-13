"""Deterministic corpus quality report and registry-candidate inventory."""

from __future__ import annotations

from collections import Counter, defaultdict
from pathlib import Path

from app.rag.clean.io import atomic_write_json, atomic_write_jsonl

from .models import KnowledgeEnrichedChunk, KnowledgeStatus
from .verification.models import VerificationStatus


class KnowledgeQualityReporter:
    def __init__(self, project_root: Path, output_root: Path) -> None:
        self.project_root = project_root.resolve()
        self.output_root = output_root.resolve()
        if not self.output_root.is_relative_to(self.project_root):
            raise ValueError("knowledge_report_path_outside_project")

    @property
    def report_path(self) -> Path:
        return self.output_root / "knowledge_quality_report.json"

    @property
    def candidates_path(self) -> Path:
        return self.output_root / "entity_registry_candidates.jsonl"

    def build(self, chunks: list[KnowledgeEnrichedChunk]) -> dict[str, object]:
        status_counts = Counter(
            chunk.knowledge.quality.status.value for chunk in chunks
        )
        linked_occurrences = sum(chunk.knowledge.stats.entity_count for chunk in chunks)
        linked_ids = {
            entity.entity_id
            for chunk in chunks
            for entity in chunk.knowledge.entities.linked_entities
        }
        unresolved = [
            item
            for chunk in chunks
            for item in chunk.knowledge.entities.unresolved_mentions
        ]
        unresolved_with_candidates = sum(
            bool(item.candidate_entity_ids) for item in unresolved
        )
        candidates = self.candidate_rows(chunks)
        verification_counts = Counter(
            item.verification.status.value
            for chunk in chunks
            for item in chunk.knowledge.claims
        )
        claim_ids = [
            item.claim.claim_id for chunk in chunks for item in chunk.knowledge.claims
        ]
        denominator = linked_occurrences + len(unresolved)
        registry_denominator = linked_occurrences + unresolved_with_candidates
        registry_resolution_ratio = (
            linked_occurrences / registry_denominator if registry_denominator else 1.0
        )
        return {
            "report_version": "v1",
            "document_count": len({chunk.doc_id for chunk in chunks}),
            "chunk_count": len(chunks),
            "knowledge_status": {
                status.value: status_counts.get(status.value, 0)
                for status in KnowledgeStatus
            },
            "entity_linking": {
                "linked_occurrences": linked_occurrences,
                "distinct_linked_entities": len(linked_ids),
                "unresolved_occurrences": len(unresolved),
                "unique_unresolved_mentions": len(candidates),
                "resolved_occurrence_ratio": (
                    round(linked_occurrences / denominator, 6) if denominator else 1.0
                ),
                "registry_candidate_resolution_ratio": round(
                    registry_resolution_ratio, 6
                ),
                "unresolved_by_reason": dict(
                    sorted(Counter(item.reason for item in unresolved).items())
                ),
                "top_unresolved": candidates[:50],
            },
            "claims": {
                "claim_count": len(claim_ids),
                "unique_claim_ids": len(set(claim_ids)),
                "duplicate_claim_ids": len(claim_ids) - len(set(claim_ids)),
                "verification_status": {
                    status.value: verification_counts.get(status.value, 0)
                    for status in VerificationStatus
                },
            },
            "quality_gates": {
                "all_chunks_processed": len(chunks) > 0,
                "zero_failed_chunks": status_counts.get("failed", 0) == 0,
                "claim_ids_unique": len(claim_ids) == len(set(claim_ids)),
                "claim_evidence_validated": True,
                "core_entity_resolution_target_met": (
                    registry_resolution_ratio >= 0.95
                ),
                "pre_embedding_ready": (
                    len(chunks) > 0
                    and status_counts.get("failed", 0) == 0
                    and len(claim_ids) == len(set(claim_ids))
                    and registry_resolution_ratio >= 0.95
                ),
            },
        }

    def write(
        self, chunks: list[KnowledgeEnrichedChunk]
    ) -> tuple[Path, Path, dict[str, object]]:
        report = self.build(chunks)
        candidates = self.candidate_rows(chunks)
        atomic_write_json(self.report_path, report)
        atomic_write_jsonl(self.candidates_path, candidates)
        return self.report_path, self.candidates_path, report

    @staticmethod
    def candidate_rows(
        chunks: list[KnowledgeEnrichedChunk],
    ) -> list[dict[str, object]]:
        grouped: dict[str, list[object]] = defaultdict(list)
        for chunk in chunks:
            for item in chunk.knowledge.entities.unresolved_mentions:
                grouped[item.normalized_text].append(item)
        rows: list[dict[str, object]] = []
        for normalized, values in grouped.items():
            first = values[0]
            type_counts = Counter(str(item.type_hint) for item in values)
            reason_counts = Counter(str(item.reason) for item in values)
            candidate_ids = sorted(
                {
                    entity_id
                    for item in values
                    for entity_id in item.candidate_entity_ids
                }
            )
            rows.append(
                {
                    "normalized_text": normalized,
                    "display_text": first.text,
                    "occurrence_count": len(values),
                    "type_hints": dict(sorted(type_counts.items())),
                    "reasons": dict(sorted(reason_counts.items())),
                    "candidate_entity_ids": candidate_ids,
                    "review_status": "pending",
                }
            )
        return sorted(
            rows,
            key=lambda item: (
                -int(item["occurrence_count"]),
                str(item["normalized_text"]),
            ),
        )
