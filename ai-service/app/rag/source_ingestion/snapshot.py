"""Immutable raw file storage and append-only provenance ledger."""

import hashlib
import json
import os
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path

from .http_client import RawHttpResponse
from .models import CORPUS_VERSION, CandidateSource, RawSnapshotRecord, ensure_approved
from .v2_models import SnapshotValidationResult
from .validation import InvalidSnapshotError


class RawSnapshotStore:
    def __init__(
        self,
        project_root: Path,
        *,
        corpus_version: str = CORPUS_VERSION,
        now: Callable[[], datetime] | None = None,
    ) -> None:
        self.project_root = project_root.resolve()
        self.raw_root = self.project_root / "data" / "raw"
        self.manifest_path = self.raw_root / "sources_manifest.jsonl"
        self.corpus_version = corpus_version
        self._now = now or (lambda: datetime.now(UTC))

    def save(
        self,
        candidate: CandidateSource,
        response: RawHttpResponse,
    ) -> RawSnapshotRecord:
        ensure_approved(candidate)
        return self._persist(candidate, response)

    def save_auto_validated(
        self,
        candidate: CandidateSource,
        response: RawHttpResponse,
        validation: SnapshotValidationResult,
    ) -> RawSnapshotRecord:
        if validation.source_id != candidate.source_id:
            raise InvalidSnapshotError("snapshot validation source_id mismatch")
        if not validation.valid:
            reasons = ",".join(validation.reasons) or "unknown"
            raise InvalidSnapshotError(f"snapshot validation failed: {reasons}")
        return self._persist(candidate, response)

    def _persist(
        self,
        candidate: CandidateSource,
        response: RawHttpResponse,
    ) -> RawSnapshotRecord:
        fetched_at = self._now()
        if fetched_at.tzinfo is None or fetched_at.utcoffset() is None:
            raise ValueError("snapshot clock must return a timezone-aware datetime")

        digest = hashlib.sha256(response.content).hexdigest()
        timestamp = fetched_at.astimezone(UTC).strftime("%Y%m%dT%H%M%S.%fZ")
        filename = f"{candidate.source_id}--{timestamp}--{digest[:16]}.{candidate.file_type.value}"
        target_directory = self.raw_root / candidate.file_type.value
        target_directory.mkdir(parents=True, exist_ok=True)
        target_path = target_directory / filename

        # Exclusive creation is the immutability boundary: a collision is an error,
        # never an instruction to replace an earlier snapshot.
        with target_path.open("xb") as handle:
            handle.write(response.content)
            handle.flush()
            os.fsync(handle.fileno())

        record = RawSnapshotRecord(
            source_id=candidate.source_id,
            original_url=candidate.url,
            content_type=response.content_type,
            content_hash=f"sha256:{digest}",
            fetched_at=fetched_at,
            raw_path=target_path.relative_to(self.project_root).as_posix(),
            http_status=response.http_status,
            source_quality=candidate.source_quality,
            corpus_version=self.corpus_version,
        )
        self.raw_root.mkdir(parents=True, exist_ok=True)
        with self.manifest_path.open("a", encoding="utf-8", newline="\n") as handle:
            handle.write(
                json.dumps(record.model_dump(mode="json"), ensure_ascii=False, separators=(",", ":"))
            )
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        return record
