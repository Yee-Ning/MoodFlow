"""Payload validation, exclusion handling, parent collapse, and diversity caps."""

from __future__ import annotations

from collections import defaultdict
from typing import Any

from pydantic import ValidationError

from app.rag.query_understanding.models import QueryType, SearchPlan

from .config import RetrievalConfig
from .fusion import FusedSearchHit
from .models import ChannelEvidence, DeferredParentContext, RetrievalCandidate


BROAD_QUERY_TYPES = {
    QueryType.TIMELINE,
    QueryType.COMPARISON,
    QueryType.INFLUENCE_STYLE,
    QueryType.EXPLORATORY,
}


def select_candidates(
    hits: list[FusedSearchHit],
    plan: SearchPlan,
    config: RetrievalConfig,
) -> tuple[list[RetrievalCandidate], list[str]]:
    warnings: list[str] = []
    candidates_by_parent: dict[str, RetrievalCandidate] = {}
    invalid_count = 0
    excluded_count = 0
    navigation_count = 0
    for hit in hits:
        if _matches_exclusion(hit.payload, plan.post_exclusions):
            excluded_count += 1
            continue
        if _is_timeline_navigation_hit(hit.payload, plan):
            navigation_count += 1
            continue
        try:
            candidate = _candidate_from_hit(hit)
        except (TypeError, ValueError, ValidationError):
            invalid_count += 1
            continue
        existing = candidates_by_parent.get(candidate.parent_id)
        if existing is None:
            candidates_by_parent[candidate.parent_id] = candidate
        else:
            candidates_by_parent[candidate.parent_id] = _merge_parent(
                existing,
                candidate,
            )
    if invalid_count:
        warnings.append(f"invalid_retrieval_payloads:{invalid_count}")
    if excluded_count:
        warnings.append(f"post_exclusions_applied:{excluded_count}")
    if navigation_count:
        warnings.append(f"timeline_navigation_sections_removed:{navigation_count}")

    ranked = sorted(
        candidates_by_parent.values(),
        key=lambda item: (-item.fusion_score, item.point_id),
    )
    target = min(plan.candidate_top_k, config.selection.maximum_candidates)
    document_cap = (
        config.selection.broad_document_cap
        if set(plan.query_types) & BROAD_QUERY_TYPES
        else config.selection.default_document_cap
    )
    selected: list[RetrievalCandidate] = []
    overflow: list[RetrievalCandidate] = []
    document_counts: dict[str, int] = defaultdict(int)
    source_counts: dict[str, int] = defaultdict(int)
    for candidate in ranked:
        if (
            document_counts[candidate.doc_id] >= document_cap
            or source_counts[candidate.source_id] >= config.selection.source_cap
        ):
            overflow.append(candidate)
            continue
        selected.append(candidate)
        document_counts[candidate.doc_id] += 1
        source_counts[candidate.source_id] += 1
        if len(selected) >= target:
            break

    if len(selected) < target and overflow:
        selected.extend(overflow[: target - len(selected)])
        warnings.append("selection_caps_relaxed_for_recall")
    return selected[:target], warnings


def _matches_exclusion(payload: dict[str, Any], terms: list[str]) -> bool:
    if not terms:
        return False
    values: list[str] = []
    for field in ("content", "retrieval_content", "breadcrumb"):
        value = payload.get(field)
        if isinstance(value, str):
            values.append(value)
    for field in ("topics", "keywords"):
        value = payload.get(field)
        if isinstance(value, list):
            values.extend(item for item in value if isinstance(item, str))
    haystack = "\n".join(values).casefold()
    return any(term.strip().casefold() in haystack for term in terms if term.strip())


def _is_timeline_navigation_hit(payload: dict[str, Any], plan: SearchPlan) -> bool:
    if QueryType.TIMELINE not in plan.query_types:
        return False
    breadcrumb = payload.get("breadcrumb")
    if not isinstance(breadcrumb, str):
        return False
    section = breadcrumb.rsplit(">", maxsplit=1)[-1].strip().casefold()
    return section in {"外部連結", "外部链接", "external links"}


def _candidate_from_hit(hit: FusedSearchHit) -> RetrievalCandidate:
    payload = hit.payload
    chunk_id = _string(payload, "chunk_id")
    parent_id = _string(payload, "parent_id")
    doc_id = _string(payload, "doc_id")
    child_content = _string(payload, "content")
    breadcrumb = _optional_string(payload.get("breadcrumb")) or ""
    parent = payload.get("parent_context")
    if not isinstance(parent, dict):
        raise ValueError("parent context missing")
    if _string(parent, "chunk_id") != parent_id:
        raise ValueError("parent identity mismatch")
    parent_content = _string(parent, "content")
    source_id = _string(payload, "source_id")
    source_name = _optional_string(payload.get("source_name")) or source_id
    entity_ids = _string_list(payload.get("entity_ids"))
    supported_claims = payload.get("supported_claims") or []
    if not isinstance(supported_claims, list) or any(
        not isinstance(item, dict) for item in supported_claims
    ):
        raise ValueError("supported claims invalid")
    child_tokens = _integer(payload.get("token_count"))
    parent_tokens = _integer(parent.get("token_count"))
    metadata = {
        key: value
        for key in (
            "chunk_index",
            "section_path",
            "content_type",
            "language",
            "knowledge_status",
            "entity_types",
        )
        if (value := payload.get(key)) is not None
    }
    return RetrievalCandidate(
        point_id=hit.point_id,
        parent_id=parent_id,
        doc_id=doc_id,
        title=breadcrumb or source_name,
        primary_child_id=chunk_id,
        primary_child_content=child_content,
        primary_child_breadcrumb=breadcrumb,
        primary_child_token_count=child_tokens,
        parent_context=DeferredParentContext(
            chunk_id=parent_id,
            content=parent_content,
            token_count=parent_tokens,
            breadcrumb=_optional_string(parent.get("breadcrumb")) or "",
        ),
        source_id=source_id,
        source_name=source_name,
        source_url=_optional_string(payload.get("source_url")),
        entity_ids=entity_ids,
        supported_claims=supported_claims,
        fusion_score=hit.fusion_score,
        channel_evidence=hit.evidence,
        matched_child_ids=[chunk_id],
        metadata=metadata,
    )


def _merge_parent(
    primary: RetrievalCandidate,
    additional: RetrievalCandidate,
) -> RetrievalCandidate:
    if additional.fusion_score > primary.fusion_score:
        primary, additional = additional, primary
    child_ids = list(
        dict.fromkeys([*primary.matched_child_ids, *additional.matched_child_ids])
    )[:20]
    evidence: list[ChannelEvidence] = []
    seen: set[tuple[str, int, str, int]] = set()
    for item in [*primary.channel_evidence, *additional.channel_evidence]:
        key = (item.chunk_id, item.variant_index, item.channel.value, item.rank)
        if key not in seen:
            seen.add(key)
            evidence.append(item)
    payload = primary.model_dump(mode="python")
    payload["matched_child_ids"] = child_ids
    payload["channel_evidence"] = evidence[:20]
    return RetrievalCandidate.model_validate(payload)


def _string(payload: dict[str, Any], field: str) -> str:
    value = payload.get(field)
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"payload field missing: {field}")
    return value.strip()


def _optional_string(value: Any) -> str | None:
    return value.strip() if isinstance(value, str) and value.strip() else None


def _integer(value: Any) -> int:
    if not isinstance(value, int) or value < 0:
        raise ValueError("payload token count invalid")
    return value


def _string_list(value: Any) -> list[str]:
    if value is None:
        return []
    if not isinstance(value, list) or any(not isinstance(item, str) for item in value):
        raise ValueError("payload string list invalid")
    return list(dict.fromkeys(item for item in value if item))
