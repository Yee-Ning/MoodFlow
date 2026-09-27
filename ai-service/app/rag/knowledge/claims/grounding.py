"""Conservative evidence-local admission, independent of artist or source identity."""

from __future__ import annotations

import re

from ..entity_linking.models import CanonicalEntity
from .models import ClaimCandidate
from .rules import is_subjective


def validate_predicate_types(candidate: ClaimCandidate, entities: list[CanonicalEntity]) -> None:
    """Reject structured arguments incompatible with the existing predicate."""
    by_id = {entity.entity_id: entity for entity in entities}
    subject = by_id.get(candidate.subject_entity_id)
    target = by_id.get(candidate.object_entity_id)
    predicate = candidate.predicate.value
    if subject is None:
        raise ValueError("claim_unknown_subject_entity")
    if predicate == "includes_track" and (
        subject.entity_type.value != "album" or target is None or target.entity_type.value != "song"
    ):
        raise ValueError("claim_predicate_argument_type_mismatch")
    if predicate == "performed_at" and target is not None and target.entity_type.value not in {"event", "place"}:
        raise ValueError("claim_predicate_argument_type_mismatch")
    if predicate in {"has_genre", "contains_music_element"} and target is not None and target.entity_type.value != "genre":
        raise ValueError("claim_predicate_argument_type_mismatch")
    if predicate in {"belongs_to_scene", "representative_of_scene", "style_scene_association", "emerged_from_scene"}:
        if not re.search(r"\b(?:scene|movement|community|collective)\b|场景|場景|社群|音乐圈|音樂圈", candidate.evidence_span, re.I):
            raise ValueError("claim_scene_not_explicit")


_MODALITIES = {
    "counterfactual": r"\b(?:would have|could have|had it not|were it not)\b",
    "hypothetical": r"\b(?:if|unless|imagine|suppose|hypothetical|another band could)\b|假如|假设|如果",
    "speculation": r"\b(?:might|may have|could|would|perhaps|possibly|probably|seems?|appears? to)\b|或许|可能|也许",
    "negation": r"\b(?:not|never|neither|didn't|wasn't|weren't|isn't)\b|并非|没有|未曾",
    "quotation_or_opinion": r"\b(?:I think|I believe|in my opinion|arguably|argued|claimed|claims that|said|says)\b",
}


def contains_name(text: str, name: str) -> bool:
    # Latin substrings must not bind a different name (e.g. Ann / Annie).
    pattern = re.escape(name)
    if name and name[0].isascii() and name[0].isalnum():
        pattern = r"(?<!\w)" + pattern
    if name and name[-1].isascii() and name[-1].isalnum():
        pattern += r"(?!\w)"
    return bool(name and re.search(pattern, text, re.IGNORECASE))


def sentence_ranges(text: str) -> list[tuple[int, int]]:
    boundaries = [0, *(match.end() for match in re.finditer(r"\n+|(?<=[.!?。！？])\s+", text)), len(text)]
    return [(start, end) for start, end in zip(boundaries, boundaries[1:]) if text[start:end].strip()]


def batches(text: str, limit: int = 1200) -> list[tuple[int, int]]:
    """Contiguous original ranges; never rewrite or join nonadjacent evidence."""
    result: list[tuple[int, int]] = []
    for start, end in sentence_ranges(text):
        if result and end - result[-1][0] <= limit:
            result[-1] = (result[-1][0], end)
        else:
            result.append((start, end))
    return result


def validate_local(candidate: ClaimCandidate, text: str, entities: list[CanonicalEntity]) -> None:
    validate_predicate_types(candidate, entities)
    span = candidate.evidence_span
    if text.count(span) != 1:
        raise ValueError("claim_evidence_missing_or_ambiguous")
    start = text.index(span)
    end = start + len(span)
    # Include the enclosing sentence so a model cannot omit an 'if' prefix.
    ranges = [(a, b) for a, b in sentence_ranges(text) if a < end and b > start]
    local = text[ranges[0][0]:ranges[-1][1]] if ranges else span
    for modality, pattern in _MODALITIES.items():
        if re.search(pattern, local, re.IGNORECASE):
            raise ValueError("claim_nonfactual_" + modality)
    if is_subjective(local):
        raise ValueError("claim_nonfactual_quotation_or_opinion")
    owners: dict[str, set[str]] = {}
    for entity in entities:
        for alias in [entity.canonical_name, *entity.aliases]:
            owners.setdefault(alias.casefold(), set()).add(entity.entity_id)
    for entity_id in [candidate.subject_entity_id, candidate.object_entity_id]:
        if entity_id is None:
            continue
        entity = next((item for item in entities if item.entity_id == entity_id), None)
        if entity is None or not any(
            owners[name.casefold()] == {entity_id} and contains_name(span, name)
            for name in [entity.canonical_name, *entity.aliases]
        ):
            raise ValueError("claim_entity_not_explicit_in_evidence")
    if candidate.time is not None:
        values = [candidate.time.raw, *[str(value) for value in (
            candidate.time.year, candidate.time.start_year, candidate.time.end_year,
            candidate.time.date.year if candidate.time.date else None,
        ) if value is not None]]
        if not any(value and value in span for value in values):
            raise ValueError("claim_time_not_explicit_in_evidence")
