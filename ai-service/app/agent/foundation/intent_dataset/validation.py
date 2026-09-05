import json
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from app.agent.foundation.intent_dataset.cleaning import normalized_text_key
from app.agent.foundation.intent_dataset.config import load_label_mapping, load_taxonomy
from app.agent.foundation.intent_dataset.schemas import IntentDatasetSample, TRAINABLE_INTENTS


def _read_validated(path: Path) -> tuple[list[IntentDatasetSample], list[str]]:
    samples: list[IntentDatasetSample] = []
    errors: list[str] = []
    if not path.exists():
        errors.append(f"missing file: {path}")
        return samples, errors
    with path.open("r", encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, start=1):
            if not line.strip():
                continue
            try:
                raw = json.loads(line)
                samples.append(IntentDatasetSample.model_validate(raw))
            except (json.JSONDecodeError, ValidationError) as error:
                errors.append(f"{path}:{line_number}: {error}")
    return samples, errors


def _sample_stats(samples: list[IntentDatasetSample]) -> dict[str, Any]:
    intent_counts = Counter(
        label.name for sample in samples for label in sample.labels
    )
    source_counts = Counter(sample.source.value for sample in samples)
    exact_counts = Counter(sample.text for sample in samples)
    normalized_counts = Counter(normalized_text_key(sample.text) for sample in samples)
    exact_duplicates = sum(count - 1 for count in exact_counts.values() if count > 1)
    normalized_duplicates = sum(
        count - 1 for count in normalized_counts.values() if count > 1
    )
    return {
        "total_samples": len(samples),
        "samples_per_intent": {
            intent.name: intent_counts[intent.name] for intent in TRAINABLE_INTENTS
        },
        "single_label_samples": sum(len(sample.labels) == 1 for sample in samples),
        "multi_label_samples": sum(sample.is_multi_intent for sample in samples),
        "hard_case_samples": sum(sample.is_hard_case for sample in samples),
        "source_distribution": dict(sorted(source_counts.items())),
        "exact_duplicate_count": exact_duplicates,
        "normalized_duplicate_count": normalized_duplicates,
    }


def _count_jsonl_records(path: Path) -> int:
    if not path.exists():
        return 0
    with path.open("r", encoding="utf-8") as handle:
        return sum(bool(line.strip()) for line in handle)


def validate_dataset_artifacts(
    cleaned_path: Path,
    splits_dir: Path,
    conflicts_path: Path,
    taxonomy_path: Path,
    label_mapping_path: Path,
    rejected_path: Path | None = None,
) -> dict[str, Any]:
    errors: list[str] = []
    warnings: list[str] = []
    try:
        load_taxonomy(taxonomy_path)
    except (OSError, json.JSONDecodeError, ValueError) as error:
        errors.append(f"taxonomy: {error}")
    try:
        mapping = load_label_mapping(label_mapping_path)
    except (OSError, json.JSONDecodeError, ValueError) as error:
        mapping = {}
        errors.append(f"label_mapping: {error}")

    cleaned, cleaned_errors = _read_validated(cleaned_path)
    errors.extend(cleaned_errors)
    split_samples: dict[str, list[IntentDatasetSample]] = {}
    split_summaries: dict[str, Any] = {}
    for split_name in ("train", "val", "test"):
        samples, split_errors = _read_validated(splits_dir / f"{split_name}.jsonl")
        split_samples[split_name] = samples
        errors.extend(split_errors)
        split_summaries[split_name] = _sample_stats(samples)
        split_summaries[split_name]["invalid_samples"] = len(split_errors)

    cleaned_stats = _sample_stats(cleaned)
    rejected_count = _count_jsonl_records(rejected_path) if rejected_path else 0
    conflict_count = _count_jsonl_records(conflicts_path)
    cleaned_stats["invalid_samples"] = len(cleaned_errors) + rejected_count
    cleaned_stats["conflicts"] = conflict_count
    if rejected_count:
        warnings.append(f"rejected samples awaiting review: {rejected_count}")
    if conflict_count:
        warnings.append(f"label conflicts awaiting review: {conflict_count}")

    text_locations: dict[str, set[str]] = defaultdict(set)
    seed_locations: dict[str, set[str]] = defaultdict(set)
    for split_name, samples in split_samples.items():
        for sample in samples:
            text_locations[normalized_text_key(sample.text)].add(split_name)
            if sample.provenance is not None and sample.provenance.seed_id is not None:
                seed_locations[sample.provenance.seed_id].add(split_name)
    leaked_text_count = sum(len(locations) > 1 for locations in text_locations.values())
    leaked_seed_count = sum(len(locations) > 1 for locations in seed_locations.values())
    if leaked_text_count:
        errors.append(f"normalized text leakage across splits: {leaked_text_count}")
    if leaked_seed_count:
        errors.append(f"provenance seed leakage across splits: {leaked_seed_count}")

    train_counts = split_summaries["train"]["samples_per_intent"]
    for intent in TRAINABLE_INTENTS:
        if train_counts[intent.name] == 0:
            warnings.append(f"{intent.name} has no train samples")
            continue
        for split_name in ("val", "test"):
            if split_summaries[split_name]["samples_per_intent"][intent.name] == 0:
                warnings.append(
                    f"{intent.name} exists in train but has no {split_name} samples"
                )

    split_ids = [sample.id for samples in split_samples.values() for sample in samples]
    duplicate_ids = len(split_ids) - len(set(split_ids))
    if duplicate_ids:
        errors.append(f"duplicate sample ids across splits: {duplicate_ids}")
    split_id_set = set(split_ids)
    split_text_keys = set(text_locations)
    split_seed_ids = set(seed_locations)
    missing_cleaned = [
        sample
        for sample in cleaned
        if sample.id not in split_id_set
        and normalized_text_key(sample.text) not in split_text_keys
        and (
            sample.provenance is None
            or sample.provenance.seed_id is None
            or sample.provenance.seed_id not in split_seed_ids
        )
    ]
    if missing_cleaned:
        errors.append(
            f"cleaned samples missing from splits: {len(missing_cleaned)}"
        )

    return {
        "valid": not errors,
        "label_mapping": mapping,
        "dataset": cleaned_stats,
        "splits": split_summaries,
        "leakage": {
            "normalized_texts_across_splits": leaked_text_count,
            "provenance_seeds_across_splits": leaked_seed_count,
        },
        "errors": errors,
        "warnings": warnings,
    }
