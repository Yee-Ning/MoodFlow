"""Idempotent and atomic normalized artifact persistence."""

from __future__ import annotations

import json
import os
import shutil
import tempfile
from pathlib import Path, PurePosixPath

from pydantic import ValidationError

from .base import ParseNormalizeError
from .models import DetectedFileType, NormalizedDocument, OutputArtifact, ParseReport


class UnsafeOutputArtifactError(ParseNormalizeError):
    """A parser tried to write an attachment outside its image directory."""


class AtomicNormalizedWriter:
    def __init__(self, project_root: Path) -> None:
        self.project_root = project_root.resolve()
        self.normalized_root = self.project_root / "data" / "normalized"

    def output_directory(self, source_id: str) -> Path:
        return self.normalized_root / source_id

    def fingerprint_matches(
        self,
        source_id: str,
        *,
        raw_content_hash: str,
        parse_fingerprint: str,
        normalization_version: str,
    ) -> bool:
        document_path = self.output_directory(source_id) / "document.json"
        try:
            document = NormalizedDocument.model_validate_json(
                document_path.read_text(encoding="utf-8")
            )
        except (OSError, ValidationError):
            return False
        return (
            document.raw_content_hash == raw_content_hash
            and document.metadata.get("parse_fingerprint") == parse_fingerprint
            and document.normalization_version == normalization_version
        )

    def write(
        self,
        document: NormalizedDocument,
        report: ParseReport,
        *,
        artifacts: list[OutputArtifact] | tuple[OutputArtifact, ...] = (),
    ) -> Path:
        output_directory = self.output_directory(document.source_id)
        artifact_targets = [
            (self._artifact_target(output_directory, artifact), artifact)
            for artifact in artifacts
        ]
        output_directory.mkdir(parents=True, exist_ok=True)
        images_directory = output_directory / "images"
        if document.original_file_type is DetectedFileType.PDF:
            self._remove_images_directory(output_directory)
            images_directory.mkdir(parents=True, exist_ok=True)
        self._write_text(output_directory / "document.md", document.markdown)
        self._write_json(
            output_directory / "document.json", document.model_dump(mode="json")
        )
        self._write_json(
            output_directory / "parse_report.json", report.model_dump(mode="json")
        )
        for target, artifact in artifact_targets:
            self._write_bytes(target, artifact.content)
        return output_directory

    def write_failure(self, source_id: str, report: ParseReport) -> Path:
        output_directory = self.output_directory(source_id)
        output_directory.mkdir(parents=True, exist_ok=True)
        self._write_json(
            output_directory / "parse_report.json", report.model_dump(mode="json")
        )
        (output_directory / "document.md").unlink(missing_ok=True)
        (output_directory / "document.json").unlink(missing_ok=True)
        self._remove_images_directory(output_directory)
        return output_directory

    @staticmethod
    def _artifact_target(output_directory: Path, artifact: OutputArtifact) -> Path:
        relative_path = PurePosixPath(artifact.relative_path)
        if (
            relative_path.is_absolute()
            or len(relative_path.parts) < 2
            or relative_path.parts[0] != "images"
            or ".." in relative_path.parts
            or any("\\" in part for part in relative_path.parts)
        ):
            raise UnsafeOutputArtifactError("unsafe_output_artifact_path")
        target = (output_directory / Path(*relative_path.parts)).resolve()
        images_root = (output_directory / "images").resolve()
        if not target.is_relative_to(images_root):
            raise UnsafeOutputArtifactError("unsafe_output_artifact_path")
        return target

    @staticmethod
    def _remove_images_directory(output_directory: Path) -> None:
        images_directory = (output_directory / "images").resolve()
        resolved_output = output_directory.resolve()
        if not images_directory.is_relative_to(resolved_output):
            raise UnsafeOutputArtifactError("unsafe_output_artifact_path")
        if images_directory.exists():
            shutil.rmtree(images_directory)

    def _write_json(self, target: Path, value: object) -> None:
        serialized = json.dumps(value, ensure_ascii=False, indent=2) + "\n"
        self._write_text(target, serialized)

    @staticmethod
    def _write_text(target: Path, value: str) -> None:
        target.parent.mkdir(parents=True, exist_ok=True)
        temporary_path: Path | None = None
        try:
            with tempfile.NamedTemporaryFile(
                mode="w",
                encoding="utf-8",
                newline="\n",
                delete=False,
                dir=target.parent,
                prefix=f".{target.name}.",
                suffix=".tmp",
            ) as handle:
                temporary_path = Path(handle.name)
                handle.write(value)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary_path, target)
        finally:
            if temporary_path is not None:
                temporary_path.unlink(missing_ok=True)

    @staticmethod
    def _write_bytes(target: Path, value: bytes) -> None:
        target.parent.mkdir(parents=True, exist_ok=True)
        temporary_path: Path | None = None
        try:
            with tempfile.NamedTemporaryFile(
                mode="wb",
                delete=False,
                dir=target.parent,
                prefix=f".{target.name}.",
                suffix=".tmp",
            ) as handle:
                temporary_path = Path(handle.name)
                handle.write(value)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary_path, target)
        finally:
            if temporary_path is not None:
                temporary_path.unlink(missing_ok=True)
