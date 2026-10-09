"""Conservative admission of personal facts; rejection messages contain no data."""

import re
import unicodedata

from app.agent.foundation.long_horizon.history_writer import validate_public_document
from app.agent.foundation.long_horizon.public_events import PublicText
from .models import EvidenceStrength, ExtractedMemory

CATEGORIES = frozenset({"artist", "genre", "music", "format", "mood", "communication",
                        "recommendation_feedback", "fact"})
SUBJECT_ALIASES = {
    "live_version": ("现场", "live", "concert"),
    "slow_songs": ("慢歌", "慢节奏", "slow"),
    "jazz": ("爵士", "jazz"), "rock": ("摇滚", "rock"),
}
NEGATIVE = re.compile(r"不喜欢|讨厌|不要|拒绝|避免|不听|少听|别推荐|don't like|do not like|avoid", re.I)
NONPERSONAL = re.compile(r"假设|假如|如果|例如|比如|他说|她说|朋友说|同事说|别人说|suppose|imagine|my friend", re.I)
INSTRUCTION = re.compile(r"(?:忽略|绕过).*(?:规则|指令)|ignore.*instructions|system[_ ]?prompt", re.I)


class MemoryAdmissionError(ValueError):
    pass


def safe_user_message(message):
    try:
        validate_public_document(PublicText, {"text": message})
    except ValueError:
        return False
    return True


def validate_candidate(candidate: ExtractedMemory, *, user_message=None):
    try:
        validate_public_document(ExtractedMemory, candidate)
    except ValueError:
        raise MemoryAdmissionError("memory_private_or_invalid_text") from None
    if candidate.category.casefold() not in CATEGORIES:
        raise MemoryAdmissionError("memory_unknown_category")
    threshold = 0.9 if candidate.evidence_strength is EvidenceStrength.INFERRED else 0.75
    if candidate.confidence < threshold:
        raise MemoryAdmissionError("memory_confidence_unproven")
    if INSTRUCTION.search(candidate.content):
        raise MemoryAdmissionError("memory_instruction_is_not_a_fact")
    if user_message is None:
        return  # Legacy service callers lack a message; retain their provenance limitation.
    if candidate.evidence_quote not in user_message:
        raise MemoryAdmissionError("memory_evidence_not_in_user_message")
    quote = candidate.evidence_quote
    if (candidate.memory_type.value == "profile" and candidate.valid_until is None
            and re.search(r"这次|这一次|今天|这周|暂时", quote)
            and not re.search(r"以后|今后|长期|一直", quote)):
        raise MemoryAdmissionError("memory_temporary_preference_needs_expiry")
    if candidate.valid_until is not None and not re.search(r"\d{4}[-年/]\d{1,2}", user_message):
        raise MemoryAdmissionError("memory_expiry_not_explicit")
    position = user_message.index(quote)
    clause_start = max((user_message.rfind(mark, 0, position) for mark in "。!?！？；;\n"), default=-1) + 1
    framing = user_message[clause_start:position + len(quote)]
    if NONPERSONAL.search(framing) or re.search(r"(?:朋友|他|她|同事|别人).*(?:喜欢|偏好|讨厌)", quote):
        raise MemoryAdmissionError("memory_subject_or_hypothesis_unproven")
    if candidate.evidence_strength is EvidenceStrength.EXPLICIT and not re.search(
        r"我|本人|以后|今后|不要再|别给|\bI\b|\bmy\b", quote, re.I
    ):
        raise MemoryAdmissionError("memory_personal_subject_unproven")
    negative = bool(NEGATIVE.search(quote))
    preference = candidate.preference or candidate.value.casefold()
    if (preference in {"like", "liked", "love", "prefer"} and negative
            or preference in {"avoid", "dislike", "disliked"} and not negative):
        raise MemoryAdmissionError("memory_polarity_conflict")
    if candidate.category in {"artist", "genre", "format"}:
        def normalized(value):
            return re.sub(r"\W+", "", unicodedata.normalize("NFKC", value).casefold())
        alternatives = SUBJECT_ALIASES.get(candidate.subject.casefold(), (candidate.subject,))
        if not any(normalized(subject) in normalized(quote) for subject in alternatives):
            raise MemoryAdmissionError("memory_entity_not_grounded")
