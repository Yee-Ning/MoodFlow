"""Validated, path-confined access to immutable raw snapshot ledgers."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from pathlib import Path

from pydantic import ValidationError

from app.rag.source_ingestion.models import RawSnapshotRecord

from .base import ParseNormalizeError


class RawManifestError(ParseNormalizeError):
    """A raw snapshot ledger is malformed or inconsistent."""


class RawSnapshotNotFoundError(RawManifestError):
    """No raw snapshot exists for a requested source."""


class RawPathOutsideProjectError(RawManifestError):
    """A ledger row points outside the configured project root."""


class ContentHashMismatchError(RawManifestError):
    """Raw bytes no longer match their immutable ledger hash."""


@dataclass(frozen=True, slots=True)
class LoadedRawSnapshot:
    record: RawSnapshotRecord
    raw_bytes: bytes


class RawSnapshotLoader:
    def __init__(self, project_root: Path) -> None:
        self.project_root = project_root.resolve()

    def load_records(self, manifest_path: Path) -> list[RawSnapshotRecord]:
        try:
            lines = manifest_path.read_text(encoding="utf-8").splitlines()
        except OSError as exc:
            raise RawManifestError("raw_manifest_unreadable") from exc
        records: list[RawSnapshotRecord] = []
        signatures: set[tuple[str, str, str]] = set()
        for line_number, line in enumerate(lines, 1):
            if not line.strip():
                continue
            try:
                record = RawSnapshotRecord.model_validate_json(line)
            except ValidationError as exc:
                raise RawManifestError(
                    f"raw_manifest_invalid_line:{line_number}"
                ) from exc
            signature = (record.source_id, record.content_hash, record.raw_path)
            if signature in signatures:
                raise RawManifestError(f"raw_manifest_duplicate_line:{line_number}")
            signatures.add(signature)
            records.append(record)
        return records

    def latest_records(self, manifest_path: Path) -> dict[str, RawSnapshotRecord]:
        latest: dict[str, RawSnapshotRecord] = {}
        for record in self.load_records(manifest_path):
            existing = latest.get(record.source_id)
            if existing is None or record.fetched_at > existing.fetched_at:
                latest[record.source_id] = record
        return latest

    def load_latest(self, manifest_path: Path, source_id: str) -> LoadedRawSnapshot:
        record = self.latest_records(manifest_path).get(source_id)
        if record is None:
            raise RawSnapshotNotFoundError(f"raw_snapshot_not_found:{source_id}")
        return self.load_record(record)

    def load_record(self, record: RawSnapshotRecord) -> LoadedRawSnapshot:
        candidate_path = (self.project_root / record.raw_path).resolve()
        if not candidate_path.is_relative_to(self.project_root):
            raise RawPathOutsideProjectError("raw_path_outside_project")
        try:
            raw_bytes = candidate_path.read_bytes()
        except OSError as exc:
            raise RawManifestError("raw_snapshot_unreadable") from exc
        actual_hash = f"sha256:{hashlib.sha256(raw_bytes).hexdigest()}"
        if actual_hash != record.content_hash:
            raise ContentHashMismatchError("raw_content_hash_mismatch")
        return LoadedRawSnapshot(record=record, raw_bytes=raw_bytes)
