"""Materialize a verified parent assertion only in its unique original child."""

from hashlib import sha256

from app.rag.chunk.models import ChildChunk
from .claims.extractor import ClaimExtractor
from .claims.models import ClaimCandidate
from .claims.ids import build_claim_id
from .models import KnowledgeStats
from .verification.models import ChildClaimGrounding, VerifiedClaim, VerificationStatus


def source_metadata(chunk):
    return chunk.metadata.get("document_metadata", chunk.metadata).get("source_metadata", {})


def validate_grounding(item, child, parent):
    grounding = item.grounding
    if grounding is None:
        return
    claim = item.claim
    candidate = ClaimCandidate.model_validate(claim.model_dump(exclude={"claim_id", "source_chunk_id", "extraction_source"}))
    if (item.verification.status != VerificationStatus.SUPPORTED
            or grounding.derived_from != build_claim_id(candidate, parent.chunk_id)
            or claim.claim_id != build_claim_id(candidate, child.chunk_id)
            or grounding.parent_chunk_id != parent.chunk_id
            or grounding.child_chunk_id != child.chunk_id
            or grounding.document_id != child.doc_id or parent.doc_id != child.doc_id
            or child.parent_id != parent.chunk_id or claim.source_chunk_id != child.chunk_id
            or grounding.source_id != child.enrichment.source.source_id
            or grounding.source_id != parent.enrichment.source.source_id
            or source_metadata(child) != source_metadata(parent)
            or grounding.response_sha256 != str(source_metadata(parent).get("content_hash", "")).removeprefix("sha256:")
            or grounding.parent_content_sha256 != sha256(parent.content.encode()).hexdigest()
            or grounding.child_content_sha256 != sha256(child.content.encode()).hexdigest()
            or parent.content[grounding.parent_start:grounding.parent_end] != claim.evidence_span
            or child.content[grounding.child_start:grounding.child_end] != claim.evidence_span
            or item.verification.evidence_span != claim.evidence_span):
        raise ValueError("parent_child_claim_grounding_mismatch")


def ground_parent_claims(chunks):
    result = list(chunks)
    for parent in chunks:
        if isinstance(parent, ChildChunk):
            continue
        source_id = parent.enrichment.source.source_id
        # Parse-normalize records the immutable HTTP response hash here.
        source_meta = source_metadata(parent)
        raw_hash = str(source_meta.get("content_hash", "")).removeprefix("sha256:")
        if len(raw_hash) != 64 or any(c not in "0123456789abcdef" for c in raw_hash):
            continue
        children = [(i, child) for i, child in enumerate(result) if isinstance(child, ChildChunk)
                    and child.parent_id == parent.chunk_id and child.doc_id == parent.doc_id
                    and child.enrichment.source.source_id == source_id
                    and source_metadata(child) == source_meta]
        for item in parent.knowledge.claims:
            claim = item.claim
            span = claim.evidence_span
            if (item.verification.status != VerificationStatus.SUPPORTED
                    or claim.source_chunk_id != parent.chunk_id
                    or parent.content.count(span) != 1
                    or item.verification.evidence_span != span):
                continue
            matches = [(i, child) for i, child in children if child.content.count(span) == 1]
            if len(matches) != 1:
                continue
            index, child = matches[0]
            # A child must itself map uniquely into this parent, not merely share
            # one phrase with an unrelated content range.
            if parent.content.count(child.content) != 1:
                continue
            ps, cs = parent.content.index(span), child.content.index(span)
            if parent.content.index(child.content) + cs != ps:
                continue
            ids = {entity.entity_id for entity in child.knowledge.entities.linked_entities}
            if claim.subject_entity_id not in ids or (claim.object_entity_id and claim.object_entity_id not in ids):
                continue
            candidate = ClaimCandidate.model_validate(claim.model_dump(exclude={"claim_id", "source_chunk_id", "extraction_source"}))
            derived = ClaimExtractor._claim(candidate, child.chunk_id, claim.extraction_source)
            # Do not duplicate or replace an independently extracted child fact.
            if any(existing.claim.predicate == claim.predicate
                   and existing.claim.subject_entity_id == claim.subject_entity_id
                   and existing.claim.object_entity_id == claim.object_entity_id
                   and existing.claim.object_value == claim.object_value
                   and existing.claim.time == claim.time
                   and existing.claim.evidence_span == span for existing in child.knowledge.claims):
                continue
            grounding = ChildClaimGrounding(
                derived_from=claim.claim_id, source_id=source_id, document_id=parent.doc_id,
                parent_chunk_id=parent.chunk_id, child_chunk_id=child.chunk_id,
                parent_start=ps, parent_end=ps + len(span), child_start=cs, child_end=cs + len(span),
                parent_content_sha256=sha256(parent.content.encode()).hexdigest(),
                child_content_sha256=sha256(child.content.encode()).hexdigest(), response_sha256=raw_hash,
            )
            claims = [*child.knowledge.claims, VerifiedClaim(claim=derived, verification=item.verification, grounding=grounding)]
            child = child.model_copy(update={"knowledge": child.knowledge.model_copy(update={
                "claims": claims, "stats": KnowledgeStats.build(child.knowledge.entities, claims)})})
            result[index] = child
            children = [(i, child if i == index else previous) for i, previous in children]
    return result
