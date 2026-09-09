"""Approval gate and orchestration for Source Ingestion V1."""

from pathlib import Path

from .fetchers import FETCHERS
from .http_client import PolicyAwareHttpClient
from .manifest import CandidateManifestError, load_candidates
from .models import (
    CandidateSource,
    RawSnapshotRecord,
    ReviewStatus,
    SourceNotApprovedError,
    ensure_approved,
)
from .snapshot import RawSnapshotStore


class SourceIngestionService:
    def __init__(
        self,
        manifest_path: Path,
        snapshot_store: RawSnapshotStore,
        http_client: PolicyAwareHttpClient,
    ) -> None:
        self.manifest_path = manifest_path
        self.snapshot_store = snapshot_store
        self.http_client = http_client

    def get_candidate(self, source_id: str) -> CandidateSource:
        candidates = load_candidates(self.manifest_path)
        matches = [candidate for candidate in candidates if candidate.source_id == source_id]
        if not matches:
            raise CandidateManifestError(f"unknown source_id: {source_id}")
        return matches[0]

    def fetch_one(self, source_id: str) -> RawSnapshotRecord:
        candidate = self.get_candidate(source_id)
        ensure_approved(candidate)
        response = FETCHERS[candidate.file_type].fetch(candidate, self.http_client)
        return self.snapshot_store.save(candidate, response)

    def fetch_all_approved(self) -> list[RawSnapshotRecord]:
        return [
            self.fetch_one(candidate.source_id)
            for candidate in load_candidates(self.manifest_path)
            if candidate.status is ReviewStatus.APPROVED
        ]
