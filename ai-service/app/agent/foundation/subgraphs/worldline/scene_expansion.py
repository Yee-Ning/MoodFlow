"""Evidence-first Scene relations within the existing WorldLine graph."""

from __future__ import annotations

from collections import Counter
from enum import StrEnum
from hashlib import sha256
from itertools import combinations
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
    normalized_text,
    quoted_first_person_subject,
    scene_aliases,
    preserve_conflict_descriptions,
)
from app.agent.foundation.subgraphs.worldline.era_expansion import (
    _SupportedClaim,
    _knowledge_entity_map,
    _metadata_string,
    _time_values,
)
from app.agent.foundation.subgraphs.worldline.merge import (
    WorldLineMergeError,
    merge_worldline_graph,
)
from app.agent.foundation.subgraphs.worldline.models import (
    EraEntityReference,
    EraTemporalPrecision,
    EvidenceSource,
    EvidenceType,
    MusicKnowledgeDocument,
    SceneDescriptor,
    SceneRelation,
    SceneRelationEvidence,
    SceneRelationType,
    WorldLineEdge,
    WorldLineGraph,
    WorldLineNode,
    WorldLineNodeType,
    WorldLineRelation,
)
from app.rag.knowledge.claims.models import ClaimTime
from app.rag.query_understanding.models import SearchPlan


SCENE_EXPANSION_VERSION = "v1"
SCENE_VALIDATOR_VERSION = "v1"
SCENE_ACCEPTANCE_THRESHOLD = 0.85
MAX_SCENE_EVIDENCE = 8
LOGGER = logging.getLogger(__name__)

_SCENE_QUERY_RE = re.compile(
    r"(?:music\s+scene|\bscene\b|场景|場景|音乐社群|音樂社群|"
    r"音乐群体|音樂群體|地域音乐文化|地域音樂文化|"
    r"[\u4e00-\u9fffA-Za-z]+\s*(?:音乐圈|音樂圈))",
    re.IGNORECASE,
)
_QUOTED_ENTITY_RE = re.compile(r"[《\"“'](?:[^》\"”']{1,300})[》\"”']")
_SCENE_CUE_RE = re.compile(
    r"\b(?:scene|community|movement|collective)\b|"
    r"场景|場景|社群|音乐圈|音樂圈|音乐运动|音樂運動",
    re.IGNORECASE,
)
_NEGATION_RE = re.compile(
    r"不属于|不屬於|并非|並非|不是|无关|無關|"
    r"not\s+(?:part\s+of|associated\s+with|from|a\s+member\s+of)|"
    r"never\s+(?:part\s+of|associated\s+with)|"
    r"\b(?:never|not)\s+(?:emerg\w*|originat\w*|belong\w*)|"
    r"\b(?:didn't|did\s+not)\s+(?:emerge|originate|belong)",
    re.IGNORECASE,
)
_MEMBERSHIP_CUE_RE = re.compile(
    r"属于|屬於|参与|參與|成员|成員|一员|一員|"
    r"part\s+of|member\s+of",
    re.IGNORECASE,
)
_REPRESENTATIVE_CUE_RE = re.compile(
    r"代表|标志性|標誌性|representative|iconic|leading",
    re.IGNORECASE,
)
_STYLE_CUE_RE = re.compile(
    r"风格|風格|曲风|曲風|流派|genre|style",
    re.IGNORECASE,
)
_EMERGED_CUE_RE = re.compile(
    r"兴起于|興起於|发源于|發源於|源自|起源于|起源於|"
    r"emerged\s+from|grew\s+out\s+of|originated\s+in",
    re.IGNORECASE,
)
_ASSOCIATION_CUE_RE = re.compile(
    r"同属|同屬|共同参与|共同參與|同一|same|both|together",
    re.IGNORECASE,
)
_SCENE_SPECULATION_RE = re.compile(
    r"\b(?:may|might|could)\s+(?:have\s+|be\s+|have\s+been\s+)?"
    r"(?:emerg\w*|belong\w*|originat\w*|part\s+of|a\s+member\s+of)|"
    r"\b(?:possibly|perhaps|probably|seemingly|speculated)\b|"
    r"可能|或许|或許|似乎|据推测|據推測",
    re.IGNORECASE,
)


def _scene_name_in_span(name: str, span: str) -> bool:
    """Compare typography only; never change the immutable evidence text.

    Remove balanced double-quote delimiters, keeping their contents, whitespace,
    hyphens and word boundaries. Apostrophes and unmatched quotes stay intact.
    """
    def comparison(value: str) -> str:
        value = normalized_text(value)
        return re.sub(r'“([^“”"]+)”|"([^“”"]+)"',
                      lambda match: match.group(1) or match.group(2), value)

    return re.search(rf"(?<!\w){re.escape(comparison(name))}(?!\w)", comparison(span)) is not None

_PREDICATE_TO_RELATION = {
    "belongs_to_scene": SceneRelationType.BELONGS_TO_SCENE,
    "representative_of_scene": SceneRelationType.REPRESENTATIVE_OF_SCENE,
    "scene_association": SceneRelationType.SCENE_ASSOCIATION,
    "style_scene_association": SceneRelationType.STYLE_SCENE_ASSOCIATION,
    "emerged_from_scene": SceneRelationType.EMERGED_FROM_SCENE,
}


class SceneRelationStatus(StrEnum):
    ACCEPTED = "accepted"
    REJECTED = "rejected"


class SceneRelationCandidate(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    source_entity: EraEntityReference
    target_entity: EraEntityReference | None = None
    scene: SceneDescriptor | None = None
    scene_name: str = Field(min_length=1, max_length=300)
    location: str | None = Field(default=None, min_length=1, max_length=200)
    relation_type: SceneRelationType
    start_year: int | None = Field(default=None, ge=1000, le=2999)
    end_year: int | None = Field(default=None, ge=1000, le=2999)
    temporal_precision: EraTemporalPrecision
    confidence: float = Field(ge=0, le=1)
    evidence: SceneRelationEvidence
    evidence_source: EvidenceSource
    claim_subject_entity_id: str
    claim_predicate: str
    claim_object_entity_id: str | None = None
    claim_object_value: str | int | float | bool | None = None
    claim_time: ClaimTime | None = None


class SceneRelationDecision(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    candidate: SceneRelationCandidate
    status: SceneRelationStatus
    rejection_reasons: list[str] = Field(default_factory=list, max_length=20)


class SceneExpansionTrace(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    requested: bool
    candidate_count: int = Field(ge=0)
    accepted_count: int = Field(ge=0)
    rejected_count: int = Field(ge=0)
    rejection_reasons: dict[str, int] = Field(default_factory=dict)
    evidence_count: int = Field(ge=0)
    temporal_conflict_count: int = Field(ge=0)
    location_conflict_count: int = Field(ge=0)
    attribution_conflict_count: int = Field(default=0, ge=0)
    aggregation_trace: list[dict] = Field(default_factory=list)
    validator_version: str = SCENE_VALIDATOR_VERSION


class SceneExpansionResult(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    graph: WorldLineGraph
    sources: list[EvidenceSource] = Field(max_length=30)
    relations: list[SceneRelation] = Field(default_factory=list, max_length=20)
    rejected: list[SceneRelationDecision] = Field(default_factory=list)
    trace: SceneExpansionTrace


def is_explicit_scene_query(query: str) -> bool:
    return _SCENE_QUERY_RE.search(_QUOTED_ENTITY_RE.sub("", query)) is not None


def _scene_descriptor(value: object) -> SceneDescriptor | None:
    if not isinstance(value, str):
        return None
    name = " ".join(value.split())
    if not name or len(name) > 300:
        return None
    normalized = unicodedata.normalize("NFKD", name.casefold()).encode(
        "ascii", "ignore"
    ).decode("ascii")
    slug = re.sub(r"[^a-z0-9]+", "_", normalized).strip("_")
    if any(ord(character) > 127 for character in name):
        suffix = sha256(name.casefold().encode("utf-8")).hexdigest()[:12]
        slug = f"{slug[:105]}_{suffix}" if slug else suffix
    if not slug:
        slug = sha256(name.casefold().encode("utf-8")).hexdigest()[:16]
    return SceneDescriptor(node_id=f"scene:{slug[:120]}", canonical_name=name)


def _normalize_membership_scenes(
    candidates: list[SceneRelationCandidate], graph_nodes: dict[str, WorldLineNode],
) -> list[SceneRelationCandidate]:
    """Unify validated aliases using an existing identity or the fuller name."""
    groups: dict[str, list[SceneDescriptor]] = {}
    for candidate in candidates:
        if candidate.relation_type is SceneRelationType.BELONGS_TO_SCENE and candidate.scene:
            base = min(scene_aliases(candidate.scene_name), key=len)
            groups.setdefault(base, []).append(candidate.scene)
    canonical = {}
    for base, descriptors in groups.items():
        existing = [node for node in graph_nodes.values() if node.type is WorldLineNodeType.SCENE
                    and base == min(scene_aliases(node.name), key=len)]
        # Multiple existing identities need an explicit resolver, not guessing.
        if len(existing) > 1:
            continue
        canonical[base] = (
            SceneDescriptor(node_id=existing[0].id, canonical_name=existing[0].name)
            if existing else max(descriptors, key=lambda d: (len(d.canonical_name), d.node_id))
        )
    return [candidate.model_copy(update={
        "scene": descriptor, "scene_name": descriptor.canonical_name,
    }) if candidate.relation_type is SceneRelationType.BELONGS_TO_SCENE and (
        descriptor := canonical.get(min(scene_aliases(candidate.scene_name), key=len))
    ) is not None else candidate for candidate in candidates]


def _scene_target_id(relation: SceneRelation) -> str:
    return (
        relation.target_entity.node_id
        if relation.target_entity is not None
        else relation.scene.node_id
    )


class SceneRelationCandidateExtractor:
    """Only the already verified supported_claims may produce candidates."""

    def extract(
        self,
        search_plan: SearchPlan,
        graph: WorldLineGraph,
        documents: list[MusicKnowledgeDocument],
    ) -> list[SceneRelationCandidate]:
        entity_map = _knowledge_entity_map(graph, search_plan)
        candidates: list[SceneRelationCandidate] = []
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
                    document, claim, raw_claim, entity_map,
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
        raw_claim: dict[str, object],
        entity_map: dict[str, EraEntityReference],
        *,
        parent_id: str,
        child_id: str,
        context_version: str,
    ) -> SceneRelationCandidate | None:
        relation_type = _PREDICATE_TO_RELATION.get(claim.predicate)
        source = entity_map.get(claim.subject_entity_id)
        if relation_type is None or source is None:
            return None
        target: EraEntityReference | None = None
        scene: SceneDescriptor | None = None
        output_source = source
        if relation_type is SceneRelationType.SCENE_ASSOCIATION:
            target = entity_map.get(claim.object_entity_id or "")
            scene = _scene_descriptor(raw_claim.get("scene_name"))
            if target is None or scene is None:
                return None
        else:
            scene = _scene_descriptor(claim.object_value)
            if scene is None:
                return None
        scene_name = scene.canonical_name
        if relation_type is SceneRelationType.REPRESENTATIVE_OF_SCENE:
            output_source = EraEntityReference(
                node_id=scene.node_id,
                node_type=WorldLineNodeType.SCENE,
                canonical_name=scene_name,
            )
            target = source
            scene = None
        elif relation_type is SceneRelationType.SCENE_ASSOCIATION:
            scene = None
        raw_location = raw_claim.get("scene_location")
        location = (
            " ".join(raw_location.split())
            if isinstance(raw_location, str) and 0 < len(raw_location) <= 200
            else None
        )
        start_year, end_year, precision, raw_time = _time_values(claim.time)
        material = {
            "document_id": document.id,
            "claim_id": claim.claim_id,
            "chunk_id": child_id,
            "span": claim.evidence_span,
        }
        digest = sha256(json.dumps(
            material, ensure_ascii=False, sort_keys=True,
        ).encode("utf-8")).hexdigest()[:24]
        evidence_id = f"knowledge:scene:{digest}"
        confidence = min(
            claim.extraction_confidence, claim.verification_confidence,
        )
        evidence = SceneRelationEvidence(
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
                **relation_provenance(document, claim.claim_id, claim.evidence_span),
                "parent_id": parent_id,
                "child_id": child_id,
                "claim_id": claim.claim_id,
                "source_chunk_id": claim.source_chunk_id,
                "context_version": context_version,
                "retrieval_version": _metadata_string(document, "retrieval_version"),
                "scene_expansion_version": SCENE_EXPANSION_VERSION,
                "validator_version": SCENE_VALIDATOR_VERSION,
            },
        )
        return SceneRelationCandidate(
            source_entity=output_source,
            target_entity=target,
            scene=scene,
            scene_name=scene_name,
            location=location,
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


class SceneRelationValidator:
    def __init__(self, *, threshold: float = SCENE_ACCEPTANCE_THRESHOLD) -> None:
        self.threshold = threshold

    def validate(
        self,
        graph: WorldLineGraph,
        documents: list[MusicKnowledgeDocument],
        candidates: list[SceneRelationCandidate],
    ) -> tuple[list[SceneRelation], list[SceneRelationDecision]]:
        document_counts = Counter(document.id for document in documents)
        document_by_id = {document.id: document for document in documents}
        graph_nodes = {node.id: node for node in graph.nodes}
        accepted: list[SceneRelationCandidate] = []
        contradictions: list[SceneRelationCandidate] = []
        decisions: list[SceneRelationDecision] = []
        for candidate in candidates:
            reasons = self._rejection_reasons(
                candidate, graph_nodes, document_counts, document_by_id,
            )
            status = (
                SceneRelationStatus.REJECTED if reasons
                else SceneRelationStatus.ACCEPTED
            )
            decisions.append(SceneRelationDecision(
                candidate=candidate, status=status, rejection_reasons=reasons,
            ))
            if not reasons:
                accepted.append(candidate)
            elif reasons == ["negated_scene_membership"]:
                contradictions.append(candidate)
        normalized = _normalize_membership_scenes([*accepted, *contradictions], graph_nodes)
        accepted, contradictions = normalized[:len(accepted)], normalized[len(accepted):]
        pair_scenes: dict[tuple[str, str], set[str]] = {}
        for candidate in accepted:
            if candidate.relation_type is SceneRelationType.SCENE_ASSOCIATION:
                key = (
                    candidate.source_entity.node_id,
                    candidate.target_entity.node_id,
                )
                pair_scenes.setdefault(key, set()).add(candidate.scene_name)
        ambiguous_pairs = {
            key for key, names in pair_scenes.items() if len(names) > 1
        }
        if ambiguous_pairs:
            accepted = [
                candidate for candidate in accepted
                if candidate.relation_type is not SceneRelationType.SCENE_ASSOCIATION
                or (candidate.source_entity.node_id, candidate.target_entity.node_id)
                not in ambiguous_pairs
            ]
            decisions = [
                decision.model_copy(update={
                    "status": SceneRelationStatus.REJECTED,
                    "rejection_reasons": ["scene_identity_conflict"],
                })
                if decision.status is SceneRelationStatus.ACCEPTED
                and decision.candidate.relation_type is SceneRelationType.SCENE_ASSOCIATION
                and (
                    decision.candidate.source_entity.node_id,
                    decision.candidate.target_entity.node_id,
                ) in ambiguous_pairs
                else decision
                for decision in decisions
            ]
        return self._aggregate(accepted, contradictions), decisions

    def _rejection_reasons(
        self,
        candidate: SceneRelationCandidate,
        graph_nodes: dict[str, WorldLineNode],
        document_counts: Counter[str],
        document_by_id: dict[str, MusicKnowledgeDocument],
    ) -> list[str]:
        reasons: list[str] = []
        entity_id = (
            candidate.target_entity.node_id
            if candidate.relation_type is SceneRelationType.REPRESENTATIVE_OF_SCENE
            else candidate.source_entity.node_id
        )
        entity = graph_nodes.get(entity_id)
        allowed = {
            WorldLineNodeType.ARTIST,
            WorldLineNodeType.SONG,
            WorldLineNodeType.ALBUM,
        }
        if entity is None:
            reasons.append("source_entity_missing")
        elif candidate.relation_type in {
            SceneRelationType.BELONGS_TO_SCENE,
            SceneRelationType.REPRESENTATIVE_OF_SCENE,
            SceneRelationType.EMERGED_FROM_SCENE,
        } and entity.type not in allowed:
            reasons.append("entity_type_not_allowed")
        elif candidate.relation_type is SceneRelationType.STYLE_SCENE_ASSOCIATION and (
            entity.type is not WorldLineNodeType.GENRE
        ):
            reasons.append("style_scene_requires_genre")
        elif candidate.relation_type is SceneRelationType.SCENE_ASSOCIATION and (
            entity.type is not WorldLineNodeType.ARTIST
            or candidate.target_entity is None
            or candidate.target_entity.node_type is not WorldLineNodeType.ARTIST
            or candidate.target_entity.node_id not in graph_nodes
            or candidate.target_entity.node_id == entity_id
        ):
            reasons.append("scene_association_requires_two_artists")
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
            if document.metadata.get("parent_id") != candidate.evidence_source.metadata.get("parent_id"):
                reasons.append("evidence_parent_mismatch")
            if document.metadata.get("chunk_id") != candidate.evidence_source.metadata.get("child_id"):
                reasons.append("evidence_chunk_mismatch")
            if not self._claim_is_present(document, candidate):
                reasons.append("claim_not_in_document")
        if candidate.confidence < self.threshold:
            reasons.append("confidence_below_threshold")
        if candidate.evidence.confidence != candidate.confidence:
            reasons.append("candidate_confidence_mismatch")
        if len(candidate.evidence.exact_span) > 2_000:
            reasons.append("evidence_span_too_long")
        if _SCENE_SPECULATION_RE.search(candidate.evidence.exact_span):
            reasons.append("speculative_scene_relation")
        if not self._time_is_supported(candidate):
            reasons.append("time_not_in_evidence_span")
        membership_stance = self._membership_stance(candidate, document)
        if membership_stance == "contradiction":
            reasons.append("negated_scene_membership")
        elif not self._scene_is_supported(candidate) and membership_stance != "support":
            reasons.append("scene_not_in_evidence_span")
        if candidate.location is not None and (
            candidate.location.casefold()
            not in candidate.evidence.exact_span.casefold()
        ):
            reasons.append("location_not_in_evidence_span")
        return list(dict.fromkeys(reasons))

    @staticmethod
    def _membership_stance(
        candidate: SceneRelationCandidate, document: MusicKnowledgeDocument | None,
    ) -> str | None:
        """Normalize scene aliases and bind narrowly scoped membership assertions."""
        if candidate.relation_type is not SceneRelationType.BELONGS_TO_SCENE:
            return None
        span = normalized_text(candidate.evidence.exact_span)
        aliases = scene_aliases(candidate.scene_name)
        if not any(named_in_text(alias, span) for alias in aliases):
            return None
        named = named_in_text(candidate.source_entity.canonical_name, span)
        quoted_subject = bool(document and quoted_first_person_subject(
            candidate.source_entity.canonical_name, document.title, span,
        ))
        if not named and not quoted_subject:
            return None
        entity = rf"(?<!\w){re.escape(normalized_text(candidate.source_entity.canonical_name))}(?!\w)"
        actor = rf"(?:{entity}|\b(?:we|i)\b)" if quoted_subject else entity
        copula = r"\s+(?:(?:was|is|are|were|became|remained)\s+|[’'](?:re|m)\s+)?"
        for alias in aliases:
            scene = rf"(?<!\w){re.escape(alias)}(?!\w)"
            if re.search(
                rf"{actor}{copula}not\s+(?:part\s+of|associated\s+with|from|a\s+member\s+of)"
                rf"\s+(?:the\s+)?{scene}", span,
            ) or (quoted_subject and re.search(
                rf"{scene}[’'\".\s]{{0,8}}\s*(?:we|i)[’']?(?:re|m|\s+are|\s+am)"
                r"\s+not\s+part\s+of\s+that\b", span,
            )):
                return "contradiction"
        # "Frontrunner of X" is a membership attribution, not mere venue or
        # era co-occurrence. X must be the normalized scene in the claim.
        if not _NEGATION_RE.search(span) and (
            _SCENE_CUE_RE.search(span) or _SCENE_CUE_RE.search(candidate.scene_name)
        ):
            for alias in aliases:
                scene = rf"(?<!\w){re.escape(alias)}(?!\w)"
                if re.search(rf"{actor}{copula}(?:part|a\s+member)\s+of\s+(?:the\s+)?{scene}", span):
                    return "support"
                if named and (re.search(
                    rf"{entity}{copula}(?:(?:one\s+of\s+the|a)\s+)?"
                    rf"(?:frontrunners?|leading\s+members?)\s+of\s+{scene}", span,
                ) or re.search(
                    rf"(?:frontrunners?|leading\s+members?)\s+of\s+{scene}\s*,\s*{entity}", span,
                )):
                    return "support"
        return None

    @staticmethod
    def _claim_is_present(
        document: MusicKnowledgeDocument,
        candidate: SceneRelationCandidate,
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
                and claim.predicate == candidate.claim_predicate
                and claim.object_entity_id == candidate.claim_object_entity_id
                and claim.object_value == candidate.claim_object_value
                and claim.time == candidate.claim_time
                and claim.source_chunk_id
                == candidate.evidence_source.metadata.get("source_chunk_id")
                and claim.source_chunk_id == document.metadata.get("chunk_id")
                and claim.evidence_span == candidate.evidence.exact_span
                and min(claim.extraction_confidence, claim.verification_confidence)
                == candidate.confidence
                and (
                    candidate.relation_type is not SceneRelationType.SCENE_ASSOCIATION
                    or raw.get("scene_name") == candidate.scene_name
                )
                and raw.get("scene_location") == candidate.location
            ):
                return True
        return False

    @staticmethod
    def _time_is_supported(candidate: SceneRelationCandidate) -> bool:
        start, end, precision, raw = _time_values(candidate.claim_time)
        if (
            (start, end, precision, raw)
            != (
                candidate.start_year, candidate.end_year,
                candidate.temporal_precision, candidate.evidence.raw_time,
            )
        ):
            return False
        span = candidate.evidence.exact_span.casefold()
        if any(str(year) not in span for year in {start, end} - {None}):
            return False
        if precision is EraTemporalPrecision.APPROXIMATE and raw and raw.casefold() not in span:
            return False
        return True

    @staticmethod
    def _scene_is_supported(candidate: SceneRelationCandidate) -> bool:
        span = " ".join(candidate.evidence.exact_span.split()).casefold()
        if _NEGATION_RE.search(span) or _SCENE_CUE_RE.search(span) is None:
            return False
        if not _scene_name_in_span(candidate.scene_name, span):
            return False
        named_entity = (
            candidate.target_entity
            if candidate.relation_type is SceneRelationType.REPRESENTATIVE_OF_SCENE
            else candidate.source_entity
        )
        if named_entity is None or named_entity.canonical_name.casefold() not in span:
            return False
        relation_type = candidate.relation_type
        if relation_type is SceneRelationType.BELONGS_TO_SCENE:
            return _MEMBERSHIP_CUE_RE.search(span) is not None
        if relation_type is SceneRelationType.REPRESENTATIVE_OF_SCENE:
            return _REPRESENTATIVE_CUE_RE.search(span) is not None
        if relation_type is SceneRelationType.STYLE_SCENE_ASSOCIATION:
            return _STYLE_CUE_RE.search(span) is not None
        if relation_type is SceneRelationType.EMERGED_FROM_SCENE:
            return _EMERGED_CUE_RE.search(span) is not None
        return (
            _ASSOCIATION_CUE_RE.search(span) is not None
            and candidate.target_entity is not None
            and candidate.target_entity.canonical_name.casefold() in span
        )

    @staticmethod
    def _aggregate(
        candidates: list[SceneRelationCandidate],
        contradictions: list[SceneRelationCandidate] | None = None,
    ) -> list[SceneRelation]:
        grouped: dict[tuple[str, str, SceneRelationType], list[SceneRelationCandidate]] = {}
        for candidate in candidates:
            target_id = (
                candidate.target_entity.node_id
                if candidate.target_entity is not None
                else candidate.scene.node_id
            )
            grouped.setdefault((
                candidate.source_entity.node_id, target_id,
                candidate.relation_type,
            ), []).append(candidate)
        output: list[SceneRelation] = []
        for key, items in grouped.items():
            first = items[0]
            opposing = [item for item in contradictions or [] if (
                item.source_entity.node_id,
                item.scene.node_id if item.scene else item.target_entity.node_id,
                item.relation_type,
            ) == key]
            positive_ids = {item.evidence.evidence_id for item in items}
            items = [*items, *opposing]
            evidence = {item.evidence.evidence_id: item.evidence for item in items}
            overflow = max(0, len(evidence) - MAX_SCENE_EVIDENCE)
            retained = {item.evidence.evidence_id for item in balanced_evidence(
                items, limit=MAX_SCENE_EVIDENCE,
                stance=lambda item: item.evidence.evidence_id in positive_ids,
            )}
            items = [item for item in items if item.evidence.evidence_id in retained]
            evidence = {key: item for key, item in evidence.items() if key in retained}
            attribution_conflict = bool(opposing)
            ranges = {
                (item.start_year, item.end_year) for item in items
                if item.start_year is not None or item.end_year is not None
            }
            time_conflict = len(ranges) > 1
            if len(ranges) == 1 and not time_conflict:
                start_year, end_year = next(iter(ranges))
                precision = (
                    EraTemporalPrecision.APPROXIMATE
                    if any(item.temporal_precision is EraTemporalPrecision.APPROXIMATE for item in items)
                    else EraTemporalPrecision.EXACT
                )
            else:
                start_year = end_year = None
                precision = EraTemporalPrecision.UNKNOWN
            locations = {item.location for item in items if item.location is not None}
            location_conflict = len(locations) > 1
            location = next(iter(locations)) if len(locations) == 1 else None
            output.append(SceneRelation(
                source_entity=first.source_entity,
                target_entity=first.target_entity,
                scene=first.scene,
                scene_name=first.scene_name,
                location=location,
                relation_type=first.relation_type,
                start_year=start_year,
                end_year=end_year,
                temporal_precision=precision,
                confidence=max(0.0, min(item.confidence for item in items)
                               - (0.15 if time_conflict or location_conflict else 0.0)),
                evidence=list(evidence.values()),
                metadata={
                    "scene_expansion_version": SCENE_EXPANSION_VERSION,
                    "validator_version": SCENE_VALIDATOR_VERSION,
                    "temporal_conflict": time_conflict,
                    "location_conflict": location_conflict,
                    "attribution_conflict": attribution_conflict,
                    "conflict_flags": [key for key, active in (
                        ("temporal_conflict", time_conflict),
                        ("location_conflict", location_conflict),
                        ("attribution_conflict", attribution_conflict),
                    ) if active],
                    "provenance": [{
                        "evidence_id": item.evidence.evidence_id,
                        "claim_id": item.evidence.claim_id,
                        "stance": "support" if item.evidence.evidence_id in positive_ids else "contradiction",
                    } for item in items],
                    "temporal_assertions": [
                        {"evidence_id": item.evidence.evidence_id,
                         "start_year": item.start_year,
                         "end_year": item.end_year,
                         "raw_time": item.evidence.raw_time}
                        for item in items
                    ],
                    "location_assertions": [
                        {"evidence_id": item.evidence.evidence_id,
                         "location": item.location}
                        for item in items
                    ],
                    "evidence_overflow_count": overflow,
                },
            ))
        return output


def _derive_artist_associations(relations: list[SceneRelation]) -> list[SceneRelation]:
    """Two independently grounded memberships may establish a shared scene."""

    by_scene: dict[str, list[SceneRelation]] = {}
    for relation in relations:
        if (
            relation.relation_type is SceneRelationType.BELONGS_TO_SCENE
            and relation.source_entity.node_type is WorldLineNodeType.ARTIST
            and relation.scene is not None
            and not relation.metadata.get("attribution_conflict")
        ):
            by_scene.setdefault(relation.scene.node_id, []).append(relation)
    pair_memberships: dict[tuple[str, str], list[tuple[SceneRelation, SceneRelation]]] = {}
    for members in by_scene.values():
        for left, right in combinations(sorted(
            members, key=lambda item: item.source_entity.node_id,
        ), 2):
            pair_memberships.setdefault((
                left.source_entity.node_id,
                right.source_entity.node_id,
            ), []).append((left, right))
    associations: list[SceneRelation] = []
    for pairs in pair_memberships.values():
        # One graph edge cannot encode competing scene identities for the same
        # artist pair. Keep their individual scene memberships instead.
        if len(pairs) != 1:
            continue
        left, right = pairs[0]
        evidence = {item.evidence_id: item for item in [
            *left.evidence, *right.evidence,
        ]}
        if len(evidence) > MAX_SCENE_EVIDENCE:
            continue
        locations = {value for value in (left.location, right.location) if value}
        associations.append(SceneRelation(
            source_entity=left.source_entity,
            target_entity=right.source_entity,
            scene_name=left.scene_name,
            location=next(iter(locations)) if len(locations) == 1 else None,
            relation_type=SceneRelationType.SCENE_ASSOCIATION,
            temporal_precision=EraTemporalPrecision.UNKNOWN,
            confidence=max(
                0.0,
                min(left.confidence, right.confidence)
                - (0.15 if len(locations) > 1 else 0.0),
            ),
            evidence=list(evidence.values()),
            metadata={
                "derivation": "two_verified_scene_memberships",
                "scene_id": left.scene.node_id,
                "location_conflict": len(locations) > 1,
                "temporal_assertions": [
                    *left.metadata.get("temporal_assertions", []),
                    *right.metadata.get("temporal_assertions", []),
                ],
                "location_assertions": [
                    *left.metadata.get("location_assertions", []),
                    *right.metadata.get("location_assertions", []),
                ],
            },
        ))
    return associations


class SceneGraphProjector:
    def project(
        self,
        graph: WorldLineGraph,
        sources: list[EvidenceSource],
        relations: list[SceneRelation],
        candidates: list[SceneRelationCandidate],
    ) -> tuple[WorldLineGraph, list[EvidenceSource]]:
        if not relations:
            return graph, sources
        existing_nodes = {node.id: node for node in graph.nodes}
        delta_nodes: dict[str, WorldLineNode] = {graph.root: existing_nodes[graph.root]}
        delta_edges: dict[tuple[str, str, WorldLineRelation], WorldLineEdge] = {}
        source_by_id = {
            candidate.evidence.evidence_id: candidate.evidence_source
            for candidate in candidates
        }
        delta_sources: dict[str, EvidenceSource] = {}
        for relation in relations:
            endpoints: list[EraEntityReference] = [relation.source_entity]
            if relation.target_entity is not None:
                endpoints.append(relation.target_entity)
            if relation.scene is not None:
                endpoints.append(EraEntityReference(
                    node_id=relation.scene.node_id,
                    node_type=WorldLineNodeType.SCENE,
                    canonical_name=relation.scene.canonical_name,
                ))
            for endpoint in endpoints:
                existing = existing_nodes.get(endpoint.node_id)
                if existing is not None:
                    if existing.type is not endpoint.node_type:
                        raise WorldLineMergeError("node_type_conflict")
                    if endpoint.node_type is WorldLineNodeType.SCENE and existing.name != endpoint.canonical_name:
                        raise WorldLineMergeError("node_identity_conflict")
                    delta_nodes.setdefault(endpoint.node_id, existing)
                elif endpoint.node_type is WorldLineNodeType.SCENE:
                    delta_nodes[endpoint.node_id] = WorldLineNode(
                        id=endpoint.node_id,
                        type=WorldLineNodeType.SCENE,
                        name=endpoint.canonical_name,
                        description="由已验证音乐证据投影的音乐场景",
                        metadata={
                            "scene_expansion_version": SCENE_EXPANSION_VERSION,
                            "executable": False,
                            "expandable": False,
                        },
                    )
                else:
                    raise WorldLineMergeError("missing_relation_endpoint")
            target_id = _scene_target_id(relation)
            relation_type = WorldLineRelation(relation.relation_type.value)
            key = (relation.source_entity.node_id, target_id, relation_type)
            evidence_ids = [item.evidence_id for item in relation.evidence]
            for evidence_id in evidence_ids:
                source = source_by_id.get(evidence_id)
                if source is None:
                    raise WorldLineMergeError("missing_edge_evidence")
                delta_sources[evidence_id] = source
            previous = delta_edges.get(key)
            merged_ids = list(dict.fromkeys([
                *(previous.evidence_ids if previous else []), *evidence_ids,
            ]))
            if len(merged_ids) > MAX_SCENE_EVIDENCE:
                raise WorldLineMergeError("capacity_reached")
            delta_edges[key] = WorldLineEdge(
                source=key[0], target=key[1], relation=key[2],
                description=(
                    f"{relation.source_entity.canonical_name} 与 "
                    f"{relation.scene_name} 的归属存在来源分歧；保留双方证据"
                    if relation.metadata.get("attribution_conflict") else
                    f"{relation.source_entity.canonical_name} 与 "
                    f"{relation.scene_name} 的已验证 {relation.relation_type.value} 关系"
                ),
                evidence_ids=merged_ids,
            )
        delta = WorldLineGraph(
            root=graph.root,
            nodes=list(delta_nodes.values()),
            edges=list(delta_edges.values()),
        )
        merged_graph, merged_sources = merge_worldline_graph(
            graph, delta, sources, list(delta_sources.values()),
            target_node_id=graph.root,
        )
        keys = {(item.source_entity.node_id, _scene_target_id(item), WorldLineRelation(item.relation_type.value))
                for item in relations if item.metadata.get("attribution_conflict")}
        return preserve_conflict_descriptions(merged_graph, delta, keys), merged_sources


def merge_scene_relations(
    old: list[SceneRelation], new: list[SceneRelation],
) -> list[SceneRelation]:
    """Keep prior checkpoint DTOs while merging new evidence by graph edge."""

    merged: dict[tuple[str, str, SceneRelationType], SceneRelation] = {}
    for relation in [*old, *new]:
        key = (
            relation.source_entity.node_id,
            _scene_target_id(relation),
            relation.relation_type,
        )
        previous = merged.get(key)
        if previous is None:
            merged[key] = relation
            continue
        if previous == relation:
            continue
        evidence = {item.evidence_id: item for item in previous.evidence}
        evidence.update({item.evidence_id: item for item in relation.evidence})
        if len(evidence) > MAX_SCENE_EVIDENCE:
            raise WorldLineMergeError("capacity_reached")
        time_conflict = (
            previous.start_year != relation.start_year
            or previous.end_year != relation.end_year
        )
        locations = {value for value in (previous.location, relation.location) if value}
        location_conflict = len(locations) > 1
        merged[key] = previous.model_copy(update={
            "start_year": None if time_conflict else previous.start_year,
            "end_year": None if time_conflict else previous.end_year,
            "temporal_precision": (
                EraTemporalPrecision.UNKNOWN if time_conflict
                else previous.temporal_precision
            ),
            "location": next(iter(locations)) if len(locations) == 1 else None,
            "confidence": max(0.0, min(previous.confidence, relation.confidence)
                              - (0.15 if time_conflict or location_conflict else 0.0)),
            "evidence": list(evidence.values()),
            "metadata": {
                **previous.metadata,
                "conflict_flags": sorted(set(previous.metadata.get("conflict_flags", []))
                                         | set(relation.metadata.get("conflict_flags", []))
                                         | ({"temporal_conflict"} if time_conflict else set())
                                         | ({"location_conflict"} if location_conflict else set())),
                "attribution_conflict": bool(previous.metadata.get("attribution_conflict"))
                or bool(relation.metadata.get("attribution_conflict")),
                "provenance": list({item["evidence_id"]: item for item in [
                    *previous.metadata.get("provenance", []),
                    *relation.metadata.get("provenance", []),
                ]}.values()),
                "temporal_conflict": time_conflict
                or bool(previous.metadata.get("temporal_conflict"))
                or bool(relation.metadata.get("temporal_conflict")),
                "location_conflict": location_conflict
                or bool(previous.metadata.get("location_conflict"))
                or bool(relation.metadata.get("location_conflict")),
                "temporal_assertions": [
                    *previous.metadata.get("temporal_assertions", []),
                    *relation.metadata.get("temporal_assertions", []),
                ],
                "location_assertions": [
                    *previous.metadata.get("location_assertions", []),
                    *relation.metadata.get("location_assertions", []),
                ],
            },
        })
    if len(merged) > 20:
        raise WorldLineMergeError("capacity_reached")
    return list(merged.values())


def _fit_graph_capacity(
    graph: WorldLineGraph,
    sources: list[EvidenceSource],
    relations: list[SceneRelation],
) -> tuple[list[SceneRelation], int]:
    node_ids = {node.id for node in graph.nodes}
    edge_evidence = {
        (edge.source, edge.target, edge.relation): set(edge.evidence_ids)
        for edge in graph.edges
    }
    source_ids = {source.id for source in sources}
    selected: list[SceneRelation] = []
    skipped = 0
    for relation in sorted(relations, key=lambda item: (
        item.relation_type is SceneRelationType.SCENE_ASSOCIATION,
        -item.confidence,
    )):
        if relation.metadata.get("derivation") == "two_verified_scene_memberships":
            scene_id = relation.metadata.get("scene_id")
            member_ids = {
                item.source_entity.node_id for item in selected
                if item.relation_type is SceneRelationType.BELONGS_TO_SCENE
                and item.scene is not None
                and item.scene.node_id == scene_id
            }
            if not {
                relation.source_entity.node_id,
                relation.target_entity.node_id,
            }.issubset(member_ids):
                skipped += 1
                continue
        target_id = _scene_target_id(relation)
        edge_key = (
            relation.source_entity.node_id,
            target_id,
            WorldLineRelation(relation.relation_type.value),
        )
        new_evidence = {item.source_id for item in relation.evidence}
        if (
            len(node_ids | {edge_key[0], edge_key[1]}) > 15
            or len(edge_evidence | {edge_key: set()}) > 20
            or len(source_ids | new_evidence) > 30
            or len(edge_evidence.get(edge_key, set()) | new_evidence) > 8
        ):
            skipped += 1
            continue
        selected.append(relation)
        node_ids.update((edge_key[0], edge_key[1]))
        source_ids.update(new_evidence)
        edge_evidence.setdefault(edge_key, set()).update(new_evidence)
    return selected, skipped


class SceneExpansionService:
    def __init__(self, *, threshold: float = SCENE_ACCEPTANCE_THRESHOLD) -> None:
        self.extractor = SceneRelationCandidateExtractor()
        self.validator = SceneRelationValidator(threshold=threshold)
        self.projector = SceneGraphProjector()

    def expand(
        self,
        query: str,
        search_plan: SearchPlan,
        graph: WorldLineGraph,
        sources: list[EvidenceSource],
        documents: list[MusicKnowledgeDocument],
    ) -> SceneExpansionResult:
        requested = is_explicit_scene_query(query)
        if not requested:
            return SceneExpansionResult(
                graph=graph, sources=sources,
                trace=SceneExpansionTrace(
                    requested=False, candidate_count=0, accepted_count=0,
                    rejected_count=0, evidence_count=0,
                    temporal_conflict_count=0, location_conflict_count=0,
                ),
            )
        candidates = self.extractor.extract(search_plan, graph, documents)
        direct, decisions = self.validator.validate(graph, documents, candidates)
        relations = [*direct, *_derive_artist_associations(direct)]
        relations, capacity_rejected = _fit_graph_capacity(graph, sources, relations)
        accepted_ids = {
            evidence.evidence_id
            for relation in relations for evidence in relation.evidence
        }
        accepted_candidates = [
            candidate for candidate in candidates
            if candidate.evidence.evidence_id in accepted_ids
        ]
        projected_graph, projected_sources = self.projector.project(
            graph, sources, relations, accepted_candidates,
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
        trace = SceneExpansionTrace(
            requested=True,
            candidate_count=len(candidates),
            accepted_count=len(relations),
            rejected_count=capacity_rejected + overflow + sum(
                decision.status is SceneRelationStatus.REJECTED
                for decision in decisions
            ),
            rejection_reasons=dict(reasons),
            evidence_count=sum(len(relation.evidence) for relation in relations),
            temporal_conflict_count=sum(
                bool(relation.metadata.get("temporal_conflict")) for relation in relations
            ),
            location_conflict_count=sum(
                bool(relation.metadata.get("location_conflict")) for relation in relations
            ),
            attribution_conflict_count=sum(
                bool(relation.metadata.get("attribution_conflict")) for relation in relations
            ),
            aggregation_trace=aggregation_trace(decisions, relations),
        )
        LOGGER.info(
            "worldline.scene_expansion",
            extra={"scene_expansion_trace": trace.model_dump(mode="json")},
        )
        return SceneExpansionResult(
            graph=projected_graph, sources=projected_sources,
            relations=relations,
            rejected=[
                decision for decision in decisions
                if decision.status is SceneRelationStatus.REJECTED
            ],
            trace=trace,
        )
