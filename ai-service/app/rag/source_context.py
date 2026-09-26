"""Verbatim, source-bound context supplements; never substitute claim evidence."""

from __future__ import annotations

from hashlib import sha256
from typing import TYPE_CHECKING, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

if TYPE_CHECKING:
    from app.agent.foundation.subgraphs.worldline.models import MusicKnowledgeDocument


class ContextSegment(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    role: Literal[
        "article_sentence", "interview_question", "interview_answer", "speaker_binding"
    ]
    exact_span: str = Field(min_length=1)
    start: int = Field(ge=0)
    end: int = Field(gt=0)


class DocumentContextCapture(BaseModel):
    """Verbatim document metadata before native chunk/claim IDs exist.

    Ingestion supplies offsets and roles from the original source. Indexing
    binds them to verified claims; it never infers a speaker or direction.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    source_url: str = Field(pattern=r"^https?://")
    publisher: str = Field(min_length=1)
    title: str = Field(min_length=1)
    locator: str = Field(min_length=1)
    captured_at: str = Field(min_length=1)
    capture_method: Literal["verbatim_web_excerpt"] = "verbatim_web_excerpt"
    response_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    content: str = Field(min_length=1, max_length=6000)
    content_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    segments: list[ContextSegment] = Field(min_length=1, max_length=8)

    @model_validator(mode="after")
    def check_capture(self):
        if sha256(self.content.encode()).hexdigest() != self.content_sha256:
            raise ValueError("context_content_hash_mismatch")
        for segment in self.segments:
            if self.content[segment.start : segment.end] != segment.exact_span:
                raise ValueError("context_span_mismatch")
        if len({s.role for s in self.segments}) != len(self.segments):
            raise ValueError("context_duplicate_role")
        return self


class RelationSourceContext(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal["relation-source-context-v1"] = "relation-source-context-v1"
    context_id: str = Field(min_length=1)
    source_id: str = Field(min_length=1)
    document_id: str = Field(min_length=1)
    chunk_id: str = Field(min_length=1)
    claim_id: str = Field(min_length=1)
    source_url: str = Field(pattern=r"^https?://")
    publisher: str = Field(min_length=1)
    title: str = Field(min_length=1)
    locator: str = Field(min_length=1)
    captured_at: str = Field(min_length=1)
    capture_method: Literal["verbatim_web_excerpt"] = "verbatim_web_excerpt"
    response_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    source_content_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    content: str = Field(min_length=1, max_length=6000)
    content_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    anchor: ContextSegment
    segments: list[ContextSegment] = Field(min_length=1, max_length=8)

    @model_validator(mode="after")
    def check_capture(self):
        if sha256(self.content.encode()).hexdigest() != self.content_sha256:
            raise ValueError("context_content_hash_mismatch")
        for segment in [self.anchor, *self.segments]:
            if self.content[segment.start : segment.end] != segment.exact_span:
                raise ValueError("context_span_mismatch")
        roles = [segment.role for segment in self.segments]
        if len(roles) != len(set(roles)):
            raise ValueError("context_duplicate_role")
        return self

    def bound_to(
        self, document: MusicKnowledgeDocument, claim_id: str, exact_span: str
    ) -> bool:
        # Native retrieval carries the immutable Child separately from the
        # compressed generation context. Replay callers use document.content.
        original = document.metadata.get("source_evidence_content", document.content)
        return (
            self.document_id == document.id
            and self.source_id
            == (
                document.metadata.get("source_id")
                or document.metadata.get("snapshot_source_id")
            )
            and self.chunk_id == document.metadata.get("chunk_id")
            and self.claim_id == claim_id
            and self.source_url == str(document.url or "")
            and self.publisher == document.source
            and self.title == document.title
            and isinstance(original, str)
            and self.source_content_sha256 == sha256(original.encode()).hexdigest()
            and original.count(exact_span) == 1
            and self.anchor.exact_span == exact_span
            and document.content.count(exact_span) == 1
        )


def source_context(document: MusicKnowledgeDocument, claim_id: str, exact_span: str):
    """Fail closed on malformed, changed or cross-source supplements."""
    payloads = document.metadata.get("relation_source_contexts")
    if payloads is None:
        payloads = [document.metadata.get("relation_source_context")]
    if not isinstance(payloads, list):
        return None
    matched = []
    for payload in payloads:
        try:
            context = RelationSourceContext.model_validate(payload)
        except ValidationError:
            continue
        if context.bound_to(document, claim_id, exact_span):
            matched.append(context)
    return matched[0] if len(matched) == 1 else None


def relation_provenance(
    document: MusicKnowledgeDocument, claim_id: str, exact_span: str
) -> dict:
    """Carry trusted retrieval provenance without changing relation decisions."""
    metadata = {
        key: document.metadata[key]
        for key in (
            "source_id",
            "source_locator",
            "source_content_sha256",
            "source_release_id",
            "knowledge_input_hash",
            "index_input_hash",
            "source_published_at",
        )
        if key in document.metadata
    }
    context = source_context(document, claim_id, exact_span)
    if context is not None:
        metadata["relation_source_context"] = context.model_dump(mode="json")
    return metadata
