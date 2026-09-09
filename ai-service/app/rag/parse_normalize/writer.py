"""Idempotent and atomic normalized artifact persistence."""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path

from pydantic import ValidationError

from .models import NormalizedDocument, ParseReport


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
        parser_name: str,
        parser_version: str,
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
            and document.parser_name == parser_name
            and document.parser_version == parser_version
            and document.normalization_version == normalization_version
        )

    def write(self, document: NormalizedDocument, report: ParseReport) -> Path:
        output_directory = self.output_directory(document.source_id)
        output_directory.mkdir(parents=True, exist_ok=True)
        self._write_text(output_directory / "document.md", document.markdown)
        self._write_json(
            output_directory / "document.json", document.model_dump(mode="json")
        )
        self._write_json(
            output_directory / "parse_report.json", report.model_dump(mode="json")
        )
        return output_directory

    def write_failure(self, source_id: str, report: ParseReport) -> Path:
        output_directory = self.output_directory(source_id)
        output_directory.mkdir(parents=True, exist_ok=True)
        self._write_json(
            output_directory / "parse_report.json", report.model_dump(mode="json")
        )
        (output_directory / "document.md").unlink(missing_ok=True)
        (output_directory / "document.json").unlink(missing_ok=True)
        return output_directory

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
