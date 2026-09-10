"""Persistence boundary for source-backed discovery results."""

from __future__ import annotations

import json
from pathlib import Path

from .v2_models import DiscoveredCandidate


class DiscoveryManifestError(ValueError):
    """The discovery manifest is malformed or ambiguous."""


def load_discovered_candidates(path: Path) -> list[DiscoveredCandidate]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise DiscoveryManifestError(f"cannot load discovery manifest: {path}") from exc
    if not isinstance(payload, list):
        raise DiscoveryManifestError("discovery manifest root must be a JSON array")
    candidates = [DiscoveredCandidate.model_validate(item) for item in payload]
    source_ids = [candidate.source_id for candidate in candidates]
    if len(source_ids) != len(set(source_ids)):
        raise DiscoveryManifestError("discovered source_id values must be unique")
    return candidates
