import json
import re
from collections import Counter, defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from app.agent.foundation.intent_dataset.io import write_jsonl
from app.agent.foundation.intent_dataset.schemas import (
    DatasetSource,
    IntentDatasetSample,
    label_signature,
)
from app.agent.foundation.rule_routing.normalizer import normalize_text

_EDGE_PUNCTUATION = " \t\r\n,.!?;:，。！？；：、"
_SOURCE_PRIORITY = {
    DatasetSource.MANUAL: 0,
    DatasetSource.RULE_BOOTSTRAP: 1,
    DatasetSource.SYNTHETIC: 2,
}


@dataclass(frozen=True)
class CleaningSummary:
    input_samples: int
    cleaned_samples: int
    rejected_samples: int
    exact_duplicates: int
    normalized_duplicates: int
    conflict_groups: int

    def as_dict(self) -> dict[str, int]:
        return {
            "input_samples": self.input_samples,
            "cleaned_samples": self.cleaned_samples,
            "rejected_samples": self.rejected_samples,
            "exact_duplicates": self.exact_duplicates,
            "normalized_duplicates": self.normalized_duplicates,
            "conflict_groups": self.conflict_groups,
        }


@dataclass(frozen=True)
class _Candidate:
    line_number: int
    original_text: str
    sample: IntentDatasetSample


def clean_text(text: str) -> str:
    """Normalize typography and whitespace without rewriting lexical content."""
    return normalize_text(text).text


def normalized_text_key(text: str) -> str:
    normalized = clean_text(text).casefold()
    normalized = re.sub(r"\s+", " ", normalized).strip()
    return normalized.strip(_EDGE_PUNCTUATION)


def _rebuild_with_clean_text(
    sample: IntentDatasetSample,
    text: str,
) -> IntentDatasetSample:
    raw = sample.model_dump(mode="json", exclude_none=True)
    raw["text"] = text
    return IntentDatasetSample.model_validate(raw)


def _load_candidates(
    path: Path,
) -> tuple[list[_Candidate], list[dict[str, Any]], int]:
    candidates: list[_Candidate] = []
    rejected: list[dict[str, Any]] = []
    input_count = 0
    with path.open("r", encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, start=1):
            if not line.strip():
                continue
            input_count += 1
            try:
                raw = json.loads(line)
            except json.JSONDecodeError as error:
                rejected.append(
                    {
                        "line_number": line_number,
                        "reason": f"invalid JSON: {error.msg}",
                        "raw": line.rstrip("\r\n"),
                    }
                )
                continue
            try:
                sample = IntentDatasetSample.model_validate(raw)
                original_text = sample.text
                cleaned_text = clean_text(sample.text)
                if not cleaned_text:
                    raise ValueError("text is empty after cleaning")
                sample = _rebuild_with_clean_text(sample, cleaned_text)
            except (ValidationError, ValueError) as error:
                rejected.append(
                    {
                        "line_number": line_number,
                        "reason": str(error),
                        "raw": raw,
                    }
                )
                continue
            candidates.append(
                _Candidate(
                    line_number=line_number,
                    original_text=original_text,
                    sample=sample,
                )
            )
    return candidates, rejected, input_count


def _preferred_candidate(candidate: _Candidate) -> tuple[int, int, int]:
    return (
        _SOURCE_PRIORITY[candidate.sample.source],
        0 if candidate.sample.is_hard_case else 1,
        candidate.line_number,
    )


def clean_dataset(
    input_path: Path,
    output_path: Path,
    conflicts_path: Path,
    rejected_path: Path,
) -> CleaningSummary:
    candidates, rejected, input_count = _load_candidates(input_path)
    groups: dict[str, list[_Candidate]] = defaultdict(list)
    for candidate in candidates:
        groups[normalized_text_key(candidate.sample.text)].append(candidate)

    cleaned: list[IntentDatasetSample] = []
    conflicts: list[dict[str, Any]] = []
    exact_duplicates = 0
    normalized_duplicates = 0

    for normalized_key, group in groups.items():
        signatures = {label_signature(item.sample.labels) for item in group}
        if len(signatures) > 1:
            conflicts.append(
                {
                    "normalized_text": normalized_key,
                    "label_sets": [list(signature) for signature in sorted(signatures)],
                    "samples": [
                        item.sample.model_dump(mode="json", exclude_none=True)
                        for item in sorted(group, key=lambda item: item.line_number)
                    ],
                }
            )
            continue

        kept = min(group, key=_preferred_candidate)
        cleaned.append(kept.sample)
        exact_texts = Counter(item.original_text for item in group)
        exact_duplicates += sum(count - 1 for count in exact_texts.values())
        normalized_duplicates += max(0, len(exact_texts) - 1)

    cleaned.sort(key=lambda sample: sample.id)
    conflicts.sort(key=lambda conflict: conflict["normalized_text"])
    write_jsonl(output_path, cleaned)
    write_jsonl(conflicts_path, conflicts)
    write_jsonl(rejected_path, rejected)
    return CleaningSummary(
        input_samples=input_count,
        cleaned_samples=len(cleaned),
        rejected_samples=len(rejected),
        exact_duplicates=exact_duplicates,
        normalized_duplicates=normalized_duplicates,
        conflict_groups=len(conflicts),
    )
