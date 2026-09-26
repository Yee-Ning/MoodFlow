"""Verbatim, source-bound context supplements; never substitute claim evidence."""

from hashlib import sha256
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

from app.agent.foundation.subgraphs.worldline.models import MusicKnowledgeDocument


class ContextSegment(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    role: Literal["article_sentence", "interview_question", "interview_answer", "speaker_binding"]
    exact_span: str = Field(min_length=1)
    start: int = Field(ge=0)
    end: int = Field(gt=0)


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
            if self.content[segment.start:segment.end] != segment.exact_span:
                raise ValueError("context_span_mismatch")
        roles = [segment.role for segment in self.segments]
        if len(roles) != len(set(roles)):
            raise ValueError("context_duplicate_role")
        return self

    def bound_to(self, document: MusicKnowledgeDocument, claim_id: str, exact_span: str) -> bool:
        return (
            self.document_id == document.id
            and self.source_id == document.metadata.get("snapshot_source_id")
            and self.chunk_id == document.metadata.get("chunk_id")
            and self.claim_id == claim_id
            and self.source_url == str(document.url or "")
            and self.publisher == document.source and self.title == document.title
            and self.source_content_sha256 == sha256(document.content.encode()).hexdigest()
            and self.anchor.exact_span == exact_span
            and document.content.count(exact_span) == 1
        )


def source_context(document: MusicKnowledgeDocument, claim_id: str, exact_span: str):
    """Fail closed on malformed, changed or cross-source supplements."""
    try:
        context = RelationSourceContext.model_validate(document.metadata.get("relation_source_context"))
    except ValidationError:
        return None
    return context if context.bound_to(document, claim_id, exact_span) else None
