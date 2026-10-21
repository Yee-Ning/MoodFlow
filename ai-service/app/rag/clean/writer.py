"""Atomic persistence for versioned Cleaned Dataset artifacts."""

from __future__ import annotations

import json
import hashlib
from pathlib import Path

from pydantic import ValidationError

from .errors import RuleRegistryError
from .io import atomic_write_json, atomic_write_jsonl, atomic_write_text
from .models import CleanedDocument, CleanExecutionResult


class CleanedDatasetWriter:
    def __init__(
        self,
        project_root: Path,
        *,
        clean_version: str,
        registry_checksum: str,
        output_root: Path | None = None,
    ) -> None:
        self.project_root = project_root.resolve()
        self.clean_version = clean_version
        self.registry_checksum = registry_checksum
        self.output_root = (
            output_root.resolve()
            if output_root is not None
            else self.project_root / "data" / "cleaned" / clean_version
        )

    def write(self, results: list[CleanExecutionResult]) -> Path:
        self._ensure_registry_compatible()
        manifest_rows: list[dict[str, object]] = []
        for result in sorted(results, key=lambda item: item.document.document_id):
            source_directory = (self.output_root / result.document.source_id).resolve()
            if not source_directory.is_relative_to(self.output_root.resolve()):
                raise RuleRegistryError("cleaned_output_path_outside_root")
            atomic_write_text(
                source_directory / "document.md", result.document.cleaned_text
            )
            atomic_write_json(
                source_directory / "document.json",
                result.document.model_dump(mode="json"),
            )
            manifest_rows.append(
                {
                    "document_id": result.document.document_id,
                    "source_id": result.document.source_id,
                    "source": result.document.source,
                    "source_type": result.document.source_type,
                    "document_type": result.document.document_type,
                    "document_type_origin": result.document.document_type_origin,
                    "document_type_sidecar_version": (
                        result.document.document_type_sidecar_version
                    ),
                    "document_type_sidecar_checksum": (
                        result.document.document_type_sidecar_checksum
                    ),
                    "clean_version": result.document.clean_version,
                    "rule_registry_checksum": result.document.rule_registry_checksum,
                    "rules_applied": result.document.rules_applied,
                    "needs_review": result.document.needs_review,
                    "warnings": result.document.warnings,
                    "changed": result.audit.changed,
                    "normalized_path": result.document.normalized_path,
                    "cleaned_path": (source_directory / "document.json")
                    .relative_to(self.project_root)
                    .as_posix(),
                    "cleaned_sha256": "sha256:" + hashlib.sha256(
                        (source_directory / "document.json").read_bytes()
                    ).hexdigest(),
                }
            )
        manifest_path = self.output_root / "cleaned_corpus_manifest.jsonl"
        atomic_write_jsonl(manifest_path, manifest_rows)
        return manifest_path

    def _ensure_registry_compatible(self) -> None:
        manifest = self.output_root / "cleaned_corpus_manifest.jsonl"
        if manifest.is_file():
            try:
                first = next(
                    line
                    for line in manifest.read_text(encoding="utf-8").splitlines()
                    if line.strip()
                )
                checksum = json.loads(first).get("rule_registry_checksum")
            except (OSError, StopIteration, json.JSONDecodeError, AttributeError):
                checksum = None
            if checksum and checksum != self.registry_checksum:
                raise RuleRegistryError(
                    "clean_version_registry_checksum_conflict; bump clean_version"
                )
            return
        for candidate in sorted(self.output_root.glob("*/document.json")):
            try:
                document = CleanedDocument.model_validate_json(
                    candidate.read_text(encoding="utf-8")
                )
            except (OSError, ValidationError, ValueError):
                continue
            if document.rule_registry_checksum != self.registry_checksum:
                raise RuleRegistryError(
                    "clean_version_registry_checksum_conflict; bump clean_version"
                )
            return
