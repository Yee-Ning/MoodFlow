"""Document-level admission to a release; verification verdicts are immutable."""

from collections import defaultdict

from .models import KnowledgeEnrichedChunk
from .verification.models import VerificationStatus


def select_release_documents(
    chunks: list[KnowledgeEnrichedChunk], *, include_manual_review: bool
) -> tuple[list[KnowledgeEnrichedChunk], dict[str, object]]:
    by_document: dict[str, list[KnowledgeEnrichedChunk]] = defaultdict(list)
    for chunk in chunks:
        by_document[chunk.doc_id].append(chunk)
    review: list[dict[str, object]] = []
    quarantined: set[str] = set()
    for document_id, values in sorted(by_document.items()):
        problems = [
            {
                "chunk_id": chunk.chunk_id,
                "claim_id": item.claim.claim_id,
                "verification_status": item.verification.status.value,
            }
            for chunk in values
            for item in chunk.knowledge.claims
            if item.verification.status is not VerificationStatus.SUPPORTED
        ]
        if not problems:
            continue
        review.append(
            {
                "document_id": document_id,
                "source_id": values[0].enrichment.source.source_id,
                "chunk_ids": sorted(chunk.chunk_id for chunk in values),
                "reasons": ["claim_verification_not_supported"],
                "problem_claims": sorted(
                    problems, key=lambda row: (row["chunk_id"], row["claim_id"])
                ),
                "disposition": "included_by_flag"
                if include_manual_review
                else "quarantined",
            }
        )
        if not include_manual_review:
            quarantined.add(document_id)
    selected = [chunk for chunk in chunks if chunk.doc_id not in quarantined]
    return selected, {
        "input_document_count": len(by_document),
        "include_manual_review": include_manual_review,
        "manual_review_documents": review,
        "manual_review_document_count": len(review),
        "quarantined_document_count": len(quarantined),
        "output_document_count": len({chunk.doc_id for chunk in selected}),
    }
