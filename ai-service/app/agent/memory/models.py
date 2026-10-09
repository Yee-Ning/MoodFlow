from __future__ import annotations

import hashlib
from datetime import datetime
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator


class MemoryType(StrEnum):
    PROFILE = "profile"
    EPISODIC = "episodic"


class MemoryStatus(StrEnum):
    ACTIVE = "active"
    SUPERSEDED = "superseded"
    INACTIVE = "inactive"


class EvidenceStrength(StrEnum):
    INFERRED = "inferred"
    REPEATED_BEHAVIOR = "repeated_behavior"
    EXPLICIT = "explicit"


class ExtractedMemory(BaseModel):
    """One untrusted extraction candidate, validated before persistence."""

    model_config = ConfigDict(extra="forbid")

    memory_type: MemoryType
    category: str = Field(min_length=1, max_length=80)
    subject: str = Field(min_length=1, max_length=300)
    preference: str | None = Field(default=None, max_length=80)
    value: str = Field(min_length=1, max_length=1000)
    content: str = Field(min_length=1, max_length=2000)
    evidence_quote: str = Field(min_length=1, max_length=500)
    evidence_strength: EvidenceStrength
    confidence: float = Field(ge=0, le=1)
    importance: float = Field(ge=0, le=1)
    scene: str | None = Field(default=None, max_length=120)

    @field_validator("category", "subject", "value", "content")
    @classmethod
    def normalize_required_text(cls, value: str) -> str:
        normalized = " ".join(value.split())
        if not normalized:
            raise ValueError("text must not be blank")
        return normalized

    @field_validator("evidence_quote")
    @classmethod
    def preserve_verbatim_quote(cls, value):
        if not value.strip():
            raise ValueError("evidence quote must not be blank")
        return value

    @field_validator("preference", "scene")
    @classmethod
    def normalize_optional_text(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = " ".join(value.split())
        return normalized or None

    @property
    def memory_key(self) -> str:
        category = self.category.casefold().strip()
        subject = self.subject.casefold().strip()
        parts = [category, subject]
        if self.memory_type is MemoryType.EPISODIC:
            parts.append(self.value.casefold().strip())
        key = ":".join(parts)
        if len(key) <= 400:
            return key
        digest = hashlib.sha256(key.encode("utf-8")).hexdigest()
        return f"{key[:328]}:sha256:{digest}"

    @property
    def structured_content(self) -> dict[str, Any]:
        return {
            "category": self.category,
            "subject": self.subject,
            "preference": self.preference,
            "value": self.value,
            "scene": self.scene,
        }


class MemoryExtractionOutput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    memories: list[ExtractedMemory] = Field(default_factory=list, max_length=8)


class MemoryRecord(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    memory_id: str = Field(min_length=1, max_length=160)
    user_id: str = Field(min_length=1, max_length=160)
    memory_type: MemoryType
    memory_key: str = Field(min_length=1, max_length=400)
    content: str = Field(min_length=1, max_length=4000)
    structured_content: dict[str, Any]
    source_thread_id: str = Field(min_length=1, max_length=160)
    confidence: float = Field(ge=0, le=1)
    importance: float = Field(ge=0, le=1)
    evidence_strength: EvidenceStrength
    confirmations: int = Field(default=1, ge=1)
    created_at: datetime
    updated_at: datetime
    status: MemoryStatus = MemoryStatus.ACTIVE
    version: int = Field(default=1, ge=1)
    superseded_by: str | None = Field(default=None, max_length=160)
    embedding: list[float] | None = None
    source_message_id: str | None = Field(default=None, min_length=1, max_length=160)
    evidence_quote: str | None = Field(default=None, min_length=1, max_length=500)


class MemoryWriteAction(StrEnum):
    CREATED = "created"
    CONFIRMED = "confirmed"
    SUPERSEDED = "superseded"
    IGNORED = "ignored"
    REPLAYED = "replayed"


class MemoryEvidence(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    evidence_id: str
    user_id: str
    source_message_id: str
    source_thread_id: str
    memory_type: MemoryType
    memory_key: str
    candidate_digest: str
    memory_id: str | None
    action: MemoryWriteAction
    evidence_quote: str
    observed_at: datetime


class MemoryWriteResult(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    action: MemoryWriteAction
    memory: MemoryRecord | None = None
    previous_memory_id: str | None = None


class MemoryRetrievalRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    user_id: str = Field(min_length=1, max_length=160)
    query: str = Field(default="", max_length=4000)
    intent: str | None = Field(default=None, max_length=120)
    scene: str | None = Field(default=None, max_length=120)
    memory_type: MemoryType | None = None
    top_k: int = Field(default=8, ge=1, le=50)


class RetrievedMemory(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    memory_id: str
    memory_type: MemoryType
    content: str
    structured_content: dict[str, Any]
    confidence: float = Field(ge=0, le=1)
    importance: float = Field(ge=0, le=1)
    relevance: float = Field(ge=0, le=1)
    updated_at: datetime


class MemoryRetrievalResult(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    user_id: str
    items: list[RetrievedMemory] = Field(default_factory=list)

    def context_items(self) -> list[dict[str, Any]]:
        return [
            {
                "memory_id": item.memory_id,
                "memory_type": item.memory_type.value,
                "content": item.content,
                "structured_content": item.structured_content,
                "confidence": item.confidence,
                "importance": item.importance,
                "relevance": item.relevance,
                "updated_at": item.updated_at.isoformat(),
            }
            for item in self.items
        ]
