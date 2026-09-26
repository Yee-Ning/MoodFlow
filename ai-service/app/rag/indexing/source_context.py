"""Bind captured source metadata to native indexed claims; no semantic inference."""

from hashlib import sha256
from types import SimpleNamespace

from app.rag.source_context import DocumentContextCapture, RelationSourceContext


def project_source_contexts(chunk, parent, claims: list[dict]) -> list[dict]:
    # Captures come from source/document metadata, never from Gold or reviews.
    metadata = chunk.metadata.get("document_metadata", {})
    if not isinstance(metadata, dict):
        raise ValueError("index_document_metadata_invalid")
    captures = chunk.metadata.get("relation_source_contexts")
    if captures is None:
        captures = metadata.get("relation_source_contexts", [])
    if not isinstance(captures, list) or len(captures) > 8:
        raise ValueError("index_source_contexts_invalid")
    source = chunk.enrichment.source
    contexts = [RelationSourceContext.model_validate(item) for item in captures]
    raw_captures = metadata.get("source_context_captures", [])
    if not isinstance(raw_captures, list) or len(raw_captures) > 8:
        raise ValueError("index_document_captures_invalid")
    for payload in raw_captures:
        capture = DocumentContextCapture.model_validate(payload)
        if capture.source_url != str(source.source_url or "") or capture.publisher != (
            source.source_name or source.source_id
        ):
            raise ValueError("index_document_capture_source_mismatch")
        for claim in claims:
            span = claim["evidence_span"]
            roles = [s for s in capture.segments if span in s.exact_span]
            if capture.content.count(span) != 1 or len(roles) != 1:
                continue
            start = capture.content.index(span)
            context_id = (
                "context:"
                + sha256(
                    (
                        chunk.chunk_id + claim["claim_id"] + capture.content_sha256
                    ).encode()
                ).hexdigest()[:24]
            )
            contexts.append(
                RelationSourceContext(
                    **capture.model_dump(),
                    context_id=context_id,
                    source_id=source.source_id,
                    document_id=chunk.parent_id,
                    chunk_id=chunk.chunk_id,
                    claim_id=claim["claim_id"],
                    source_content_sha256=sha256(chunk.content.encode()).hexdigest(),
                    anchor={
                        "role": roles[0].role,
                        "exact_span": span,
                        "start": start,
                        "end": start + len(span),
                    },
                )
            )
    if len(contexts) > 8:
        raise ValueError("index_source_context_limit_exceeded")
    titles = {item.title for item in contexts}
    if len(titles) > 1:
        raise ValueError("index_source_context_title_mismatch")
    document = SimpleNamespace(
        id=chunk.parent_id,
        content=chunk.content,
        title=next(iter(titles), chunk.breadcrumb),
        source=source.source_name or source.source_id,
        url=source.source_url,
        metadata={"source_id": source.source_id, "chunk_id": chunk.chunk_id},
    )
    by_claim = {item["claim_id"]: item for item in claims}
    seen = set()
    result = []
    for context in contexts:
        claim = by_claim.get(context.claim_id)
        if (
            claim is None
            or context.claim_id in seen
            or not context.bound_to(document, context.claim_id, claim["evidence_span"])
            or context.anchor.exact_span not in parent.content
        ):
            raise ValueError("index_source_context_binding_mismatch")
        seen.add(context.claim_id)
        result.append(context.model_dump(mode="json"))
    return result


def source_payload_provenance(chunk, parent) -> dict:
    return {
        "source_content_sha256": sha256(chunk.content.encode()).hexdigest(),
        "parent_content_sha256": sha256(parent.content.encode()).hexdigest(),
        "source_locator": " > ".join(chunk.section_path) or chunk.breadcrumb,
        "source_title": chunk.metadata.get("title") or chunk.breadcrumb,
    }
