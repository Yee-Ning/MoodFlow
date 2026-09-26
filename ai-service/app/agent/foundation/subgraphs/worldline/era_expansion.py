"""Evidence-first Era Relation extraction, validation, and graph projection."""

from __future__ import annotations

from collections import Counter
from enum import StrEnum
from hashlib import sha256
import json
import logging
import re
import unicodedata

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from app.agent.foundation.subgraphs.worldline.conflict_semantics import (
    aggregation_trace,
    balanced_evidence,
    named_in_text,
    preserve_conflict_descriptions,
    stable_projected_source,
)
from app.agent.foundation.subgraphs.worldline.merge import (
    WorldLineMergeError,
    merge_worldline_graph,
)
from app.agent.foundation.subgraphs.worldline.models import (
    EraDescriptor,
    EraEntityReference,
    EraRelation,
    EraRelationEvidence,
    EraRelationType,
    EraTemporalPrecision,
    EvidenceSource,
    EvidenceType,
    MusicKnowledgeDocument,
    WorldLineEdge,
    WorldLineGraph,
    WorldLineNode,
    WorldLineNodeType,
    WorldLineRelation,
)
from app.rag.knowledge.claims.models import ClaimTime
from app.rag.query_understanding.models import QueryType, SearchPlan


ERA_EXPANSION_VERSION = "v1"
ERA_VALIDATOR_VERSION = "v1"
ERA_ACCEPTANCE_THRESHOLD = 0.85
MAX_ERA_EVIDENCE = 8
LOGGER = logging.getLogger(__name__)

_ERA_QUERY_RE = re.compile(
    r"时代|時代|时期|時期|年代|时间线|時間線|历程|歷程|"
    r"阶段|階段|早期|初期|突破期|巅峰期|巔峰期|晚期|"
    r"(?:18|19|20)\d{2}\s*年?"
)
_APPROXIMATE_TIME_RE = re.compile(
    r"约|約|大约|大約|前后|前後|左右|初|中期|末|早期|晚期|"
    r"around|about|circa|early|mid|late",
    re.IGNORECASE,
)
_RELEASE_CUE_RE = re.compile(r"发行|發行|推出|release", re.IGNORECASE)
_DEBUT_CUE_RE = re.compile(r"出道|首张|首張|debut", re.IGNORECASE)
_NEGATED_RELATION_RE = re.compile(
    r"不属于|不屬於|并非|並非|不是|并不是|並不是|未曾|not\s+(?:part|associated|active)",
    re.IGNORECASE,
)
_ERA_RELATION_CUE_RE = re.compile(
    r"属于|屬於|活跃于|活躍於|处于|處於|时期|時期|时代|時代|"
    r"年代|during|active|part of",
    re.IGNORECASE,
)
_REPRESENTATIVE_CUE_RE = re.compile(
    r"代表|代表性|标志性|標誌性|representative|iconic",
    re.IGNORECASE,
)
_TEMPORAL_CUE_RE = re.compile(
    r"同期|同一时期|同一時期|同时期|同時期|同年代|同时|同時|"
    r"contemporary|same period",
    re.IGNORECASE,
)
_STYLE_ERA_CUE_RE = re.compile(
    r"风格|風格|曲风|曲風|流派|发展|發展|兴起|興起|演变|演變|"
    r"流行|style|genre|develop|emerge",
    re.IGNORECASE,
)

_PREDICATE_TO_RELATION = {
    "belongs_to_era": EraRelationType.BELONGS_TO_ERA,
    "representative_of_era": EraRelationType.REPRESENTATIVE_OF_ERA,
    "temporal_association": EraRelationType.TEMPORAL_ASSOCIATION,
    "career_phase": EraRelationType.CAREER_PHASE,
    "style_era_association": EraRelationType.STYLE_ERA_ASSOCIATION,
}
_RELATION_TO_GRAPH = {
    EraRelationType.BELONGS_TO_ERA: WorldLineRelation.BELONGS_TO_ERA,
    EraRelationType.REPRESENTATIVE_OF_ERA: WorldLineRelation.REPRESENTATIVE_OF_ERA,
    EraRelationType.TEMPORAL_ASSOCIATION: WorldLineRelation.TEMPORAL_ASSOCIATION,
    EraRelationType.CAREER_PHASE: WorldLineRelation.CAREER_PHASE,
    EraRelationType.STYLE_ERA_ASSOCIATION: WorldLineRelation.STYLE_ERA_ASSOCIATION,
}
_CAREER_PHASES = {
    "early": "Early career",
    "early_career": "Early career",
    "早期": "Early career",
    "初期": "Early career",
    "breakthrough": "Breakthrough",
    "breakthrough_phase": "Breakthrough",
    "突破期": "Breakthrough",
    "peak": "Peak period",
    "peak_period": "Peak period",
    "巅峰期": "Peak period",
    "巔峰期": "Peak period",
    "late": "Late career",
    "late_career": "Late career",
    "晚期": "Late career",
}


class EraRelationStatus(StrEnum):
    ACCEPTED = "accepted"
    REJECTED = "rejected"


class _SupportedClaim(BaseModel):
    model_config = ConfigDict(extra="ignore", frozen=True)

    claim_id: str = Field(min_length=1, max_length=128)
    subject_entity_id: str = Field(min_length=1, max_length=128)
    predicate: str = Field(min_length=1, max_length=100)
    object_entity_id: str | None = Field(default=None, max_length=128)
    object_value: str | int | float | bool | None = None
    time: ClaimTime | None = None
    source_chunk_id: str = Field(min_length=1, max_length=200)
    evidence_span: str = Field(min_length=1, max_length=5_000)
    extraction_confidence: float = Field(ge=0, le=1)
    verification_confidence: float = Field(ge=0, le=1)


class EraRelationCandidate(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    source_entity: EraEntityReference
    target_entity: EraEntityReference | None = None
    era: EraDescriptor | None = None
    relation_type: EraRelationType
    start_year: int | None = Field(default=None, ge=1000, le=2999)
    end_year: int | None = Field(default=None, ge=1000, le=2999)
    temporal_precision: EraTemporalPrecision
    confidence: float = Field(ge=0, le=1)
    evidence: EraRelationEvidence
    evidence_source: EvidenceSource
    claim_subject_entity_id: str
    claim_predicate: str
    claim_object_entity_id: str | None = None
    claim_object_value: str | int | float | bool | None = None
    claim_time: ClaimTime | None = None


class EraRelationDecision(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    candidate: EraRelationCandidate
    status: EraRelationStatus
    rejection_reasons: list[str] = Field(default_factory=list, max_length=20)


class EraExpansionTrace(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    requested: bool
    candidate_count: int = Field(ge=0)
    accepted_count: int = Field(ge=0)
    rejected_count: int = Field(ge=0)
    rejection_reasons: dict[str, int] = Field(default_factory=dict)
    evidence_count: int = Field(ge=0)
    temporal_conflict_count: int = Field(ge=0)
    aggregation_trace: list[dict] = Field(default_factory=list)
    validator_version: str = ERA_VALIDATOR_VERSION


class EraExpansionResult(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    graph: WorldLineGraph
    sources: list[EvidenceSource] = Field(max_length=30)
    relations: list[EraRelation] = Field(default_factory=list, max_length=20)
    rejected: list[EraRelationDecision] = Field(default_factory=list)
    trace: EraExpansionTrace


def is_explicit_era_query(query: str, search_plan: SearchPlan) -> bool:
    """Era projection is opt-in so ordinary WorldLine requests remain unchanged."""

    return (
        QueryType.TIMELINE in search_plan.query_types
        and _ERA_QUERY_RE.search(query) is not None
    )


def _metadata_string(document: MusicKnowledgeDocument, key: str) -> str | None:
    value = document.metadata.get(key)
    return value if isinstance(value, str) and value else None


def _era_id(name: str, *, prefix: str = "") -> str:
    normalized = " ".join(name.split()).casefold()
    ascii_value = unicodedata.normalize("NFKD", normalized).encode(
        "ascii", "ignore"
    ).decode("ascii")
    slug = re.sub(r"[^a-z0-9]+", "_", ascii_value).strip("_")
    if not slug:
        slug = f"period_{sha256(normalized.encode('utf-8')).hexdigest()[:16]}"
    if prefix:
        slug = f"{prefix}_{slug}"
    return f"era:{slug[:120]}"


def _era_descriptor(value: object, *, prefix: str = "") -> EraDescriptor | None:
    if not isinstance(value, str):
        return None
    name = " ".join(value.split())
    if not name or len(name) > 300:
        return None
    return EraDescriptor(node_id=_era_id(name, prefix=prefix), canonical_name=name)


def _time_values(
    value: ClaimTime | None,
) -> tuple[int | None, int | None, EraTemporalPrecision, str | None]:
    if value is None:
        return None, None, EraTemporalPrecision.UNKNOWN, None
    start_year = value.start_year
    end_year = value.end_year
    if value.year is not None:
        start_year = end_year = value.year
    elif value.date:
        start_year = end_year = value.date.year
    if start_year is None and end_year is None:
        precision = EraTemporalPrecision.UNKNOWN
    elif value.raw and _APPROXIMATE_TIME_RE.search(value.raw):
        precision = EraTemporalPrecision.APPROXIMATE
    else:
        precision = EraTemporalPrecision.EXACT
    return start_year, end_year, precision, value.raw


def _time_is_supported_by_span(
    candidate: EraRelationCandidate,
) -> bool:
    expected_start, expected_end, expected_precision, expected_raw = _time_values(
        candidate.claim_time
    )
    if (
        candidate.start_year != expected_start
        or candidate.end_year != expected_end
        or candidate.temporal_precision is not expected_precision
        or candidate.evidence.raw_time != expected_raw
    ):
        return False
    span = candidate.evidence.exact_span.casefold()
    years = {candidate.start_year, candidate.end_year} - {None}
    if any(str(year) not in span for year in years):
        return False
    if candidate.temporal_precision is EraTemporalPrecision.APPROXIMATE:
        raw = candidate.evidence.raw_time
        if raw and raw.casefold() not in span:
            return False
    return True


def _relation_is_supported_by_span(candidate: EraRelationCandidate) -> bool:
    span = " ".join(candidate.evidence.exact_span.split()).casefold()
    if _NEGATED_RELATION_RE.search(span):
        return False
    if candidate.relation_type is EraRelationType.TEMPORAL_ASSOCIATION:
        return _TEMPORAL_CUE_RE.search(span) is not None and bool(candidate.target_entity) and (
            candidate.target_entity.canonical_name.casefold() in span
        )
    if candidate.relation_type is EraRelationType.CAREER_PHASE:
        if candidate.claim_predicate == "debuted":
            return _DEBUT_CUE_RE.search(span) is not None
        value = candidate.claim_object_value
        if not isinstance(value, str):
            return False
        aliases = {
            "early": ("early", "早期", "初期"),
            "breakthrough": ("breakthrough", "突破"),
            "peak": ("peak", "巅峰", "巔峰"),
            "late": ("late", "晚期"),
        }
        normalized = value.casefold()
        label = next((key for key in aliases if key in normalized or any(
            alias in normalized for alias in aliases[key]
        )), None)
        return label is not None and any(alias in span for alias in aliases[label])
    era_name = (
        candidate.source_entity.canonical_name
        if candidate.relation_type is EraRelationType.REPRESENTATIVE_OF_ERA
        else candidate.era.canonical_name if candidate.era is not None else ""
    )
    if candidate.claim_predicate == "released":
        return bool({candidate.start_year, candidate.end_year} - {None}) and (
            _RELEASE_CUE_RE.search(span) is not None
        )
    if _release_event_key(candidate) is not None:
        return True
    if not era_name or era_name.casefold() not in span:
        return False
    if candidate.relation_type is EraRelationType.REPRESENTATIVE_OF_ERA:
        return _REPRESENTATIVE_CUE_RE.search(span) is not None
    if candidate.relation_type is EraRelationType.STYLE_ERA_ASSOCIATION:
        return _STYLE_ERA_CUE_RE.search(span) is not None
    return _ERA_RELATION_CUE_RE.search(span) is not None


def _decade_descriptor(start_year: int | None, end_year: int | None) -> EraDescriptor | None:
    year = start_year if start_year == end_year else None
    if year is None:
        return None
    decade = year // 10 * 10
    return EraDescriptor(node_id=f"era:{decade}s", canonical_name=f"{decade}s")


def _release_event_key(candidate: EraRelationCandidate) -> tuple[str, str] | None:
    """Normalize a dated release of one work, never an artist's entire career."""
    if (
        candidate.relation_type is not EraRelationType.BELONGS_TO_ERA
        or candidate.claim_predicate not in {"released", "belongs_to_era"}
        or candidate.source_entity.node_type not in {WorldLineNodeType.ALBUM, WorldLineNodeType.SONG}
        or candidate.era != _decade_descriptor(candidate.start_year, candidate.end_year)
        or candidate.start_year is None
    ):
        return None
    span = " ".join(candidate.evidence.exact_span.split()).casefold()
    # Editions and recordings are distinct events and must not be collapsed.
    if re.search(r"re[- ]?(?:release|issue)|remaster|edition|再版|重发|重發", span):
        return None
    if re.search(r"\b(?:not|never)\s+released?\b|未发行|未發行", span):
        return None
    if not named_in_text(candidate.source_entity.canonical_name, span):
        return None
    year = candidate.start_year
    release_date = re.search(
        rf"\breleased?\s+(?:(?:in|on|early|late|mid)\s+){{0,3}}{year}\b", span,
    ) or re.search(rf"发行于?\s*{year}|發行於?\s*{year}", span)
    return (candidate.source_entity.node_id, "release") if release_date else None


def _reference(node: WorldLineNode, knowledge_id: str | None = None) -> EraEntityReference:
    return EraEntityReference(
        node_id=node.id,
        node_type=node.type,
        canonical_name=node.name,
        knowledge_entity_id=knowledge_id,
    )


def _knowledge_entity_map(
    graph: WorldLineGraph,
    search_plan: SearchPlan,
) -> dict[str, EraEntityReference]:
    node_by_id = {node.id: node for node in graph.nodes}
    result: dict[str, EraEntityReference] = {}
    for node in graph.nodes:
        raw_reference = node.metadata.get("entity_reference")
        if isinstance(raw_reference, dict):
            knowledge_id = raw_reference.get("knowledge_entity_id")
            if isinstance(knowledge_id, str) and knowledge_id:
                result[knowledge_id] = _reference(node, knowledge_id)
        if node.type is WorldLineNodeType.GENRE and node.id.startswith("genre:"):
            result[node.id] = _reference(node, node.id)
    for entity in search_plan.entities:
        catalog = entity.catalog_reference
        knowledge_id = entity.knowledge_entity_id
        if catalog is None or knowledge_id is None:
            continue
        node_id = f"{catalog.provider}:{catalog.entity_type.value}:{catalog.entity_id}"
        node = node_by_id.get(node_id)
        if node is not None:
            result[knowledge_id] = _reference(node, knowledge_id)
    return result


class EraRelationCandidateExtractor:
    """Converts only verified supported_claims into typed candidates."""

    def extract(
        self,
        search_plan: SearchPlan,
        graph: WorldLineGraph,
        documents: list[MusicKnowledgeDocument],
    ) -> list[EraRelationCandidate]:
        entity_map = _knowledge_entity_map(graph, search_plan)
        candidates: list[EraRelationCandidate] = []
        for document in documents:
            raw_claims = document.metadata.get("supported_claims")
            if not isinstance(raw_claims, list):
                continue
            parent_id = _metadata_string(document, "parent_id")
            child_id = _metadata_string(document, "chunk_id")
            context_version = _metadata_string(document, "context_version")
            if parent_id is None or child_id is None or context_version is None:
                continue
            for raw_claim in raw_claims:
                if not isinstance(raw_claim, dict):
                    continue
                try:
                    claim = _SupportedClaim.model_validate(raw_claim)
                except ValidationError:
                    continue
                candidate = self._candidate(
                    document,
                    claim,
                    entity_map,
                    parent_id=parent_id,
                    child_id=child_id,
                    context_version=context_version,
                )
                if candidate is not None:
                    candidates.append(candidate)
        return candidates

    def _candidate(
        self,
        document: MusicKnowledgeDocument,
        claim: _SupportedClaim,
        entity_map: dict[str, EraEntityReference],
        *,
        parent_id: str,
        child_id: str,
        context_version: str,
    ) -> EraRelationCandidate | None:
        source = entity_map.get(claim.subject_entity_id)
        if source is None:
            return None
        relation_type = _PREDICATE_TO_RELATION.get(claim.predicate)
        start_year, end_year, precision, raw_time = _time_values(claim.time)
        era: EraDescriptor | None = None
        target: EraEntityReference | None = None
        output_source = source

        # Verified dated release claims establish activity in a decade. They
        # do not establish that the artist was representative or at a peak.
        if claim.predicate == "released":
            if source.node_type not in {
                WorldLineNodeType.SONG,
                WorldLineNodeType.ALBUM,
                WorldLineNodeType.ARTIST,
            }:
                return None
            if source.node_type is WorldLineNodeType.ARTIST and (
                claim.object_entity_id is None
                or not claim.object_entity_id.startswith(("song:", "album:"))
            ):
                return None
            era = _decade_descriptor(start_year, end_year)
            if era is None:
                return None
            relation_type = EraRelationType.BELONGS_TO_ERA
        elif claim.predicate == "debuted":
            if source.node_type is not WorldLineNodeType.ARTIST:
                return None
            if start_year is None or start_year != end_year:
                return None
            era = EraDescriptor(
                node_id=_era_id("Early career", prefix=source.node_id.replace(":", "_")),
                canonical_name="Early career",
            )
            relation_type = EraRelationType.CAREER_PHASE
        elif relation_type is EraRelationType.TEMPORAL_ASSOCIATION:
            if claim.object_entity_id is None:
                return None
            target = entity_map.get(claim.object_entity_id)
            if target is None:
                return None
        elif relation_type is EraRelationType.CAREER_PHASE:
            if not isinstance(claim.object_value, str):
                return None
            key = claim.object_value.strip().casefold().replace(" ", "_")
            phase_name = _CAREER_PHASES.get(key)
            if phase_name is None:
                return None
            era = EraDescriptor(
                node_id=_era_id(phase_name, prefix=source.node_id.replace(":", "_")),
                canonical_name=phase_name,
            )
        else:
            era = _era_descriptor(claim.object_value)

        if relation_type is None:
            return None
        if relation_type is EraRelationType.REPRESENTATIVE_OF_ERA:
            if era is None:
                return None
            output_source = EraEntityReference(
                node_id=era.node_id,
                node_type=WorldLineNodeType.ERA,
                canonical_name=era.canonical_name,
            )
            target = source
            era = None

        material = {
            "document_id": document.id,
            "claim_id": claim.claim_id,
            "chunk_id": child_id,
            "span": claim.evidence_span,
        }
        digest = sha256(
            json.dumps(material, ensure_ascii=False, sort_keys=True).encode("utf-8")
        ).hexdigest()[:24]
        evidence_id = f"knowledge:era:{digest}"
        confidence = min(
            claim.extraction_confidence,
            claim.verification_confidence,
        )
        evidence = EraRelationEvidence(
            evidence_id=evidence_id,
            source_id=evidence_id,
            claim_id=claim.claim_id,
            exact_span=claim.evidence_span,
            start_year=start_year,
            end_year=end_year,
            raw_time=raw_time,
            confidence=confidence,
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
                "parent_id": parent_id,
                "child_id": child_id,
                "claim_id": claim.claim_id,
                "source_chunk_id": claim.source_chunk_id,
                "context_version": context_version,
                "retrieval_version": _metadata_string(document, "retrieval_version"),
                "era_expansion_version": ERA_EXPANSION_VERSION,
                "validator_version": ERA_VALIDATOR_VERSION,
            },
        )
        return EraRelationCandidate(
            source_entity=output_source,
            target_entity=target,
            era=era,
            relation_type=relation_type,
            start_year=start_year,
            end_year=end_year,
            temporal_precision=precision,
            confidence=confidence,
            evidence=evidence,
            evidence_source=evidence_source,
            claim_subject_entity_id=claim.subject_entity_id,
            claim_predicate=claim.predicate,
            claim_object_entity_id=claim.object_entity_id,
            claim_object_value=claim.object_value,
            claim_time=claim.time,
        )


class EraRelationValidator:
    def __init__(self, *, threshold: float = ERA_ACCEPTANCE_THRESHOLD) -> None:
        self.threshold = threshold

    def validate(
        self,
        graph: WorldLineGraph,
        documents: list[MusicKnowledgeDocument],
        candidates: list[EraRelationCandidate],
    ) -> tuple[list[EraRelation], list[EraRelationDecision]]:
        document_counts = Counter(item.id for item in documents)
        document_by_id = {item.id: item for item in documents}
        graph_nodes = {node.id: node for node in graph.nodes}
        accepted: list[EraRelationCandidate] = []
        decisions: list[EraRelationDecision] = []
        for candidate in candidates:
            reasons = self._rejection_reasons(
                candidate,
                graph_nodes,
                document_counts,
                document_by_id,
            )
            status = (
                EraRelationStatus.REJECTED if reasons else EraRelationStatus.ACCEPTED
            )
            decisions.append(
                EraRelationDecision(
                    candidate=candidate,
                    status=status,
                    rejection_reasons=reasons,
                )
            )
            if not reasons:
                accepted.append(candidate)
        return self._aggregate(accepted), decisions

    def _rejection_reasons(
        self,
        candidate: EraRelationCandidate,
        graph_nodes: dict[str, WorldLineNode],
        document_counts: Counter[str],
        document_by_id: dict[str, MusicKnowledgeDocument],
    ) -> list[str]:
        reasons: list[str] = []
        original_source_id = (
            candidate.target_entity.node_id
            if candidate.relation_type is EraRelationType.REPRESENTATIVE_OF_ERA
            and candidate.target_entity is not None
            else candidate.source_entity.node_id
        )
        original_source = graph_nodes.get(original_source_id)
        if original_source is None:
            reasons.append("source_entity_missing")
        elif candidate.relation_type is EraRelationType.BELONGS_TO_ERA and (
            original_source.type not in {
                WorldLineNodeType.ARTIST,
                WorldLineNodeType.SONG,
                WorldLineNodeType.ALBUM,
            }
        ):
            reasons.append("entity_type_not_allowed")
        elif candidate.relation_type is EraRelationType.REPRESENTATIVE_OF_ERA and (
            original_source.type not in {
                WorldLineNodeType.ARTIST,
                WorldLineNodeType.SONG,
                WorldLineNodeType.ALBUM,
            }
        ):
            reasons.append("representative_type_not_allowed")
        elif candidate.relation_type is EraRelationType.CAREER_PHASE and (
            original_source.type is not WorldLineNodeType.ARTIST
        ):
            reasons.append("career_phase_requires_artist")
        elif candidate.relation_type is EraRelationType.STYLE_ERA_ASSOCIATION and (
            original_source.type is not WorldLineNodeType.GENRE
        ):
            reasons.append("style_era_requires_genre")
        if candidate.target_entity is not None and (
            candidate.relation_type is not EraRelationType.REPRESENTATIVE_OF_ERA
            and candidate.target_entity.node_id not in graph_nodes
        ):
            reasons.append("target_entity_missing")
        if candidate.relation_type is EraRelationType.TEMPORAL_ASSOCIATION:
            if candidate.target_entity is None:
                reasons.append("temporal_target_missing")
            elif candidate.target_entity.node_id == candidate.source_entity.node_id:
                reasons.append("temporal_self_relation")
            if candidate.start_year is None and candidate.end_year is None:
                reasons.append("temporal_evidence_missing")
        if candidate.relation_type is not EraRelationType.TEMPORAL_ASSOCIATION:
            if (
                candidate.relation_type is not EraRelationType.REPRESENTATIVE_OF_ERA
                and candidate.era is None
            ):
                reasons.append("era_target_missing")
        evidence_source = candidate.evidence_source
        document_id = evidence_source.metadata.get("document_id")
        document = document_by_id.get(document_id) if isinstance(document_id, str) else None
        if document is None or document_counts[document_id] != 1:
            reasons.append("evidence_document_missing")
        else:
            if document.metadata.get("sufficiency_status") != "sufficient":
                reasons.append("insufficient_context")
            if candidate.evidence.exact_span not in document.content:
                reasons.append("evidence_span_missing")
            if not self._claim_is_present(document, candidate):
                reasons.append("claim_not_in_document")
            if document.metadata.get("parent_id") != evidence_source.metadata.get("parent_id"):
                reasons.append("evidence_parent_mismatch")
            if document.metadata.get("chunk_id") != evidence_source.metadata.get("child_id"):
                reasons.append("evidence_chunk_mismatch")
        if candidate.confidence < self.threshold:
            reasons.append("confidence_below_threshold")
        if candidate.evidence.confidence != candidate.confidence:
            reasons.append("candidate_confidence_mismatch")
        if len(candidate.evidence.exact_span) > 2_000:
            reasons.append("evidence_span_too_long")
        if not _time_is_supported_by_span(candidate):
            reasons.append("time_not_in_evidence_span")
        if not _relation_is_supported_by_span(candidate):
            reasons.append("relation_not_in_evidence_span")
        return list(dict.fromkeys(reasons))

    @staticmethod
    def _claim_is_present(
        document: MusicKnowledgeDocument,
        candidate: EraRelationCandidate,
    ) -> bool:
        raw_claims = document.metadata.get("supported_claims")
        if not isinstance(raw_claims, list):
            return False
        for raw_claim in raw_claims:
            if not isinstance(raw_claim, dict):
                continue
            try:
                claim = _SupportedClaim.model_validate(raw_claim)
            except ValidationError:
                continue
            confidence = min(
                claim.extraction_confidence,
                claim.verification_confidence,
            )
            if (
                claim.claim_id == candidate.evidence.claim_id
                and claim.subject_entity_id == candidate.claim_subject_entity_id
                and claim.predicate == candidate.claim_predicate
                and claim.object_entity_id == candidate.claim_object_entity_id
                and claim.object_value == candidate.claim_object_value
                and claim.time == candidate.claim_time
                and claim.source_chunk_id
                == candidate.evidence_source.metadata.get("source_chunk_id")
                and claim.evidence_span == candidate.evidence.exact_span
                and confidence == candidate.confidence
            ):
                return True
        return False

    @staticmethod
    def _aggregate(candidates: list[EraRelationCandidate]) -> list[EraRelation]:
        grouped: dict[tuple[str, str, EraRelationType], list[EraRelationCandidate]] = {}
        release_events: dict[tuple[str, str], list[EraRelationCandidate]] = {}
        for candidate in candidates:
            event = _release_event_key(candidate)
            if event is not None:
                release_events.setdefault(event, []).append(candidate)
        for candidate in candidates:
            target_id = (
                candidate.target_entity.node_id
                if candidate.target_entity is not None
                else candidate.era.node_id if candidate.era is not None else ""
            )
            key = (
                candidate.source_entity.node_id,
                target_id,
                candidate.relation_type,
            )
            grouped.setdefault(key, []).append(candidate)
        output: list[EraRelation] = []
        for items in grouped.values():
            first = items[0]
            event = _release_event_key(first)
            event_items = release_events.get(event, [])
            if len({item.start_year for item in event_items}) > 1:
                # Preserve each mutually exclusive decade target, but attach
                # the same opposing assertions to every alternative.
                items = list({item.evidence.evidence_id: item for item in [
                    *items, *event_items,
                ]}.values())
            evidence_by_id = {item.evidence.evidence_id: item.evidence for item in items}
            evidence_overflow = max(0, len(evidence_by_id) - MAX_ERA_EVIDENCE)
            if evidence_overflow:
                retained_ids = {item.evidence.evidence_id for item in balanced_evidence(
                    items, limit=MAX_ERA_EVIDENCE,
                    stance=lambda item: (item.start_year, item.end_year),
                )}
                evidence_by_id = {
                    evidence_id: evidence
                    for evidence_id, evidence in evidence_by_id.items()
                    if evidence_id in retained_ids
                }
                items = [
                    item for item in items
                    if item.evidence.evidence_id in retained_ids
                ]
            assertions = [
                {
                    "evidence_id": item.evidence.evidence_id,
                    "start_year": item.start_year,
                    "end_year": item.end_year,
                    "raw_time": item.evidence.raw_time,
                    "precision": item.temporal_precision.value,
                }
                for item in items
            ]
            known_ranges = {
                (item.start_year, item.end_year)
                for item in items
                if item.start_year is not None or item.end_year is not None
            }
            temporal_conflict = len(known_ranges) > 1
            has_unknown = any(
                item.start_year is None and item.end_year is None for item in items
            )
            if temporal_conflict:
                start_year = end_year = None
                precision = EraTemporalPrecision.UNKNOWN
                penalty = 0.15
            elif known_ranges:
                start_year, end_year = next(iter(known_ranges))
                precision = (
                    EraTemporalPrecision.APPROXIMATE
                    if has_unknown
                    or any(
                        item.temporal_precision is EraTemporalPrecision.APPROXIMATE
                        for item in items
                    )
                    else EraTemporalPrecision.EXACT
                )
                penalty = 0.05 if has_unknown else 0.0
            else:
                start_year = end_year = None
                precision = EraTemporalPrecision.UNKNOWN
                penalty = 0.0
            output.append(
                EraRelation(
                    source_entity=first.source_entity,
                    target_entity=first.target_entity,
                    era=first.era,
                    relation_type=first.relation_type,
                    start_year=start_year,
                    end_year=end_year,
                    temporal_precision=precision,
                    confidence=max(0.0, min(item.confidence for item in items) - penalty),
                    evidence=list(evidence_by_id.values()),
                    metadata={
                        "era_expansion_version": ERA_EXPANSION_VERSION,
                        "validator_version": ERA_VALIDATOR_VERSION,
                        "temporal_conflict": temporal_conflict,
                        "conflict_flags": ["temporal_conflict"] if temporal_conflict else [],
                        "evidence_overflow_count": evidence_overflow,
                        "temporal_assertions": assertions,
                        "claim_predicates": sorted({item.claim_predicate for item in items}),
                        "derivation": (
                            "release_activity"
                            if all(item.claim_predicate == "released" for item in items)
                            else "debut_activity"
                            if all(item.claim_predicate == "debuted" for item in items)
                            else "verified_claim"
                        ),
                    },
                )
            )
        return output


class EraGraphProjector:
    def project(
        self,
        graph: WorldLineGraph,
        sources: list[EvidenceSource],
        relations: list[EraRelation],
        candidates: list[EraRelationCandidate],
    ) -> tuple[WorldLineGraph, list[EvidenceSource]]:
        if not relations:
            return graph, sources
        existing_nodes = {node.id: node for node in graph.nodes}
        delta_nodes: dict[str, WorldLineNode] = {graph.root: existing_nodes[graph.root]}
        delta_edges: list[WorldLineEdge] = []
        source_by_evidence = {
            item.evidence.evidence_id: item.evidence_source for item in candidates
        }
        delta_sources: dict[str, EvidenceSource] = {}
        existing_sources = {source.id: source for source in sources}
        for relation in relations:
            endpoints = [relation.source_entity]
            if relation.target_entity is not None:
                endpoints.append(relation.target_entity)
            if relation.era is not None:
                endpoints.append(
                    EraEntityReference(
                        node_id=relation.era.node_id,
                        node_type=WorldLineNodeType.ERA,
                        canonical_name=relation.era.canonical_name,
                    )
                )
            for endpoint in endpoints:
                existing = existing_nodes.get(endpoint.node_id)
                if existing is not None:
                    if existing.type is not endpoint.node_type:
                        raise WorldLineMergeError("node_type_conflict")
                    if (
                        endpoint.node_type is WorldLineNodeType.ERA
                        and existing.name != endpoint.canonical_name
                    ):
                        raise WorldLineMergeError("node_identity_conflict")
                    delta_nodes.setdefault(endpoint.node_id, existing)
                    continue
                if endpoint.node_type is not WorldLineNodeType.ERA:
                    raise WorldLineMergeError("missing_relation_endpoint")
                delta_nodes[endpoint.node_id] = WorldLineNode(
                    id=endpoint.node_id,
                    type=WorldLineNodeType.ERA,
                    name=endpoint.canonical_name,
                    description="由已验证音乐证据投影的时代或职业阶段",
                    metadata={
                        "era_expansion_version": ERA_EXPANSION_VERSION,
                        "executable": False,
                        "expandable": False,
                    },
                )
            target_id = (
                relation.target_entity.node_id
                if relation.target_entity is not None
                else relation.era.node_id if relation.era is not None else ""
            )
            evidence_ids = [item.evidence_id for item in relation.evidence]
            for evidence_id in evidence_ids:
                projected_source = source_by_evidence.get(evidence_id)
                if projected_source is None:
                    raise WorldLineMergeError("missing_edge_evidence")
                delta_sources[evidence_id] = projected_source.model_copy(
                    update={
                        "metadata": {
                            **projected_source.metadata,
                            "relation_key": (
                                f"{relation.source_entity.node_id}|{target_id}|"
                                f"{relation.relation_type.value}"
                            ),
                            "temporal_conflict": relation.metadata.get(
                                "temporal_conflict", False
                            ),
                        }
                    }
                )
                delta_sources[evidence_id] = stable_projected_source(
                    delta_sources[evidence_id], existing_sources.get(evidence_id),
                )
            delta_edges.append(
                WorldLineEdge(
                    source=relation.source_entity.node_id,
                    target=target_id,
                    relation=_RELATION_TO_GRAPH[relation.relation_type],
                    description=self._description(relation),
                    evidence_ids=evidence_ids,
                )
            )
        delta = WorldLineGraph(
            root=graph.root,
            nodes=list(delta_nodes.values()),
            edges=delta_edges,
        )
        merged_graph, merged_sources = merge_worldline_graph(
            graph,
            delta,
            sources,
            list(delta_sources.values()),
            target_node_id=graph.root,
        )
        keys = {(item.source_entity.node_id, (item.target_entity or item.era).node_id,
                 _RELATION_TO_GRAPH[item.relation_type]) for item in relations
                if item.metadata.get("temporal_conflict")}
        return preserve_conflict_descriptions(merged_graph, delta, keys), merged_sources

    @staticmethod
    def _description(relation: EraRelation) -> str:
        target = (
            relation.target_entity.canonical_name
            if relation.target_entity is not None
            else relation.era.canonical_name if relation.era is not None else "时代"
        )
        if relation.metadata.get("temporal_conflict"):
            return f"{relation.source_entity.canonical_name} 与 {target} 的年代归属存在来源冲突；保留互斥候选"
        if relation.metadata.get("derivation") == "release_activity":
            return f"{relation.source_entity.canonical_name} 在 {target} 有已验证发行活动"
        if relation.metadata.get("derivation") == "debut_activity":
            return f"{relation.source_entity.canonical_name} 在 {target} 有已验证出道活动"
        return (
            f"{relation.source_entity.canonical_name} 与 {target} 的"
            f"{relation.relation_type.value}关系"
        )


def merge_era_relations(
    old: list[EraRelation],
    new: list[EraRelation],
) -> list[EraRelation]:
    """Non-mutating relation merge used across checkpointed exploration turns."""

    merged: dict[tuple[str, str, EraRelationType], EraRelation] = {}
    for relation in [*old, *new]:
        target_id = (
            relation.target_entity.node_id
            if relation.target_entity is not None
            else relation.era.node_id if relation.era is not None else ""
        )
        key = (relation.source_entity.node_id, target_id, relation.relation_type)
        previous = merged.get(key)
        if previous is None:
            merged[key] = relation
            continue
        if previous == relation:
            continue
        evidence = {item.evidence_id: item for item in previous.evidence}
        evidence.update({item.evidence_id: item for item in relation.evidence})
        if len(evidence) > MAX_ERA_EVIDENCE:
            raise WorldLineMergeError("capacity_reached")
        conflict = (
            previous.start_year != relation.start_year
            or previous.end_year != relation.end_year
        )
        merged[key] = previous.model_copy(
            update={
                "start_year": None if conflict else previous.start_year,
                "end_year": None if conflict else previous.end_year,
                "temporal_precision": (
                    EraTemporalPrecision.UNKNOWN
                    if conflict
                    else max(
                        previous.temporal_precision,
                        relation.temporal_precision,
                        key=lambda item: {
                            EraTemporalPrecision.EXACT: 0,
                            EraTemporalPrecision.APPROXIMATE: 1,
                            EraTemporalPrecision.UNKNOWN: 2,
                        }[item],
                    )
                ),
                "confidence": max(
                    0.0,
                    min(previous.confidence, relation.confidence)
                    - (0.15 if conflict else 0.0),
                ),
                "evidence": list(evidence.values()),
                "metadata": {
                    **previous.metadata,
                    "temporal_conflict": (
                        conflict
                        or bool(previous.metadata.get("temporal_conflict"))
                        or bool(relation.metadata.get("temporal_conflict"))
                    ),
                    "conflict_flags": ["temporal_conflict"] if (
                        conflict or previous.metadata.get("temporal_conflict")
                        or relation.metadata.get("temporal_conflict")
                    ) else [],
                    "temporal_assertions": [
                        *previous.metadata.get("temporal_assertions", []),
                        *relation.metadata.get("temporal_assertions", []),
                    ],
                },
            }
        )
    if len(merged) > 20:
        raise WorldLineMergeError("capacity_reached")
    return list(merged.values())


def _fit_graph_capacity(
    graph: WorldLineGraph,
    sources: list[EvidenceSource],
    relations: list[EraRelation],
) -> tuple[list[EraRelation], int]:
    """Keep the existing WorldLine usable when bounded graph slots are full."""

    node_ids = {node.id for node in graph.nodes}
    edge_keys = {(edge.source, edge.target, edge.relation) for edge in graph.edges}
    edge_evidence = {
        (edge.source, edge.target, edge.relation): set(edge.evidence_ids)
        for edge in graph.edges
    }
    source_ids = {source.id for source in sources}
    selected: list[EraRelation] = []
    skipped = 0
    for relation in sorted(relations, key=lambda item: item.confidence, reverse=True):
        target_id = (
            relation.target_entity.node_id
            if relation.target_entity is not None
            else relation.era.node_id
        )
        candidate_nodes = {relation.source_entity.node_id, target_id}
        candidate_edge = (
            relation.source_entity.node_id,
            target_id,
            _RELATION_TO_GRAPH[relation.relation_type],
        )
        candidate_sources = {item.source_id for item in relation.evidence}
        if (
            len(node_ids | candidate_nodes) > 15
            or len(edge_keys | {candidate_edge}) > 20
            or len(source_ids | candidate_sources) > 30
            or len(edge_evidence.get(candidate_edge, set()) | candidate_sources) > 8
        ):
            skipped += 1
            continue
        selected.append(relation)
        node_ids.update(candidate_nodes)
        edge_keys.add(candidate_edge)
        edge_evidence.setdefault(candidate_edge, set()).update(candidate_sources)
        source_ids.update(candidate_sources)
    return selected, skipped


class EraExpansionService:
    def __init__(self, *, threshold: float = ERA_ACCEPTANCE_THRESHOLD) -> None:
        self.extractor = EraRelationCandidateExtractor()
        self.validator = EraRelationValidator(threshold=threshold)
        self.projector = EraGraphProjector()

    def expand(
        self,
        query: str,
        search_plan: SearchPlan,
        graph: WorldLineGraph,
        sources: list[EvidenceSource],
        documents: list[MusicKnowledgeDocument],
    ) -> EraExpansionResult:
        requested = is_explicit_era_query(query, search_plan)
        if not requested:
            return EraExpansionResult(
                graph=graph,
                sources=sources,
                trace=EraExpansionTrace(
                    requested=False,
                    candidate_count=0,
                    accepted_count=0,
                    rejected_count=0,
                    evidence_count=0,
                    temporal_conflict_count=0,
                ),
            )
        candidates = self.extractor.extract(search_plan, graph, documents)
        relations, decisions = self.validator.validate(graph, documents, candidates)
        relations, capacity_rejected = _fit_graph_capacity(graph, sources, relations)
        accepted_candidate_keys = {
            evidence.evidence_id
            for relation in relations
            for evidence in relation.evidence
        }
        accepted_candidates = [
            candidate
            for candidate in candidates
            if candidate.evidence.evidence_id in accepted_candidate_keys
        ]
        projected_graph, projected_sources = self.projector.project(
            graph,
            sources,
            relations,
            accepted_candidates,
        )
        reasons = Counter(
            reason
            for decision in decisions
            for reason in decision.rejection_reasons
        )
        if capacity_rejected:
            reasons["capacity_reached"] += capacity_rejected
        evidence_overflow = sum(
            int(item.metadata.get("evidence_overflow_count", 0))
            for item in relations
        )
        if evidence_overflow:
            reasons["evidence_capacity_reached"] += evidence_overflow
        trace = EraExpansionTrace(
            requested=True,
            candidate_count=len(candidates),
            accepted_count=len(relations),
            rejected_count=capacity_rejected + evidence_overflow + sum(
                decision.status is EraRelationStatus.REJECTED for decision in decisions
            ),
            rejection_reasons=dict(reasons),
            evidence_count=sum(len(item.evidence) for item in relations),
            temporal_conflict_count=sum(
                bool(item.metadata.get("temporal_conflict")) for item in relations
            ),
            aggregation_trace=aggregation_trace(decisions, relations),
        )
        LOGGER.info(
            "worldline.era_expansion",
            extra={"era_expansion_trace": trace.model_dump(mode="json")},
        )
        return EraExpansionResult(
            graph=projected_graph,
            sources=projected_sources,
            relations=relations,
            rejected=[
                item for item in decisions if item.status is EraRelationStatus.REJECTED
            ],
            trace=trace,
        )
