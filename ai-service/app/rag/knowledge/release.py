"""Immutable, quality-gated release of a knowledge corpus for later indexing."""

from __future__ import annotations

import json
import hashlib
import os
import shutil
import tempfile
from dataclasses import dataclass
from pathlib import Path

import yaml

from app.rag.clean.io import atomic_write_json, atomic_write_jsonl

from .config import load_knowledge_config
from .corpus import KnowledgeCorpusReader
from .entity_linking.registry import DEFAULT_ENTITY_REGISTRY_PATH
from .entity_linking.registry import EntityRegistry
from .models import KnowledgeEnrichedChunk
from .qa import KnowledgeQASampler, sha256_file
from .report import KnowledgeQualityReporter
from .release_review import select_release_documents
from .validator import KnowledgeValidator
from .writer import KnowledgeDatasetWriter


class KnowledgeReleaseError(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class KnowledgeReleaseOutcome:
    status: str
    release_id: str | None
    output_root: Path | None
    review_root: Path
    summary: dict[str, object]


class KnowledgeReleaseBuilder:
    """Prepare review samples and freeze a content-addressed Knowledge V2 release."""

    REQUIRED_SOURCE_FILES = (
        "knowledge_enriched_chunks.jsonl",
        "knowledge_corpus_manifest.jsonl",
        "knowledge_quality_report.json",
        "entity_registry_candidates.jsonl",
    )

    def __init__(
        self,
        project_root: Path,
        *,
        source_root: Path,
        output_root: Path,
        review_root: Path,
        enriched_path: Path,
        config_path: Path,
        registry_path: Path = DEFAULT_ENTITY_REGISTRY_PATH,
        claim_sample_size: int = 100,
        entity_sample_size: int = 100,
    ) -> None:
        self.project_root = project_root.resolve()
        self.source_root = self._confined(source_root, "knowledge_source")
        self.output_root = self._confined(output_root, "knowledge_release")
        self.review_root = self._confined(review_root, "knowledge_review")
        self.enriched_path = self._confined(enriched_path, "enriched_source")
        self.config_path = self._confined(config_path, "knowledge_config")
        self.registry_path = self._confined(registry_path, "entity_registry")
        if self.source_root == self.output_root:
            raise KnowledgeReleaseError("knowledge_release_must_not_replace_source")
        self.config = load_knowledge_config(self.config_path)
        self.sampler = KnowledgeQASampler(
            claim_sample_size=claim_sample_size,
            entity_sample_size=entity_sample_size,
        )

    def prepare_review(
        self, *, include_manual_review: bool = False
    ) -> KnowledgeReleaseOutcome:
        chunks, report = self._validate_source(
            include_manual_review=include_manual_review
        )
        if not chunks:
            return KnowledgeReleaseOutcome(
                status="quarantined",
                release_id=None,
                output_root=None,
                review_root=self.review_root,
                summary={**report["manual_review"], "pre_embedding_ready": False},
            )
        claim_path, entity_path, manifest_path, manifest = self.sampler.write(
            chunks, self.review_root
        )
        manifest["release_selection_sha256"] = report["release_selection_sha256"]
        atomic_write_json(manifest_path, manifest)
        summary = {
            **report["manual_review"],
            "pre_embedding_ready": (
                report["quality_gates"]["pre_embedding_ready"]
                and report["all_claims_supported"]
            ),
            "all_claims_supported": report["all_claims_supported"],
            "claim_sample_count": manifest["claim_sample_count"],
            "entity_link_sample_count": manifest["entity_link_sample_count"],
            "claim_sample_path": self._relative(claim_path),
            "entity_link_sample_path": self._relative(entity_path),
            "sampling_manifest_path": self._relative(manifest_path),
            "attestation_path": self._relative(
                self.review_root / "qa_review_attestation.json"
            ),
        }
        return KnowledgeReleaseOutcome(
            status="review_prepared",
            release_id=None,
            output_root=None,
            review_root=self.review_root,
            summary=summary,
        )

    def finalize(
        self, *, include_manual_review: bool = False
    ) -> KnowledgeReleaseOutcome:
        chunks, report = self._validate_source(
            include_manual_review=include_manual_review
        )
        if not chunks:
            raise KnowledgeReleaseError("knowledge_release_no_eligible_documents")
        sampling_manifest, attestation = self._validate_attestation(chunks, report)
        self.output_root.parent.mkdir(parents=True, exist_ok=True)
        stage: Path | None = Path(
            tempfile.mkdtemp(
                prefix=f".{self.output_root.name}.",
                suffix=".tmp",
                dir=self.output_root.parent,
            )
        )
        try:
            self._write_release_artifacts(stage, chunks, report)
            qa_root = stage / "qa"
            qa_root.mkdir(parents=True, exist_ok=True)
            for name in (
                "claim_review_sample.jsonl",
                "entity_link_review_sample.jsonl",
                "qa_sampling_manifest.json",
                "qa_review_attestation.json",
            ):
                shutil.copyfile(self.review_root / name, qa_root / name)
            artifact_hashes = self._artifact_hashes(stage)
            release_id = self._release_id(artifact_hashes)
            release_manifest = self._release_manifest(
                release_id,
                artifact_hashes,
                report,
                sampling_manifest,
                attestation,
            )
            atomic_write_json(stage / "knowledge_release_manifest.json", release_manifest)
            if self.output_root.exists():
                self._assert_identical_release(stage, self.output_root)
                status = "unchanged"
            else:
                os.replace(stage, self.output_root)
                stage = None
                status = "created"
            return KnowledgeReleaseOutcome(
                status=status,
                release_id=release_id,
                output_root=self.output_root,
                review_root=self.review_root,
                summary={
                    **report["manual_review"],
                    "all_claims_supported": report["all_claims_supported"],
                    "pre_embedding_release_ready": report["all_claims_supported"],
                    "chunk_count": report["chunk_count"],
                    "claim_count": report["claims"]["claim_count"],
                    "linked_entities": report["entity_linking"]["linked_occurrences"],
                    "claim_sample_count": sampling_manifest["claim_sample_count"],
                    "entity_link_sample_count": sampling_manifest[
                        "entity_link_sample_count"
                    ],
                    "release_manifest": self._relative(
                        self.output_root / "knowledge_release_manifest.json"
                    ),
                },
            )
        finally:
            if stage is not None and stage.exists():
                shutil.rmtree(stage)

    def _validate_source(
        self, *, include_manual_review: bool = False,
    ) -> tuple[list[KnowledgeEnrichedChunk], dict[str, object]]:
        for name in self.REQUIRED_SOURCE_FILES:
            if not (self.source_root / name).is_file():
                raise KnowledgeReleaseError(f"knowledge_release_source_missing:{name}")
        source_chunks = KnowledgeCorpusReader(
            self.project_root, enriched_path=self.enriched_path
        ).load_all()
        persisted = KnowledgeDatasetWriter(
            self.project_root, self.source_root
        ).load_existing()
        source_ids = {chunk.chunk_id for chunk in source_chunks}
        if source_ids != set(persisted):
            raise KnowledgeReleaseError("knowledge_release_source_chunk_set_mismatch")
        validator = KnowledgeValidator()
        chunks: list[KnowledgeEnrichedChunk] = []
        for source in source_chunks:
            chunk = persisted[source.chunk_id]
            validator.validate(source, chunk)
            chunks.append(chunk)
        validator.validate_parent_references(chunks)
        registry = EntityRegistry.load(self.registry_path)
        if registry.version != self.config.registry_version:
            raise KnowledgeReleaseError("knowledge_release_registry_version_mismatch")
        expected_versions = {
            "entity_linking_version": self.config.entity_linking_version,
            "claim_extraction_version": self.config.claim_extraction_version,
            "claim_verification_version": self.config.claim_verification_version,
            "registry_version": self.config.registry_version,
            "prompt_version": self.config.prompt_version,
        }
        for chunk in chunks:
            provenance = chunk.knowledge.provenance
            if any(
                getattr(provenance, field) != version
                for field, version in expected_versions.items()
            ):
                raise KnowledgeReleaseError("knowledge_release_version_mismatch")
        reporter = KnowledgeQualityReporter(self.project_root, self.source_root)
        report = reporter.build(chunks)
        writer = KnowledgeDatasetWriter(self.project_root, self.source_root)
        if self._load_jsonl(writer.manifest_path) != writer.manifest_rows(chunks):
            raise KnowledgeReleaseError("knowledge_release_corpus_manifest_stale")
        if self._load_jsonl(reporter.candidates_path) != reporter.candidate_rows(chunks):
            raise KnowledgeReleaseError("knowledge_release_registry_candidates_stale")
        persisted_report = self._load_json(
            self.source_root / "knowledge_quality_report.json"
        )
        if persisted_report != report:
            raise KnowledgeReleaseError("knowledge_release_quality_report_stale")
        chunks, review = select_release_documents(
            chunks, include_manual_review=include_manual_review
        )
        report = reporter.build(chunks)
        report["manual_review"] = review
        report["all_claims_supported"] = not any(
            count
            for status, count in report["claims"]["verification_status"].items()
            if status != "SUPPORTED"
        )
        # Bind the full source and this invocation's admission policy, even when
        # a small QA sample happens to be identical for two different selections.
        selection = {
            "source_knowledge_sha256": sha256_file(writer.chunks_path),
            "selected_chunk_ids": [chunk.chunk_id for chunk in chunks],
            "manual_review": review,
        }
        report["release_selection_sha256"] = "sha256:" + hashlib.sha256(
            json.dumps(
                selection, ensure_ascii=False, sort_keys=True, separators=(",", ":")
            ).encode("utf-8")
        ).hexdigest()
        atomic_write_jsonl(
            self.review_root / "manual_review_documents.jsonl",
            review["manual_review_documents"],
        )
        atomic_write_json(self.review_root / "release_selection_report.json", report)
        if any(not chunk.enrichment.quality.index_ready for chunk in chunks):
            raise KnowledgeReleaseError("knowledge_release_enrichment_not_ready")
        if chunks and not report["quality_gates"]["pre_embedding_ready"]:
            raise KnowledgeReleaseError("knowledge_release_quality_gate_failed")
        return chunks, report

    def _validate_attestation(
        self, chunks: list[KnowledgeEnrichedChunk], report: dict[str, object]
    ) -> tuple[dict[str, object], dict[str, object]]:
        claim_path = self.review_root / "claim_review_sample.jsonl"
        entity_path = self.review_root / "entity_link_review_sample.jsonl"
        manifest_path = self.review_root / "qa_sampling_manifest.json"
        attestation_path = self.review_root / "qa_review_attestation.json"
        for path in (claim_path, entity_path, manifest_path, attestation_path):
            if not path.is_file():
                raise KnowledgeReleaseError(
                    f"knowledge_release_review_missing:{path.name}"
                )
        expected_claims = self.sampler.claim_rows(chunks)
        expected_entities = self.sampler.entity_rows(chunks)
        if self._load_jsonl(claim_path) != expected_claims:
            raise KnowledgeReleaseError("knowledge_release_claim_sample_stale")
        if self._load_jsonl(entity_path) != expected_entities:
            raise KnowledgeReleaseError("knowledge_release_entity_sample_stale")
        manifest = self._load_json(manifest_path)
        if (
            manifest.get("release_selection_sha256")
            != report["release_selection_sha256"]
        ):
            raise KnowledgeReleaseError("knowledge_release_review_selection_stale")
        if manifest.get("claim_sample_sha256") != sha256_file(claim_path):
            raise KnowledgeReleaseError("knowledge_release_claim_sample_hash_mismatch")
        if manifest.get("entity_link_sample_sha256") != sha256_file(entity_path):
            raise KnowledgeReleaseError("knowledge_release_entity_sample_hash_mismatch")
        attestation = self._load_json(attestation_path)
        required = {
            "attestation_version": "v1",
            "review_status": "approved",
            "claim_sample_sha256": manifest["claim_sample_sha256"],
            "entity_link_sample_sha256": manifest["entity_link_sample_sha256"],
            "claim_reviewed_count": manifest["claim_sample_count"],
            "entity_link_reviewed_count": manifest["entity_link_sample_count"],
            "generation_mode": "rule_only",
            "release_selection_sha256": manifest["release_selection_sha256"],
        }
        if any(attestation.get(key) != value for key, value in required.items()):
            raise KnowledgeReleaseError("knowledge_release_attestation_invalid")
        if not str(attestation.get("reviewer") or "").strip():
            raise KnowledgeReleaseError("knowledge_release_attestation_reviewer_missing")
        rejected = attestation.get("rejected_sample_ids")
        if rejected != []:
            raise KnowledgeReleaseError("knowledge_release_review_rejections_present")
        return manifest, attestation

    def _write_release_artifacts(
        self,
        stage: Path,
        chunks: list[KnowledgeEnrichedChunk],
        report: dict[str, object],
    ) -> None:
        stage.mkdir(parents=True, exist_ok=True)
        atomic_write_jsonl(
            stage / "knowledge_enriched_chunks.jsonl",
            (chunk.model_dump(mode="json") for chunk in chunks),
        )
        source_manifest = KnowledgeDatasetWriter(
            self.project_root, self.source_root
        ).manifest_rows(chunks)
        released_path = self._relative(
            self.output_root / "knowledge_enriched_chunks.jsonl"
        )
        for row in source_manifest:
            row["knowledge_chunks_path"] = released_path
        atomic_write_jsonl(stage / "knowledge_corpus_manifest.jsonl", source_manifest)
        reporter = KnowledgeQualityReporter(self.project_root, stage)
        reporter.write(chunks)
        if any(
            report.get(key) != value for key, value in reporter.build(chunks).items()
        ):
            raise KnowledgeReleaseError("knowledge_release_report_not_reproducible")
        atomic_write_json(reporter.report_path, report)
        atomic_write_jsonl(
            stage / "manual_review_documents.jsonl",
            report["manual_review"]["manual_review_documents"],
        )

    def _artifact_hashes(self, stage: Path) -> dict[str, str]:
        return {
            path.relative_to(stage).as_posix(): sha256_file(path)
            for path in sorted(stage.rglob("*"))
            if path.is_file()
        }

    def _release_manifest(
        self,
        release_id: str,
        artifact_hashes: dict[str, str],
        report: dict[str, object],
        sampling_manifest: dict[str, object],
        attestation: dict[str, object],
    ) -> dict[str, object]:
        registry_files = self._registry_dependencies(self.registry_path)
        return {
            "release_manifest_version": "v1",
            "dataset_version": self.output_root.name,
            "release_id": release_id,
            "immutability_policy": "create_once_or_verify_identical",
            "manual_review": report["manual_review"],
            "source": {
                "working_dataset_root": self._relative(self.source_root),
                "working_knowledge_sha256": sha256_file(
                    self.source_root / "knowledge_enriched_chunks.jsonl"
                ),
                "enriched_chunks_path": self._relative(self.enriched_path),
                "enriched_chunks_sha256": sha256_file(self.enriched_path),
                "knowledge_config_path": self._relative(self.config_path),
                "knowledge_config_sha256": sha256_file(self.config_path),
                "entity_registry_files": [
                    {"path": self._relative(path), "sha256": sha256_file(path)}
                    for path in registry_files
                ],
            },
            "component_versions": {
                "entity_linking": self.config.entity_linking_version,
                "claim_extraction": self.config.claim_extraction_version,
                "claim_verification": self.config.claim_verification_version,
                "entity_registry": self.config.registry_version,
                "prompt": self.config.prompt_version,
            },
            "artifacts": artifact_hashes,
            "quality": {
                "chunk_count": report["chunk_count"],
                "linked_entity_occurrences": report["entity_linking"][
                    "linked_occurrences"
                ],
                "claim_count": report["claims"]["claim_count"],
                "all_claims_supported": report["all_claims_supported"],
                "pipeline_quality_gates": report["quality_gates"],
                "qa_sampling": sampling_manifest,
                "qa_attestation": attestation,
                "generation_mode": attestation["generation_mode"],
                "pre_embedding_release_ready": report["all_claims_supported"],
            },
            "excluded_stages": [
                "embedding",
                "qdrant_indexing",
                "bm25_indexing",
                "online_retrieval",
                "rrf",
                "rerank",
            ],
        }

    @staticmethod
    def _release_id(artifact_hashes: dict[str, str]) -> str:
        serialized = json.dumps(
            artifact_hashes, sort_keys=True, separators=(",", ":")
        ).encode("utf-8")
        return "knowledge-v2-" + hashlib.sha256(serialized).hexdigest()[:16]

    @staticmethod
    def _registry_dependencies(path: Path) -> list[Path]:
        result: list[Path] = []
        current = path.resolve()
        seen: set[Path] = set()
        while current not in seen:
            seen.add(current)
            payload = yaml.safe_load(current.read_text(encoding="utf-8"))
            if not isinstance(payload, dict):
                raise KnowledgeReleaseError("knowledge_release_registry_invalid")
            result.append(current)
            extends = payload.get("extends")
            if extends is None:
                break
            if not isinstance(extends, str) or not extends:
                raise KnowledgeReleaseError("knowledge_release_registry_extends_invalid")
            candidate = (current.parent / extends).resolve()
            if candidate.parent != current.parent:
                raise KnowledgeReleaseError("knowledge_release_registry_extends_unsafe")
            current = candidate
        else:
            raise KnowledgeReleaseError("knowledge_release_registry_extends_cycle")
        return list(reversed(result))

    @staticmethod
    def _load_json(path: Path) -> dict[str, object]:
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise KnowledgeReleaseError(f"knowledge_release_json_invalid:{path.name}") from exc
        if not isinstance(payload, dict):
            raise KnowledgeReleaseError(f"knowledge_release_json_not_object:{path.name}")
        return payload

    @staticmethod
    def _load_jsonl(path: Path) -> list[dict[str, object]]:
        try:
            rows = [
                json.loads(line)
                for line in path.read_text(encoding="utf-8").splitlines()
                if line.strip()
            ]
        except (OSError, json.JSONDecodeError) as exc:
            raise KnowledgeReleaseError(f"knowledge_release_jsonl_invalid:{path.name}") from exc
        if any(not isinstance(row, dict) for row in rows):
            raise KnowledgeReleaseError(f"knowledge_release_jsonl_not_object:{path.name}")
        return rows

    def _assert_identical_release(self, stage: Path, existing: Path) -> None:
        expected = self._artifact_hashes(stage)
        actual = self._artifact_hashes(existing)
        if expected != actual:
            raise KnowledgeReleaseError("knowledge_release_conflict")

    def _confined(self, path: Path, label: str) -> Path:
        resolved = path.resolve()
        if not resolved.is_relative_to(self.project_root):
            raise KnowledgeReleaseError(f"{label}_path_outside_project")
        return resolved

    def _relative(self, path: Path) -> str:
        return path.resolve().relative_to(self.project_root).as_posix()
