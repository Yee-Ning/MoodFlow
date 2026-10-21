"""Validate immutable Knowledge releases before offline indexing."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path

from app.rag.knowledge.qa import sha256_file


SHA256_PATTERN = re.compile(r"^sha256:[0-9a-f]{64}$")
RELEASE_ID_PATTERN = re.compile(r"^[a-z0-9][a-z0-9_-]{7,199}$")


class KnowledgeReleaseError(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class ValidatedKnowledgeRelease:
    release_id: str
    dataset_version: str
    manifest_path: Path
    manifest_fingerprint: str
    knowledge_fingerprint: str
    chunk_count: int


class KnowledgeReleaseValidator:
    """Fail closed unless the selected corpus belongs to an approved release."""

    def __init__(
        self,
        project_root: Path,
        *,
        knowledge_path: Path,
        manifest_path: Path | None = None,
    ) -> None:
        self.project_root = project_root.resolve()
        self.knowledge_path = knowledge_path.resolve()
        self.manifest_path = (
            manifest_path.resolve()
            if manifest_path is not None
            else self.knowledge_path.parent / "knowledge_release_manifest.json"
        )
        for path, error in (
            (self.knowledge_path, "knowledge_path_outside_project"),
            (self.manifest_path, "release_manifest_path_outside_project"),
        ):
            if not path.is_relative_to(self.project_root):
                raise KnowledgeReleaseError(error)

    def validate(self) -> ValidatedKnowledgeRelease:
        payload = self._load_manifest()
        release_id = self._required_string(payload, "release_id")
        if not RELEASE_ID_PATTERN.fullmatch(release_id):
            raise KnowledgeReleaseError("knowledge_release_id_invalid")
        dataset_version = self._required_string(payload, "dataset_version")
        if not re.fullmatch(r"v\d+", dataset_version):
            raise KnowledgeReleaseError("knowledge_release_dataset_version_invalid")
        if self.knowledge_path.parent.name != dataset_version:
            raise KnowledgeReleaseError("knowledge_release_dataset_path_mismatch")

        artifacts = payload.get("artifacts")
        if not isinstance(artifacts, dict) or not artifacts:
            raise KnowledgeReleaseError("knowledge_release_artifacts_missing")
        fingerprints = self._verify_artifacts(artifacts)
        knowledge_fingerprint = fingerprints.get(self.knowledge_path.name)
        if knowledge_fingerprint is None:
            raise KnowledgeReleaseError("knowledge_release_corpus_not_declared")
        if sha256_file(self.knowledge_path) != knowledge_fingerprint:
            raise KnowledgeReleaseError("knowledge_release_selected_corpus_hash_mismatch")

        quality = payload.get("quality")
        if not isinstance(quality, dict):
            raise KnowledgeReleaseError("knowledge_release_quality_missing")
        if quality.get("pre_embedding_release_ready") is not True:
            raise KnowledgeReleaseError("knowledge_release_not_embedding_ready")
        if quality.get("all_claims_supported") is not True:
            raise KnowledgeReleaseError("knowledge_release_claims_not_supported")
        gates = quality.get("pipeline_quality_gates")
        required_gates = (
            "all_chunks_processed",
            "zero_failed_chunks",
            "pre_embedding_ready",
        )
        if not isinstance(gates, dict) or any(
            gates.get(name) is not True for name in required_gates
        ):
            raise KnowledgeReleaseError("knowledge_release_quality_gate_failed")
        attestation = quality.get("qa_attestation")
        if not isinstance(attestation, dict):
            raise KnowledgeReleaseError("knowledge_release_attestation_missing")
        if attestation.get("review_status") != "approved":
            raise KnowledgeReleaseError("knowledge_release_attestation_not_approved")
        if not str(attestation.get("reviewer") or "").strip():
            raise KnowledgeReleaseError("knowledge_release_attestation_reviewer_missing")
        if attestation.get("rejected_sample_ids") != []:
            raise KnowledgeReleaseError("knowledge_release_attestation_has_rejections")

        chunk_count = quality.get("chunk_count")
        if not isinstance(chunk_count, int) or chunk_count < 1:
            raise KnowledgeReleaseError("knowledge_release_chunk_count_invalid")
        return ValidatedKnowledgeRelease(
            release_id=release_id,
            dataset_version=dataset_version,
            manifest_path=self.manifest_path,
            manifest_fingerprint=sha256_file(self.manifest_path),
            knowledge_fingerprint=knowledge_fingerprint,
            chunk_count=chunk_count,
        )

    def _load_manifest(self) -> dict[str, object]:
        try:
            payload = json.loads(self.manifest_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise KnowledgeReleaseError("knowledge_release_manifest_unreadable") from exc
        if not isinstance(payload, dict):
            raise KnowledgeReleaseError("knowledge_release_manifest_invalid")
        return payload

    def _verify_artifacts(self, artifacts: dict[object, object]) -> dict[str, str]:
        release_root = self.manifest_path.parent.resolve()
        fingerprints: dict[str, str] = {}
        for relative, expected in artifacts.items():
            if not isinstance(relative, str) or not isinstance(expected, str):
                raise KnowledgeReleaseError("knowledge_release_artifact_entry_invalid")
            if not SHA256_PATTERN.fullmatch(expected):
                raise KnowledgeReleaseError("knowledge_release_artifact_hash_invalid")
            path = (release_root / relative).resolve()
            if not path.is_relative_to(release_root):
                raise KnowledgeReleaseError("knowledge_release_artifact_path_invalid")
            if not path.is_file():
                raise KnowledgeReleaseError(f"knowledge_release_artifact_missing:{relative}")
            actual = sha256_file(path)
            if actual != expected:
                raise KnowledgeReleaseError(
                    f"knowledge_release_artifact_hash_mismatch:{relative}"
                )
            fingerprints[relative] = actual
        return fingerprints

    @staticmethod
    def _required_string(payload: dict[str, object], key: str) -> str:
        value = payload.get(key)
        if not isinstance(value, str) or not value.strip():
            raise KnowledgeReleaseError(f"knowledge_release_{key}_missing")
        return value.strip()
