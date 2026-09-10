"""Validated, path-confined access to immutable raw snapshot ledgers."""

from __future__ import annotations

import hashlib
import json
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
    source_metadata: dict[str, object]


class RawSnapshotLoader:
    def __init__(self, project_root: Path) -> None:
        self.project_root = project_root.resolve()

    def load_records(self, manifest_path: Path) -> list[RawSnapshotRecord]:
        return [record for record, _ in self.load_entries(manifest_path)]

    def load_entries(
        self, manifest_path: Path
    ) -> list[tuple[RawSnapshotRecord, dict[str, object]]]:
        try:
            lines = manifest_path.read_text(encoding="utf-8").splitlines()
        except OSError as exc:
            raise RawManifestError("raw_manifest_unreadable") from exc
        entries: list[tuple[RawSnapshotRecord, dict[str, object]]] = []
        signatures: set[tuple[str, str, str]] = set()
        for line_number, line in enumerate(lines, 1):
            if not line.strip():
                continue
            try:
                payload = json.loads(line)
                if not isinstance(payload, dict):
                    raise ValueError("manifest row must be an object")
                raw_fields = {
                    field: payload[field]
                    for field in RawSnapshotRecord.model_fields
                    if field in payload
                }
                record = RawSnapshotRecord.model_validate(raw_fields)
            except (json.JSONDecodeError, ValidationError, ValueError) as exc:
                raise RawManifestError(
                    f"raw_manifest_invalid_line:{line_number}"
                ) from exc
            signature = (record.source_id, record.content_hash, record.raw_path)
            if signature in signatures:
                raise RawManifestError(f"raw_manifest_duplicate_line:{line_number}")
            signatures.add(signature)
            entries.append((record, payload))
        return entries

    def latest_records(self, manifest_path: Path) -> dict[str, RawSnapshotRecord]:
        latest: dict[str, RawSnapshotRecord] = {}
        for record in self.load_records(manifest_path):
            existing = latest.get(record.source_id)
            if existing is None or record.fetched_at > existing.fetched_at:
                latest[record.source_id] = record
        return latest

    def latest_entries(
        self, manifest_path: Path
    ) -> dict[str, tuple[RawSnapshotRecord, dict[str, object]]]:
        latest: dict[str, tuple[RawSnapshotRecord, dict[str, object]]] = {}
        for record, metadata in self.load_entries(manifest_path):
            existing = latest.get(record.source_id)
            if existing is None or record.fetched_at > existing[0].fetched_at:
                latest[record.source_id] = (record, metadata)
        return latest

    def load_latest(self, manifest_path: Path, source_id: str) -> LoadedRawSnapshot:
        entry = self.latest_entries(manifest_path).get(source_id)
        if entry is None:
            raise RawSnapshotNotFoundError(f"raw_snapshot_not_found:{source_id}")
        record, metadata = entry
        return self.load_record(record, source_metadata=metadata)

    def load_record(
        self,
        record: RawSnapshotRecord,
        *,
        source_metadata: dict[str, object] | None = None,
    ) -> LoadedRawSnapshot:
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
        return LoadedRawSnapshot(
            record=record,
            raw_bytes=raw_bytes,
            source_metadata=source_metadata or record.model_dump(mode="json"),
        )
