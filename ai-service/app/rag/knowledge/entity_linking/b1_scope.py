"""Immutable, answer-free scope for an approved offline B1 retry."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from dataclasses import dataclass
from types import MappingProxyType

from pydantic import BaseModel, ConfigDict, Field


class B1Occurrence(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    start: int = Field(ge=0)
    end: int = Field(gt=0)
    quote: str = Field(min_length=1)
    reason: str
    candidate_entity_ids: tuple[str, ...]


class B1ScopeEntry(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    audit_id: str
    doc_id: str
    chunk_id: str
    body_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    normalized_text: str
    candidate_entity_ids: tuple[str, ...]
    reason: str
    occurrences: tuple[B1Occurrence, ...] = Field(min_length=1)


@dataclass(frozen=True, init=False)
class B1Scope:
    entries: Mapping[tuple[str, str], B1ScopeEntry]
    sha256: str

    def __init__(self, entries: list[B1ScopeEntry]):
        by_key = {}
        for entry in entries:
            key = (entry.chunk_id, entry.normalized_text)
            if key in by_key:
                raise ValueError("b1_duplicate_scope_entry")
            by_key[key] = entry
        object.__setattr__(self, "entries", MappingProxyType(by_key))
        payload = [by_key[k].model_dump(mode="json") for k in sorted(by_key)]
        digest = hashlib.sha256(
            json.dumps(payload, ensure_ascii=False, sort_keys=True).encode("utf-8")
        ).hexdigest()
        object.__setattr__(self, "sha256", digest)

    def for_chunk(self, chunk_id: str) -> tuple[B1ScopeEntry, ...]:
        return tuple(v for (key, _), v in self.entries.items() if key == chunk_id)

    def payload(self) -> dict:
        return {
            "scope_version": "v1",
            "scope_sha256": self.sha256,
            "entries": [
                self.entries[k].model_dump(mode="json") for k in sorted(self.entries)
            ],
        }
