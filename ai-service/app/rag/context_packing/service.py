"""Deterministic post-rerank evidence processing for online generation."""

from __future__ import annotations

import re
from collections import Counter
from collections.abc import Sequence
from dataclasses import dataclass
from types import SimpleNamespace

from pydantic import ValidationError

from app.rag.source_context import RelationSourceContext

from app.rag.chunk.token_counter import TokenCounter, UnicodeHeuristicTokenCounter
from app.rag.context_packing.config import (
    ContextPackingConfig,
    load_context_packing_config,
)
from app.rag.context_packing.models import (
    ContextEvidence,
    ContextPack,
    EvidenceSufficiency,
    EvidenceSufficiencyStatus,
)
from app.rag.query_understanding.models import QueryType, QueryVariantKind, SearchPlan
from app.rag.reranking.models import RerankResult, RerankStatus
from app.rag.retrieval.models import RetrievalCandidate, RetrievalResult, RetrievalStatus


class EvidenceSufficiencyGate:
    """Apply hard evidence/coverage checks without an uncalibrated score threshold."""

    def __init__(self, config: ContextPackingConfig | None = None) -> None:
        self.config = config or load_context_packing_config()

    def assess(
        self,
        plan: SearchPlan,
        result: RetrievalResult | RerankResult,
    ) -> EvidenceSufficiency:
        if result.plan_id != plan.plan_id:
            raise ValueError("context_pack_plan_id_mismatch")
        candidates = _ranked_candidates(result)
        required_subqueries, required_entities = self._requirements(plan)
        if _unavailable(result):
            return EvidenceSufficiency(
                status=EvidenceSufficiencyStatus.UNAVAILABLE,
                required_subquery_ids=required_subqueries,
                missing_subquery_ids=required_subqueries,
                required_entity_ids=required_entities,
                missing_entity_ids=required_entities,
                reasons=["retrieval_or_rerank_unavailable"],
            )
        if not candidates:
            return EvidenceSufficiency(
                status=EvidenceSufficiencyStatus.INSUFFICIENT,
                required_subquery_ids=required_subqueries,
                missing_subquery_ids=required_subqueries,
                required_entity_ids=required_entities,
                missing_entity_ids=required_entities,
                reasons=["candidate_pool_empty"],
            )

        candidate_subqueries = set(
            evidence.subquery_id
            for candidate in candidates
            for evidence in candidate.channel_evidence
        )
        candidate_entities = set(
            entity_id for candidate in candidates for entity_id in candidate.entity_ids
        )
        covered_subqueries = [
            item for item in required_subqueries if item in candidate_subqueries
        ]
        covered_entities = [
            item for item in required_entities if item in candidate_entities
        ]
        missing_subqueries = [
            item for item in required_subqueries if item not in covered_subqueries
        ]
        missing_entities = [
            item for item in required_entities if item not in covered_entities
        ]
        reasons = []
        if missing_subqueries:
            reasons.append("decomposed_query_coverage_missing")
        if missing_entities:
            reasons.append("comparison_entity_coverage_missing")
        return EvidenceSufficiency(
            status=(
                EvidenceSufficiencyStatus.INSUFFICIENT
                if reasons
                else EvidenceSufficiencyStatus.SUFFICIENT
            ),
            required_subquery_ids=required_subqueries,
            covered_subquery_ids=covered_subqueries,
            missing_subquery_ids=missing_subqueries,
            required_entity_ids=required_entities,
            covered_entity_ids=covered_entities,
            missing_entity_ids=missing_entities,
            reasons=reasons,
        )

    def _requirements(self, plan: SearchPlan) -> tuple[list[str], list[str]]:
        required_subqueries = (
            [
                f"q{index}:{variant.kind.value}"
                for index, variant in enumerate(plan.queries)
                if variant.kind is QueryVariantKind.DECOMPOSED
            ]
            if self.config.gate.require_decomposed_query_coverage
            else []
        )
        required_entities = (
            list(
                dict.fromkeys(
                    item.knowledge_entity_id
                    for item in plan.entities
                    if item.knowledge_entity_id is not None
                )
            )
            if self.config.gate.require_comparison_entity_coverage
            and QueryType.COMPARISON in plan.query_types
            else []
        )
        return required_subqueries, required_entities


@dataclass(frozen=True, slots=True)
class _EvidenceDraft:
    source_rank: int
    candidate: RetrievalCandidate
    exact_text: str
    context: str
    token_count: int
    subquery_ids: tuple[str, ...]
    retrieval_legs: tuple[str, ...]
    rerank_score: float | None
    relation_source_contexts: tuple[RelationSourceContext, ...] = ()
    context_warnings: tuple[str, ...] = ()


class ContextPackBuilder:
    def __init__(
        self,
        config: ContextPackingConfig | None = None,
        *,
        token_counter: TokenCounter | None = None,
        gate: EvidenceSufficiencyGate | None = None,
    ) -> None:
        self.config = config or load_context_packing_config()
        self.token_counter = token_counter or UnicodeHeuristicTokenCounter()
        self.gate = gate or EvidenceSufficiencyGate(self.config)

    def build(
        self,
        plan: SearchPlan,
        result: RetrievalResult | RerankResult,
    ) -> ContextPack:
        sufficiency = self.gate.assess(plan, result)
        if sufficiency.status is EvidenceSufficiencyStatus.UNAVAILABLE:
            return self._empty_pack(plan, sufficiency)
        drafts = self._extract_drafts(plan.standalone_query, result)
        drafts, duplicate_count = self._deduplicate(drafts)
        selected, warnings = self._select(drafts, sufficiency)
        warnings.extend(dict.fromkeys(warning for item in drafts for warning in item.context_warnings))
        if duplicate_count:
            warnings.append(f"near_duplicate_evidence_removed:{duplicate_count}")
        warnings = list(dict.fromkeys(warnings))
        if len(warnings) > 30:
            warnings = [*warnings[:29], f"context_warnings_truncated:{len(warnings) - 29}"]
        selected_coverage = self._selected_coverage(selected)
        missing_subqueries = [
            item
            for item in sufficiency.required_subquery_ids
            if item not in selected_coverage
        ]
        missing_entities = [
            item
            for item in sufficiency.required_entity_ids
            if item not in selected_coverage
        ]
        if not selected:
            sufficiency = EvidenceSufficiency(
                status=EvidenceSufficiencyStatus.INSUFFICIENT,
                required_subquery_ids=sufficiency.required_subquery_ids,
                covered_subquery_ids=sufficiency.covered_subquery_ids,
                missing_subquery_ids=sufficiency.missing_subquery_ids,
                required_entity_ids=sufficiency.required_entity_ids,
                covered_entity_ids=sufficiency.covered_entity_ids,
                missing_entity_ids=sufficiency.missing_entity_ids,
                reasons=list(dict.fromkeys([*sufficiency.reasons, "context_pack_empty"])),
            )
        elif missing_subqueries or missing_entities:
            sufficiency = EvidenceSufficiency(
                status=EvidenceSufficiencyStatus.INSUFFICIENT,
                required_subquery_ids=sufficiency.required_subquery_ids,
                covered_subquery_ids=sufficiency.covered_subquery_ids,
                missing_subquery_ids=missing_subqueries,
                required_entity_ids=sufficiency.required_entity_ids,
                covered_entity_ids=sufficiency.covered_entity_ids,
                missing_entity_ids=missing_entities,
                reasons=list(dict.fromkeys([*sufficiency.reasons, "selected_coverage_missing"])),
            )
        return ContextPack(
            context_version=self.config.context_version,
            plan_id=plan.plan_id,
            query=plan.standalone_query,
            sufficiency=sufficiency,
            items=[self._to_evidence(item, rank) for rank, item in enumerate(selected, 1)],
            token_count=sum(item.token_count for item in selected),
            token_budget=self.config.selection.token_budget,
            warnings=warnings,
        )

    def _empty_pack(
        self,
        plan: SearchPlan,
        sufficiency: EvidenceSufficiency,
    ) -> ContextPack:
        return ContextPack(
            context_version=self.config.context_version,
            plan_id=plan.plan_id,
            query=plan.standalone_query,
            sufficiency=sufficiency,
            token_count=0,
            token_budget=self.config.selection.token_budget,
        )

    def _extract_drafts(
        self,
        query: str,
        result: RetrievalResult | RerankResult,
    ) -> list[_EvidenceDraft]:
        rerank_by_parent = (
            {item.candidate.parent_id: item for item in result.candidates}
            if isinstance(result, RerankResult)
            else {}
        )
        drafts = []
        for source_rank, candidate in enumerate(_ranked_candidates(result), start=1):
            contexts = _source_contexts(candidate)
            context_warnings = []
            raw_contexts = candidate.metadata.get("relation_source_contexts", [])
            if not isinstance(raw_contexts, list) or len(raw_contexts) != len(contexts):
                context_warnings.append(f"source_context_invalid:{candidate.primary_child_id}")
            exact_text = _truncate_tokens(
                candidate.primary_child_content.strip(),
                self.config.compression.max_evidence_tokens,
                self.token_counter,
            )
            context = _compressed_parent(
                candidate,
                query,
                exact_text,
                neighbor_window=self.config.compression.neighbor_window,
            )
            context = _truncate_tokens(
                context,
                self.config.compression.max_item_tokens,
                self.token_counter,
                required_prefix=exact_text,
            )
            if contexts and (len(candidate.primary_child_content) > 8_000 or any(
                context.count(item.anchor.exact_span) != 1 for item in contexts
            )):
                contexts = ()
                context_warnings.append(f"source_context_anchor_omitted:{candidate.primary_child_id}")
            evidence = candidate.channel_evidence
            reranked = rerank_by_parent.get(candidate.parent_id)
            # Supplement text is budgeted. Never truncate its offsets or omit
            # the question/speaker while keeping only a convenient answer.
            supplement_tokens = sum(self.token_counter.count(text) for text in {
                item.content for item in contexts if item.content not in context
            })
            if self.token_counter.count(context) + supplement_tokens > self.config.compression.max_item_tokens:
                contexts = ()
                supplement_tokens = 0
                context_warnings.append(f"source_context_budget_omitted:{candidate.primary_child_id}")
            drafts.append(
                _EvidenceDraft(
                    source_rank=source_rank,
                    candidate=candidate,
                    exact_text=exact_text,
                    context=context,
                    token_count=max(self.token_counter.count(context), 1) + supplement_tokens,
                    subquery_ids=tuple(
                        dict.fromkeys(item.subquery_id for item in evidence)
                    ),
                    retrieval_legs=tuple(
                        dict.fromkeys(item.retrieval_leg for item in evidence)
                    ),
                    rerank_score=(reranked.rerank_score if reranked else None),
                    relation_source_contexts=contexts,
                    context_warnings=tuple(context_warnings),
                )
            )
        return drafts

    def _deduplicate(
        self,
        drafts: Sequence[_EvidenceDraft],
    ) -> tuple[list[_EvidenceDraft], int]:
        selected: list[_EvidenceDraft] = []
        removed = 0
        for draft in drafts:
            if any(
                _similarity(draft.exact_text, item.exact_text)
                >= self.config.selection.near_duplicate_threshold
                for item in selected
            ):
                removed += 1
                continue
            selected.append(draft)
        return selected, removed

    def _select(
        self,
        drafts: Sequence[_EvidenceDraft],
        sufficiency: EvidenceSufficiency,
    ) -> tuple[list[_EvidenceDraft], list[str]]:
        required = [
            *sufficiency.required_subquery_ids,
            *sufficiency.required_entity_ids,
        ]
        ordered: list[_EvidenceDraft] = []
        for requirement in required:
            match = next(
                (
                    item
                    for item in drafts
                    if item not in ordered
                    and (
                        requirement in item.subquery_ids
                        or requirement in item.candidate.entity_ids
                    )
                ),
                None,
            )
            if match is not None:
                ordered.append(match)
        ordered.extend(item for item in drafts if item not in ordered)

        selected: list[_EvidenceDraft] = []
        source_counts: Counter[str] = Counter()
        token_count = 0
        warnings: list[str] = []
        for item in ordered:
            if len(selected) >= self.config.selection.max_items:
                break
            if source_counts[item.candidate.source_id] >= self.config.selection.source_cap:
                warnings.append("context_source_cap_applied")
                continue
            if token_count + item.token_count > self.config.selection.token_budget:
                warnings.append("context_token_budget_applied")
                continue
            selected.append(item)
            source_counts[item.candidate.source_id] += 1
            token_count += item.token_count
        return selected, list(dict.fromkeys(warnings))

    @staticmethod
    def _selected_coverage(selected: Sequence[_EvidenceDraft]) -> set[str]:
        return {
            value
            for item in selected
            for value in (*item.subquery_ids, *item.candidate.entity_ids)
        }

    @staticmethod
    def _to_evidence(item: _EvidenceDraft, pack_rank: int) -> ContextEvidence:
        candidate = item.candidate
        return ContextEvidence(
            pack_rank=pack_rank,
            source_rank=item.source_rank,
            parent_id=candidate.parent_id,
            child_id=candidate.primary_child_id,
            doc_id=candidate.doc_id,
            title=candidate.title,
            exact_text=item.exact_text,
            context=item.context,
            token_count=item.token_count,
            source_id=candidate.source_id,
            source_name=candidate.source_name,
            source_url=candidate.source_url,
            subquery_ids=list(item.subquery_ids),
            retrieval_legs=list(item.retrieval_legs),
            entity_ids=candidate.entity_ids,
            rerank_score=item.rerank_score,
            source_evidence_content=candidate.primary_child_content if len(candidate.primary_child_content) <= 8_000 else None,
            relation_source_contexts=list(item.relation_source_contexts),
            document_metadata=candidate.metadata.get("document_metadata", {}),
            source_provenance={
                key: candidate.metadata[key]
                for key in (
                    "source_locator", "source_content_sha256", "parent_content_sha256",
                    "source_response_sha256", "source_release_id", "knowledge_input_hash",
                    "index_input_hash", "source_published_at",
                )
                if key in candidate.metadata
            },
        )


def _ranked_candidates(
    result: RetrievalResult | RerankResult,
) -> list[RetrievalCandidate]:
    if isinstance(result, RerankResult):
        return [item.candidate for item in result.candidates]
    return result.candidates


def _unavailable(result: RetrievalResult | RerankResult) -> bool:
    if isinstance(result, RerankResult):
        return result.status is RerankStatus.UNAVAILABLE
    return result.status is RetrievalStatus.UNAVAILABLE


def _compressed_parent(
    candidate: RetrievalCandidate,
    query: str,
    exact_text: str,
    *,
    neighbor_window: int,
) -> str:
    parent = candidate.parent_context.content.strip()
    if not parent:
        return exact_text
    paragraphs = [item.strip() for item in re.split(r"\n\s*\n", parent) if item.strip()]
    matched_index = next(
        (index for index, paragraph in enumerate(paragraphs) if exact_text in paragraph),
        None,
    )
    if matched_index is None:
        query_terms = _terms(query)
        matched_index = max(
            range(len(paragraphs)),
            key=lambda index: len(query_terms.intersection(_terms(paragraphs[index]))),
        )
    start = max(0, matched_index - neighbor_window)
    end = min(len(paragraphs), matched_index + neighbor_window + 1)
    parts = [
        candidate.parent_context.breadcrumb.strip(),
        candidate.primary_child_breadcrumb.strip(),
        *paragraphs[start:end],
    ]
    if exact_text not in "\n\n".join(parts):
        parts.insert(2, exact_text)
    return "\n\n".join(dict.fromkeys(item for item in parts if item))


def _source_contexts(candidate: RetrievalCandidate) -> tuple[RelationSourceContext, ...]:
    payloads = candidate.metadata.get("relation_source_contexts", [])
    if not isinstance(payloads, list) or len(payloads) > 8:
        return ()
    document = SimpleNamespace(
        id=candidate.parent_id, title=candidate.title, content=candidate.primary_child_content,
        source=candidate.source_name, url=candidate.source_url,
        metadata={"source_id": candidate.source_id, "chunk_id": candidate.primary_child_id},
    )
    claims = {item.get("claim_id"): item for item in candidate.supported_claims}
    result, seen = [], set()
    for payload in payloads:
        try:
            context = RelationSourceContext.model_validate(payload)
        except ValidationError:
            continue
        claim = claims.get(context.claim_id)
        if context.claim_id in seen:
            return ()
        if (claim is None
                or not context.bound_to(document, context.claim_id, claim.get("evidence_span", ""))):
            continue
        seen.add(context.claim_id)
        result.append(context)
    return tuple(result)


def _truncate_tokens(
    text: str,
    limit: int,
    counter: TokenCounter,
    *,
    required_prefix: str | None = None,
) -> str:
    value = text.strip()
    if required_prefix is not None and required_prefix not in value:
        value = f"{required_prefix}\n\n{value}" if value else required_prefix
    if counter.count(value) <= limit:
        return value
    low, high = 1, len(value)
    while low < high:
        middle = (low + high + 1) // 2
        if counter.count(value[:middle]) <= limit:
            low = middle
        else:
            high = middle - 1
    truncated = value[:low].rstrip()
    if required_prefix is not None and required_prefix not in truncated:
        if counter.count(required_prefix) > limit:
            return _truncate_tokens(required_prefix, limit, counter)
        truncated = required_prefix
    return truncated


def _terms(text: str) -> set[str]:
    normalized = "".join(text.casefold().split())
    latin = set(re.findall(r"[a-z0-9]+", normalized))
    cjk = "".join(re.findall(r"[\u3400-\u9fff]", normalized))
    return latin | {cjk[index : index + 2] for index in range(max(0, len(cjk) - 1))}


def _similarity(left: str, right: str) -> float:
    if "".join(left.casefold().split()) == "".join(right.casefold().split()):
        return 1.0
    left_terms = _terms(left)
    right_terms = _terms(right)
    # Short evidence spans share many generic CJK bigrams; only exact duplicates
    # are safe to collapse until enough lexical signal is present.
    if min(len(left_terms), len(right_terms)) < 12:
        return 0.0
    if not left_terms or not right_terms:
        return 0.0
    return len(left_terms & right_terms) / len(left_terms | right_terms)
