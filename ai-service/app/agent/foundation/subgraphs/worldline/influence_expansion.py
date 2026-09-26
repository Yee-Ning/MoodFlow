"""Evidence-first, direction-checked Influence relations for WorldLine."""

from __future__ import annotations

from collections import Counter
from enum import StrEnum
from hashlib import sha256
import json
import logging
import re
import unicodedata

from pydantic import BaseModel, ConfigDict, Field, ValidationError
from app.rag.source_context import relation_provenance

from app.agent.foundation.subgraphs.worldline.conflict_semantics import (
    aggregation_trace,
    balanced_evidence,
    named_in_text,
    preserve_conflict_descriptions,
)
from app.agent.foundation.subgraphs.worldline.era_expansion import (
    _SupportedClaim,
    _knowledge_entity_map,
    _metadata_string,
)
from app.agent.foundation.subgraphs.worldline.source_context import source_context
from app.agent.foundation.subgraphs.worldline.merge import (
    WorldLineMergeError,
    merge_worldline_graph,
)
from app.agent.foundation.subgraphs.worldline.models import (
    EraEntityReference,
    EvidenceSource,
    EvidenceType,
    InfluenceDirection,
    InfluenceEvidenceStance,
    InfluenceEvidenceStrength,
    InfluenceRelation,
    InfluenceRelationEvidence,
    InfluenceRelationType,
    InfluenceScope,
    MusicKnowledgeDocument,
    WorldLineEdge,
    WorldLineGraph,
    WorldLineNode,
    WorldLineNodeType,
    WorldLineRelation,
)
from app.rag.query_understanding.models import SearchPlan


INFLUENCE_EXPANSION_VERSION = "v1"
INFLUENCE_VALIDATOR_VERSION = "v1"
INFLUENCE_ACCEPTANCE_THRESHOLD = 0.85
MAX_INFLUENCE_EVIDENCE = 8
LOGGER = logging.getLogger(__name__)

_INFLUENCE_QUERY_RE = re.compile(
    r"\binfluenc(?:e|es|ed|ing)\b|\binspir(?:e|es|ed|ation)\b|"
    r"影响|影響|启发|啟發|风格来源|風格來源|"
    r"塑造.{0,8}(?:声音|聲音|风格|風格)|"
    r"(?:shaped|shape).{0,12}(?:sound|style)",
    re.IGNORECASE,
)
_QUOTED_ENTITY_RE = re.compile(r"[《\"“'](?:[^》\"”']{1,300})[》\"”']")
_NEGATION_RE = re.compile(
    r"\b(?:not|never|denied|deny|didn't|wasn't|isn't)\b|"
    r"\bdirei\s+di\s+no\b|"
    r"没有|沒有|并未|並未|并不|並不|否认|否認|未受|不受|不是",
    re.IGNORECASE,
)
_SPECULATION_RE = re.compile(
    r"\b(?:may|might|possibly|perhaps|probably|seemingly|speculated)\b|"
    r"可能|或许|或許|似乎|据推测|據推測",
    re.IGNORECASE,
)
_CRITICAL_RE = re.compile(
    r"\b(?:critics?|reviewers?|scholars?|researchers?)\b|"
    r"评论家|評論家|研究者|学者|學者",
    re.IGNORECASE,
)
_SELF_REPORT_RE = re.compile(
    r"\b(?:said|stated|credited|acknowledged|cited)\b|"
    r"自述|表示|承认|承認|称|稱|提到|视为|視為",
    re.IGNORECASE,
)
_SCOPE_PATTERNS = {
    InfluenceScope.SOUND: re.compile(r"\bsound\b|声音|聲音|音色", re.IGNORECASE),
    InfluenceScope.STYLE: re.compile(r"\bstyle\b|风格|風格|曲风|曲風", re.IGNORECASE),
    InfluenceScope.SONGWRITING: re.compile(r"songwriting|创作|創作|写歌|寫歌", re.IGNORECASE),
    InfluenceScope.PRODUCTION: re.compile(r"\bproduction\b|制作|製作", re.IGNORECASE),
    InfluenceScope.CAREER: re.compile(r"\bcareer\b|生涯|职业|職業", re.IGNORECASE),
}
_PREDICATES: dict[str, tuple[InfluenceRelationType, bool]] = {
    "influenced": (InfluenceRelationType.INFLUENCED, False),
    "influenced_by": (InfluenceRelationType.INFLUENCED, True),
    "inspired": (InfluenceRelationType.INSPIRED, False),
    "inspired_by": (InfluenceRelationType.INSPIRED, True),
    "shaped_style": (InfluenceRelationType.SHAPED_STYLE, False),
    "cited_as_influence": (InfluenceRelationType.CITED_AS_INFLUENCE, True),
}
_ALLOWED_ENTITY_TYPES = {
    WorldLineNodeType.ARTIST,
    WorldLineNodeType.SONG,
    WorldLineNodeType.ALBUM,
    WorldLineNodeType.GENRE,
}


class InfluenceCandidateStatus(StrEnum):
    ACCEPTED = "accepted"
    REJECTED = "rejected"


class InfluenceCandidate(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    source_entity: EraEntityReference
    target_entity: EraEntityReference
    relation_type: InfluenceRelationType
    direction: InfluenceDirection = InfluenceDirection.INFLUENCER_TO_INFLUENCED
    influence_scope: InfluenceScope | None = None
    scope_mentions: list[InfluenceScope] = Field(default_factory=list, max_length=5)
    claim_confidence: float = Field(ge=0, le=1)
    confidence: float = Field(ge=0, le=1)
    uncertain: bool
    evidence: InfluenceRelationEvidence
    evidence_source: EvidenceSource
    claim_subject_entity_id: str
    claim_object_entity_id: str
    claim_predicate: str


class InfluenceDecision(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    candidate: InfluenceCandidate
    status: InfluenceCandidateStatus
    rejection_reasons: list[str] = Field(default_factory=list, max_length=20)


class InfluenceExpansionTrace(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    requested: bool
    candidate_count: int = Field(ge=0)
    accepted_count: int = Field(ge=0)
    rejected_count: int = Field(ge=0)
    rejection_reasons: dict[str, int] = Field(default_factory=dict)
    evidence_count: int = Field(ge=0)
    direction_conflict_count: int = Field(ge=0)
    scope_conflict_count: int = Field(ge=0)
    attribution_conflict_count: int = Field(ge=0)
    aggregation_trace: list[dict] = Field(default_factory=list)
    validator_version: str = INFLUENCE_VALIDATOR_VERSION


class InfluenceExpansionResult(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    graph: WorldLineGraph
    sources: list[EvidenceSource] = Field(max_length=30)
    relations: list[InfluenceRelation] = Field(default_factory=list, max_length=20)
    rejected: list[InfluenceDecision] = Field(default_factory=list)
    trace: InfluenceExpansionTrace


def is_explicit_influence_query(query: str) -> bool:
    """Words inside a quoted song/album title do not activate this layer."""

    return _INFLUENCE_QUERY_RE.search(_QUOTED_ENTITY_RE.sub("", query)) is not None


def _normalized_span(value: str) -> str:
    return " ".join(unicodedata.normalize("NFKC", value).split()).casefold()


def _scope_from_span(span: str) -> tuple[InfluenceScope | None, list[InfluenceScope]]:
    matches = [scope for scope, pattern in _SCOPE_PATTERNS.items() if pattern.search(span)]
    return (matches[0] if len(matches) == 1 else None), matches


def _strength_from_span(
    span: str, relation_type: InfluenceRelationType,
) -> InfluenceEvidenceStrength:
    if _SPECULATION_RE.search(span):
        return InfluenceEvidenceStrength.SPECULATIVE
    if relation_type is InfluenceRelationType.CITED_AS_INFLUENCE or _SELF_REPORT_RE.search(span):
        return InfluenceEvidenceStrength.SELF_REPORTED
    if _CRITICAL_RE.search(span):
        return InfluenceEvidenceStrength.CRITICAL_ATTRIBUTION
    return InfluenceEvidenceStrength.HISTORICAL_ATTRIBUTION


def _direction_patterns(
    source_name: str, target_name: str, relation_type: InfluenceRelationType,
) -> list[str]:
    """The graph source is always the influencer, independent of sentence voice."""

    def entity_pattern(name: str) -> str:
        normalized = _normalized_span(name)
        escaped = re.escape(normalized)
        return rf"(?<!\w){escaped}(?!\w)" if normalized.isascii() else escaped

    source = entity_pattern(source_name)
    target = entity_pattern(target_name)
    gap = r"[^。.!?！？\n]{0,45}?"
    if relation_type is InfluenceRelationType.INFLUENCED:
        return [
            rf"{source}\s+(?:\w+ly\s+)?influenc(?:ed|es?)\s+{target}",
            rf"{source}\s+did\s+not\s+influence\s+{target}",
            rf"{source}{gap}(?:影响(?:了|着)?|影響(?:了|著)?){gap}{target}",
            rf"{target}{gap}(?:was|were|is|has\s+been)\s+(?:not\s+)?influenced\s+by{gap}{source}",
            rf"{target}{gap}(?:受|受到){gap}{source}{gap}(?:影响|影響)",
            rf"{source}{gap}influence\s+on{gap}{target}",
            rf"{source}{gap}对{target}[^。.!?！？\n]{{0,20}}(?:影响|影響)",
        ]
    if relation_type is InfluenceRelationType.INSPIRED:
        return [
            rf"{source}\s+(?:\w+ly\s+)?inspir(?:ed|es?)\s+{target}",
            rf"{source}{gap}(?:启发(?:了)?|啟發(?:了)?){gap}{target}",
            rf"{target}{gap}(?:was|were|is|has\s+been)\s+(?:not\s+)?inspired\s+by{gap}{source}",
            rf"{target}{gap}(?:受|受到){gap}{source}{gap}(?:启发|啟發)",
        ]
    if relation_type is InfluenceRelationType.SHAPED_STYLE:
        return [
            rf"{source}{gap}(?:shap(?:ed|es?)|塑造(?:了)?){gap}{target}{gap}(?:sound|style|声音|聲音|风格|風格)",
            rf"{target}{gap}(?:sound|style|声音|聲音|风格|風格){gap}(?:was|is|受到|受){gap}(?:shaped\s+by|塑造){gap}{source}",
        ]
    return [
        rf"{target}{gap}(?:cited|credited){gap}{source}{gap}(?:as\s+(?:an?\s+)?)?influence",
        rf"{target}{gap}(?:将|把){gap}{source}{gap}(?:视为|視為|称为|稱為){gap}(?:影响|影響|启发|啟發)",
    ]


def _direction_status(candidate: InfluenceCandidate) -> str:
    span = _normalized_span(candidate.evidence.exact_span)
    if (
        _normalized_span(candidate.source_entity.canonical_name) not in span
        or _normalized_span(candidate.target_entity.canonical_name) not in span
    ):
        return "missing"
    forward = any(re.search(pattern, span) for pattern in _direction_patterns(
        candidate.source_entity.canonical_name,
        candidate.target_entity.canonical_name,
        candidate.relation_type,
    ))
    reverse = any(re.search(pattern, span) for pattern in _direction_patterns(
        candidate.target_entity.canonical_name,
        candidate.source_entity.canonical_name,
        candidate.relation_type,
    ))
    if forward and reverse:
        return "ambiguous"
    if forward:
        return "forward"
    if reverse:
        return "reversed"
    return "missing"


def _relation_key(candidate: InfluenceCandidate | InfluenceRelation) -> tuple[str, str, InfluenceRelationType]:
    return (
        candidate.source_entity.node_id,
        candidate.target_entity.node_id,
        candidate.relation_type,
    )


def _context_direction(
    candidate: InfluenceCandidate, document: MusicKnowledgeDocument,
    graph_nodes: dict[str, WorldLineNode],
) -> bool:
    """Resolve list/quotation roles only in available source context.

    A verified claim alone cannot supply a missing grammatical subject or an
    omitted interview question. Context is bounded and must contain the span.
    """
    if candidate.relation_type is not InfluenceRelationType.INFLUENCED:
        return False
    exact = candidate.evidence.exact_span
    if document.content.count(exact) != 1:
        return False
    offset = document.content.index(exact)
    context = _normalized_span(document.content[max(0, offset - 800):offset + len(exact)])
    supplement = source_context(document, candidate.evidence.claim_id, exact)
    question_context = context
    if supplement is not None:
        segments = {segment.role: segment for segment in supplement.segments}
        if candidate.evidence.stance is InfluenceEvidenceStance.SUPPORT:
            sentence = segments.get("article_sentence")
            if sentence is None or exact not in sentence.exact_span:
                return False
            context = _normalized_span(sentence.exact_span)
        else:
            question, answer = segments.get("interview_question"), segments.get("interview_answer")
            binding = segments.get("speaker_binding")
            if (question is None or answer is None or binding is None
                    or answer.exact_span != exact or binding.exact_span != document.title
                    or binding.end > question.start or question.end > answer.start
                    or supplement.content[question.end:answer.start].strip()):
                return False
            question_context = _normalized_span(question.exact_span)
            context = _normalized_span(supplement.content[:answer.end])
    # A surname alias is admissible only when unique among runtime entities.
    aliases = [candidate.source_entity.canonical_name]
    surname = candidate.source_entity.canonical_name.split()[-1]
    if len(surname) >= 4 and sum(
        named_in_text(surname, node.name) for node in graph_nodes.values()
    ) == 1:
        aliases.append(surname)
    target = re.escape(_normalized_span(candidate.target_entity.canonical_name))
    actor_limit = 220 if supplement is not None else 100
    for alias in aliases:
        source = re.escape(_normalized_span(alias))
        if candidate.evidence.stance is InfluenceEvidenceStance.SUPPORT:
            # The target performs the borrowing/translation; the source is
            # inside its list of inputs, not merely adjacent in prose.
            attribution = re.search(
                rf"(?<!\w){target}(?!\w)(?P<actor_context>[^.!?\n]{{0,{actor_limit}}}"
                r"(?:[.]\s*(?:they|the\s+band)\s+)?)(?:tried\s+to\s+)?translate[^.!?\n]{0,220}"
                rf"(?<!\w){source}(?!\w)[^.!?\n]{{0,100}}"
                r"into[^.!?\n]{0,100}(?:music|electronica|sound)", context,
            )
            actor_unambiguous = bool(attribution) and not any(
                named_in_text(node.name, attribution.group("actor_context"))
                for node in graph_nodes.values() if node.id != candidate.target_entity.node_id
            ) and not named_in_text(alias, attribution.group("actor_context"))
            if actor_unambiguous and not _NEGATION_RE.search(context) and not _SPECULATION_RE.search(context):
                return True
        else:
            # Bind the respondent to the named interview subject, then require
            # an actual question about that subject's music before a denial.
            heading = _normalized_span(document.title)
            speaker = exact.split(":", 1)[0].strip()
            respondent_bound = bool(
                ":" in exact and re.match(rf"^{target}\s*:\s*.*(?:interview|intervista)", heading)
                and named_in_text(speaker, heading)
            ) or bool(re.search(
                rf"{re.escape(_normalized_span(speaker))}[^.!?\n]{{0,60}}"
                rf"(?:member\s+of|of\s+the\s+band)\s+{target}(?!\w)", context,
            ))
            question = re.search(
                rf"(?<!\w){source}(?!\w)[^.?!]{{0,100}}"
                r"(?:influence|influenz)[^.?!]{0,100}(?:your|voi|vi|vostra)[^.?!]{0,100}\?",
                question_context,
            )
            # Passive attribution followed by an elliptical follow-up question:
            # "influenced by X. Is that true for you?" Same adjacent Q/A only.
            if supplement is not None and question is None:
                question = re.search(
                    r"(?:influenced\s+by\s+|influenzati\s+dall['’](?:opera\s+di\s+)?)"
                    rf"{source}(?!\w)\s*[.]\s*"
                    r"(?:is\s+that\s+(?:also\s+)?true\s+for\s+you|è\s+così\s+anche\s+per\s+voi)\s*\?",
                    question_context,
                )
            if respondent_bound and question and _NEGATION_RE.search(exact):
                return True
    return False


class InfluenceCandidateExtractor:
    """Read only verified claims in the existing ContextPack documents."""

    def extract(
        self,
        search_plan: SearchPlan,
        graph: WorldLineGraph,
        documents: list[MusicKnowledgeDocument],
    ) -> list[InfluenceCandidate]:
        entity_map = _knowledge_entity_map(graph, search_plan)
        candidates: list[InfluenceCandidate] = []
        for document in documents:
            raw_claims = document.metadata.get("supported_claims")
            if not isinstance(raw_claims, list):
                continue
            parent_id = _metadata_string(document, "parent_id")
            child_id = _metadata_string(document, "chunk_id")
            context_version = _metadata_string(document, "context_version")
            if not all((parent_id, child_id, context_version)):
                continue
            for raw_claim in raw_claims:
                if not isinstance(raw_claim, dict):
                    continue
                try:
                    claim = _SupportedClaim.model_validate(raw_claim)
                except ValidationError:
                    continue
                candidate = self._candidate(
                    document, claim, entity_map,
                    parent_id=parent_id, child_id=child_id,
                    context_version=context_version,
                )
                if candidate is not None:
                    candidates.append(candidate)
        return candidates

    @staticmethod
    def _candidate(
        document: MusicKnowledgeDocument,
        claim: _SupportedClaim,
        entity_map: dict[str, EraEntityReference],
        *, parent_id: str, child_id: str, context_version: str,
    ) -> InfluenceCandidate | None:
        predicate = _PREDICATES.get(claim.predicate)
        if predicate is None or claim.object_entity_id is None or claim.object_value is not None:
            return None
        relation_type, reverse_claim = predicate
        subject = entity_map.get(claim.subject_entity_id)
        object_entity = entity_map.get(claim.object_entity_id)
        if subject is None or object_entity is None:
            return None
        source, target = (object_entity, subject) if reverse_claim else (subject, object_entity)
        raw_confidence = min(claim.extraction_confidence, claim.verification_confidence)
        strength = _strength_from_span(claim.evidence_span, relation_type)
        uncertain = strength is InfluenceEvidenceStrength.SPECULATIVE
        confidence = max(0.0, raw_confidence - (0.25 if uncertain else 0.0))
        scope, scope_mentions = _scope_from_span(claim.evidence_span)
        material = {
            "document_id": document.id,
            "claim_id": claim.claim_id,
            "chunk_id": child_id,
            "span": claim.evidence_span,
        }
        digest = sha256(json.dumps(
            material, ensure_ascii=False, sort_keys=True,
        ).encode("utf-8")).hexdigest()[:24]
        evidence_id = f"knowledge:influence:{digest}"
        negated = _NEGATION_RE.search(claim.evidence_span) is not None
        evidence = InfluenceRelationEvidence(
            evidence_id=evidence_id,
            source_id=evidence_id,
            claim_id=claim.claim_id,
            exact_span=claim.evidence_span,
            confidence=confidence,
            strength=strength,
            stance=(
                InfluenceEvidenceStance.CONTRADICTION
                if negated else InfluenceEvidenceStance.SUPPORT
            ),
        )
        evidence_source = EvidenceSource(
            id=evidence_id,
            source_type=EvidenceType.KNOWLEDGE,
            title=document.title,
            source=document.source,
            url=document.url,
            excerpt=claim.evidence_span[:2_000],
            metadata={
                "document_id": document.id,
                **relation_provenance(document, claim.claim_id, claim.evidence_span),
                "parent_id": parent_id,
                "child_id": child_id,
                "claim_id": claim.claim_id,
                "source_chunk_id": claim.source_chunk_id,
                "context_version": context_version,
                "retrieval_version": _metadata_string(document, "retrieval_version"),
                "influence_expansion_version": INFLUENCE_EXPANSION_VERSION,
                "validator_version": INFLUENCE_VALIDATOR_VERSION,
            },
        )
        return InfluenceCandidate(
            source_entity=source,
            target_entity=target,
            relation_type=relation_type,
            influence_scope=scope,
            scope_mentions=scope_mentions,
            claim_confidence=raw_confidence,
            confidence=confidence,
            uncertain=uncertain,
            evidence=evidence,
            evidence_source=evidence_source,
            claim_subject_entity_id=claim.subject_entity_id,
            claim_object_entity_id=claim.object_entity_id,
            claim_predicate=claim.predicate,
        )


class InfluenceValidator:
    def __init__(self, *, threshold: float = INFLUENCE_ACCEPTANCE_THRESHOLD) -> None:
        self.threshold = threshold

    def validate(
        self,
        graph: WorldLineGraph,
        documents: list[MusicKnowledgeDocument],
        candidates: list[InfluenceCandidate],
    ) -> tuple[list[InfluenceRelation], list[InfluenceDecision]]:
        document_counts = Counter(document.id for document in documents)
        document_by_id = {document.id: document for document in documents}
        graph_nodes = {node.id: node for node in graph.nodes}
        decisions: list[InfluenceDecision] = []
        supporting: list[InfluenceCandidate] = []
        contradictions: list[InfluenceCandidate] = []
        potential_conflicts = {
            _relation_key(item) for item in candidates
            if item.evidence.stance is InfluenceEvidenceStance.CONTRADICTION
        } & {
            _relation_key(item) for item in candidates
            if item.evidence.stance is InfluenceEvidenceStance.SUPPORT
        }
        for candidate in candidates:
            reasons = self._rejection_reasons(
                candidate, graph_nodes, document_counts, document_by_id,
            )
            document = document_by_id.get(candidate.evidence_source.metadata.get("document_id"))
            if (
                _relation_key(candidate) in potential_conflicts
                and reasons in (["direction_missing"], ["direction_missing", "negated_influence"])
                and document is not None and _context_direction(candidate, document, graph_nodes)
            ):
                reasons = [reason for reason in reasons if reason != "direction_missing"]
                candidate = candidate.model_copy(update={
                    "evidence_source": candidate.evidence_source.model_copy(update={
                        "metadata": {
                            **candidate.evidence_source.metadata,
                            "context_direction_basis": "paired_attribution_source_context",
                            "context_document_sha256": sha256(document.content.encode("utf-8")).hexdigest(),
                            "exact_span_start": document.content.index(candidate.evidence.exact_span),
                            "exact_span_end": document.content.index(candidate.evidence.exact_span)
                            + len(candidate.evidence.exact_span),
                        },
                    }),
                })
                supplement = source_context(document, candidate.evidence.claim_id, candidate.evidence.exact_span)
                if supplement is not None:
                    candidate = candidate.model_copy(update={
                        "evidence_source": candidate.evidence_source.model_copy(update={
                            "metadata": {**candidate.evidence_source.metadata,
                                         "relation_source_context": supplement.model_dump(mode="json")},
                        }),
                    })
            status = (
                InfluenceCandidateStatus.REJECTED if reasons
                else InfluenceCandidateStatus.ACCEPTED
            )
            decisions.append(InfluenceDecision(
                candidate=candidate, status=status, rejection_reasons=reasons,
            ))
            if not reasons:
                supporting.append(candidate)
            elif reasons == ["negated_influence"]:
                contradictions.append(candidate)
        return self._aggregate(supporting, contradictions), decisions

    def _rejection_reasons(
        self,
        candidate: InfluenceCandidate,
        graph_nodes: dict[str, WorldLineNode],
        document_counts: Counter[str],
        document_by_id: dict[str, MusicKnowledgeDocument],
    ) -> list[str]:
        reasons: list[str] = []
        source = graph_nodes.get(candidate.source_entity.node_id)
        target = graph_nodes.get(candidate.target_entity.node_id)
        if source is None or target is None:
            reasons.append("entity_missing")
        elif (
            source.type not in _ALLOWED_ENTITY_TYPES
            or target.type not in _ALLOWED_ENTITY_TYPES
            or source.type is not candidate.source_entity.node_type
            or target.type is not candidate.target_entity.node_type
            or source.id == target.id
        ):
            reasons.append("entity_type_or_identity_invalid")
        document_id = candidate.evidence_source.metadata.get("document_id")
        document = (
            document_by_id.get(document_id)
            if isinstance(document_id, str) else None
        )
        if document is None or document_counts[document_id] != 1:
            reasons.append("evidence_document_missing")
        else:
            if document.metadata.get("sufficiency_status") != "sufficient":
                reasons.append("insufficient_context")
            if candidate.evidence.exact_span not in document.content:
                reasons.append("evidence_span_missing")
            if len(candidate.evidence.exact_span) > 2_000:
                reasons.append("evidence_span_too_long")
            if document.metadata.get("parent_id") != candidate.evidence_source.metadata.get("parent_id"):
                reasons.append("evidence_parent_mismatch")
            if document.metadata.get("chunk_id") != candidate.evidence_source.metadata.get("child_id"):
                reasons.append("evidence_chunk_mismatch")
            if not self._claim_is_present(document, candidate):
                reasons.append("claim_not_in_document")
        if candidate.claim_confidence < self.threshold:
            reasons.append("confidence_below_threshold")
        if candidate.uncertain:
            reasons.append("speculative_influence")
        direction = _direction_status(candidate)
        if direction != "forward":
            reasons.append(f"direction_{direction}")
        if candidate.relation_type is InfluenceRelationType.SHAPED_STYLE and not (
            InfluenceScope.SOUND in candidate.scope_mentions
            or InfluenceScope.STYLE in candidate.scope_mentions
        ):
            reasons.append("style_or_sound_scope_missing")
        if candidate.evidence.stance is InfluenceEvidenceStance.CONTRADICTION:
            reasons.append("negated_influence")
        return list(dict.fromkeys(reasons))

    @staticmethod
    def _claim_is_present(
        document: MusicKnowledgeDocument,
        candidate: InfluenceCandidate,
    ) -> bool:
        raw_claims = document.metadata.get("supported_claims")
        if not isinstance(raw_claims, list):
            return False
        for raw in raw_claims:
            if not isinstance(raw, dict):
                continue
            try:
                claim = _SupportedClaim.model_validate(raw)
            except ValidationError:
                continue
            if (
                claim.claim_id == candidate.evidence.claim_id
                and claim.subject_entity_id == candidate.claim_subject_entity_id
                and claim.object_entity_id == candidate.claim_object_entity_id
                and claim.predicate == candidate.claim_predicate
                and claim.object_value is None
                and claim.source_chunk_id
                == candidate.evidence_source.metadata.get("source_chunk_id")
                and claim.evidence_span == candidate.evidence.exact_span
                and min(claim.extraction_confidence, claim.verification_confidence)
                == candidate.claim_confidence
            ):
                return True
        return False

    @staticmethod
    def _aggregate(
        supporting: list[InfluenceCandidate],
        contradictions: list[InfluenceCandidate],
    ) -> list[InfluenceRelation]:
        grouped: dict[tuple[str, str, InfluenceRelationType], list[InfluenceCandidate]] = {}
        for candidate in supporting:
            grouped.setdefault(_relation_key(candidate), []).append(candidate)
        negative_by_key: dict[tuple[str, str, InfluenceRelationType], list[InfluenceCandidate]] = {}
        for candidate in contradictions:
            negative_by_key.setdefault(_relation_key(candidate), []).append(candidate)
        relations: list[InfluenceRelation] = []
        for key, items in grouped.items():
            first = items[0]
            evidence_by_id = {
                candidate.evidence.evidence_id: candidate.evidence
                for candidate in [*items, *negative_by_key.get(key, [])]
            }
            overflow = max(0, len(evidence_by_id) - MAX_INFLUENCE_EVIDENCE)
            kept_ids = {item.evidence.evidence_id for item in balanced_evidence(
                [*items, *negative_by_key.get(key, [])], limit=MAX_INFLUENCE_EVIDENCE,
                stance=lambda item: item.evidence.stance,
            )}
            evidence = [
                item for evidence_id, item in evidence_by_id.items()
                if evidence_id in kept_ids
            ]
            kept_positive = [
                item for item in items if item.evidence.evidence_id in kept_ids
            ]
            scopes = {item.influence_scope for item in kept_positive if item.influence_scope}
            scope_conflict = len(scopes) > 1
            scope = next(iter(scopes)) if len(scopes) == 1 else None
            attribution_conflict = any(
                item.stance is InfluenceEvidenceStance.CONTRADICTION
                for item in evidence
            )
            relations.append(InfluenceRelation(
                source_entity=first.source_entity,
                target_entity=first.target_entity,
                relation_type=first.relation_type,
                influence_scope=scope,
                confidence=max(
                    0.0,
                    min(item.confidence for item in kept_positive)
                    - (0.10 if scope_conflict else 0.0)
                    - (0.15 if attribution_conflict else 0.0),
                ),
                evidence=evidence,
                metadata={
                    "influence_expansion_version": INFLUENCE_EXPANSION_VERSION,
                    "validator_version": INFLUENCE_VALIDATOR_VERSION,
                    "direction_conflict": False,
                    "scope_conflict": scope_conflict,
                    "attribution_conflict": attribution_conflict,
                    "conflict_flags": ["attribution_conflict"] if attribution_conflict else [],
                    "evidence_overflow_count": overflow,
                    "scope_assertions": [
                        {"evidence_id": item.evidence.evidence_id,
                         "scope": item.influence_scope.value if item.influence_scope else None,
                         "scope_mentions": [scope.value for scope in item.scope_mentions]}
                        for item in kept_positive
                    ],
                    "provenance": [
                        {"evidence_id": item.evidence_id,
                         "strength": item.strength.value,
                         "stance": item.stance.value}
                        for item in evidence
                    ],
                },
            ))
        return _mark_direction_conflicts(relations)


def _mark_direction_conflicts(relations: list[InfluenceRelation]) -> list[InfluenceRelation]:
    pairs = {(item.source_entity.node_id, item.target_entity.node_id) for item in relations}
    output: list[InfluenceRelation] = []
    for relation in relations:
        reverse = (
            relation.target_entity.node_id,
            relation.source_entity.node_id,
        )
        if reverse in pairs:
            output.append(relation.model_copy(update={
                "confidence": max(0.0, relation.confidence - 0.15),
                "metadata": {**relation.metadata, "direction_conflict": True},
            }))
        else:
            output.append(relation)
    return output


class InfluenceGraphProjector:
    def project(
        self,
        graph: WorldLineGraph,
        sources: list[EvidenceSource],
        relations: list[InfluenceRelation],
        candidates: list[InfluenceCandidate],
    ) -> tuple[WorldLineGraph, list[EvidenceSource]]:
        if not relations:
            return graph, sources
        graph_nodes = {node.id: node for node in graph.nodes}
        delta_nodes: dict[str, WorldLineNode] = {graph.root: graph_nodes[graph.root]}
        delta_edges: list[WorldLineEdge] = []
        source_by_id = {
            candidate.evidence.evidence_id: candidate.evidence_source
            for candidate in candidates
        }
        delta_sources: dict[str, EvidenceSource] = {}
        for relation in relations:
            for endpoint in (relation.source_entity, relation.target_entity):
                node = graph_nodes.get(endpoint.node_id)
                if node is None or node.type is not endpoint.node_type:
                    raise WorldLineMergeError("missing_relation_endpoint")
                delta_nodes.setdefault(node.id, node)
            evidence_ids = [item.evidence_id for item in relation.evidence]
            for evidence in relation.evidence:
                source = source_by_id.get(evidence.evidence_id)
                if source is None:
                    raise WorldLineMergeError("missing_edge_evidence")
                delta_sources[evidence.evidence_id] = source.model_copy(update={
                    "metadata": {
                        **source.metadata,
                        "influence_stance": evidence.stance.value,
                        "influence_strength": evidence.strength.value,
                    },
                })
            delta_edges.append(WorldLineEdge(
                source=relation.source_entity.node_id,
                target=relation.target_entity.node_id,
                relation=WorldLineRelation(relation.relation_type.value),
                description=(
                    f"{relation.source_entity.canonical_name} → "
                    f"{relation.target_entity.canonical_name}："
                    f"{relation.relation_type.value}（影响归因存在分歧；保留双方证据）"
                    if relation.metadata.get("attribution_conflict") else
                    f"{relation.source_entity.canonical_name} → "
                    f"{relation.target_entity.canonical_name}："
                    f"{relation.relation_type.value}（已验证方向与证据）"
                ),
                evidence_ids=evidence_ids,
            ))
        delta = WorldLineGraph(
            root=graph.root,
            nodes=list(delta_nodes.values()),
            edges=delta_edges,
        )
        merged_graph, merged_sources = merge_worldline_graph(
            graph, delta, sources, list(delta_sources.values()),
            target_node_id=graph.root,
        )
        keys = {(item.source_entity.node_id, item.target_entity.node_id, WorldLineRelation(item.relation_type.value))
                for item in relations if item.metadata.get("attribution_conflict")}
        return preserve_conflict_descriptions(merged_graph, delta, keys), merged_sources


def merge_influence_relations(
    old: list[InfluenceRelation], new: list[InfluenceRelation],
) -> list[InfluenceRelation]:
    merged: dict[tuple[str, str, InfluenceRelationType], InfluenceRelation] = {}
    for relation in [*old, *new]:
        key = _relation_key(relation)
        previous = merged.get(key)
        if previous is None:
            merged[key] = relation
            continue
        if previous == relation:
            continue
        evidence = {item.evidence_id: item for item in previous.evidence}
        evidence.update({item.evidence_id: item for item in relation.evidence})
        if len(evidence) > MAX_INFLUENCE_EVIDENCE:
            raise WorldLineMergeError("capacity_reached")
        scopes = {
            scope for scope in (previous.influence_scope, relation.influence_scope)
            if scope is not None
        }
        scope_conflict = len(scopes) > 1
        attribution_conflict = any(
            item.stance is InfluenceEvidenceStance.CONTRADICTION
            for item in evidence.values()
        )
        merged[key] = previous.model_copy(update={
            "influence_scope": next(iter(scopes)) if len(scopes) == 1 else None,
            "confidence": max(
                0.0,
                min(previous.confidence, relation.confidence)
                - (0.10 if scope_conflict else 0.0)
                - (0.15 if attribution_conflict and not previous.metadata.get("attribution_conflict") else 0.0),
            ),
            "evidence": list(evidence.values()),
            "metadata": {
                **previous.metadata,
                "scope_conflict": scope_conflict
                or bool(previous.metadata.get("scope_conflict"))
                or bool(relation.metadata.get("scope_conflict")),
                "attribution_conflict": attribution_conflict,
                "conflict_flags": sorted(set(previous.metadata.get("conflict_flags", []))
                                         | set(relation.metadata.get("conflict_flags", []))
                                         | ({"attribution_conflict"} if attribution_conflict else set())),
                "scope_assertions": [
                    *{item["evidence_id"]: item for item in [
                        *previous.metadata.get("scope_assertions", []),
                        *relation.metadata.get("scope_assertions", []),
                    ]}.values(),
                ],
                "provenance": [
                    *{item["evidence_id"]: item for item in [
                        *previous.metadata.get("provenance", []),
                        *relation.metadata.get("provenance", []),
                    ]}.values(),
                ],
            },
        })
    if len(merged) > 20:
        raise WorldLineMergeError("capacity_reached")
    # The conflict flag is recomputed across checkpoint turns without applying
    # an extra confidence penalty to relations already marked as conflicting.
    relations = list(merged.values())
    pairs = {(item.source_entity.node_id, item.target_entity.node_id) for item in relations}
    return [
        item.model_copy(update={
            "confidence": (
                item.confidence if item.metadata.get("direction_conflict")
                else max(0.0, item.confidence - 0.15)
            ),
            "metadata": {**item.metadata, "direction_conflict": True},
        })
        if (item.target_entity.node_id, item.source_entity.node_id) in pairs
        else item
        for item in relations
    ]


def _fit_graph_capacity(
    graph: WorldLineGraph,
    sources: list[EvidenceSource],
    relations: list[InfluenceRelation],
) -> tuple[list[InfluenceRelation], int]:
    edge_evidence = {
        (edge.source, edge.target, edge.relation): set(edge.evidence_ids)
        for edge in graph.edges
    }
    source_ids = {source.id for source in sources}
    selected: list[InfluenceRelation] = []
    skipped = 0
    for relation in sorted(relations, key=lambda item: item.confidence, reverse=True):
        key = (
            relation.source_entity.node_id,
            relation.target_entity.node_id,
            WorldLineRelation(relation.relation_type.value),
        )
        evidence_ids = {item.source_id for item in relation.evidence}
        if (
            len(edge_evidence | {key: set()}) > 20
            or len(source_ids | evidence_ids) > 30
            or len(edge_evidence.get(key, set()) | evidence_ids) > 8
        ):
            skipped += 1
            continue
        selected.append(relation)
        source_ids.update(evidence_ids)
        edge_evidence.setdefault(key, set()).update(evidence_ids)
    return selected, skipped


class InfluenceExpansionService:
    def __init__(self, *, threshold: float = INFLUENCE_ACCEPTANCE_THRESHOLD) -> None:
        self.extractor = InfluenceCandidateExtractor()
        self.validator = InfluenceValidator(threshold=threshold)
        self.projector = InfluenceGraphProjector()

    def expand(
        self,
        query: str,
        search_plan: SearchPlan,
        graph: WorldLineGraph,
        sources: list[EvidenceSource],
        documents: list[MusicKnowledgeDocument],
    ) -> InfluenceExpansionResult:
        requested = is_explicit_influence_query(query)
        if not requested:
            return InfluenceExpansionResult(
                graph=graph, sources=sources,
                trace=InfluenceExpansionTrace(
                    requested=False, candidate_count=0, accepted_count=0,
                    rejected_count=0, evidence_count=0,
                    direction_conflict_count=0, scope_conflict_count=0,
                    attribution_conflict_count=0,
                ),
            )
        candidates = self.extractor.extract(search_plan, graph, documents)
        relations, decisions = self.validator.validate(graph, documents, candidates)
        relations, capacity_rejected = _fit_graph_capacity(graph, sources, relations)
        selected_ids = {
            evidence.evidence_id
            for relation in relations for evidence in relation.evidence
        }
        selected_candidates = [
            decision.candidate for decision in decisions
            if decision.candidate.evidence.evidence_id in selected_ids
        ]
        projected_graph, projected_sources = self.projector.project(
            graph, sources, relations, selected_candidates,
        )
        reasons = Counter(
            reason for decision in decisions for reason in decision.rejection_reasons
        )
        overflow = sum(
            int(relation.metadata.get("evidence_overflow_count", 0))
            for relation in relations
        )
        if overflow:
            reasons["evidence_capacity_reached"] += overflow
        if capacity_rejected:
            reasons["capacity_reached"] += capacity_rejected
        trace = InfluenceExpansionTrace(
            requested=True,
            candidate_count=len(candidates),
            accepted_count=len(relations),
            rejected_count=capacity_rejected + overflow + sum(
                decision.status is InfluenceCandidateStatus.REJECTED
                for decision in decisions
            ),
            rejection_reasons=dict(reasons),
            evidence_count=sum(len(relation.evidence) for relation in relations),
            direction_conflict_count=sum(
                bool(relation.metadata.get("direction_conflict")) for relation in relations
            ),
            scope_conflict_count=sum(
                bool(relation.metadata.get("scope_conflict")) for relation in relations
            ),
            attribution_conflict_count=sum(
                bool(relation.metadata.get("attribution_conflict")) for relation in relations
            ),
            aggregation_trace=aggregation_trace(decisions, relations),
        )
        LOGGER.info(
            "worldline.influence_expansion",
            extra={"influence_expansion_trace": trace.model_dump(mode="json")},
        )
        return InfluenceExpansionResult(
            graph=projected_graph,
            sources=projected_sources,
            relations=relations,
            rejected=[
                decision for decision in decisions
                if decision.status is InfluenceCandidateStatus.REJECTED
            ],
            trace=trace,
        )
