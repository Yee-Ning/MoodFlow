from collections.abc import Sequence
from pathlib import Path
from typing import Any

import numpy as np
from sklearn.metrics import precision_recall_fscore_support

from app.agent.foundation.intent_dataset.io import write_jsonl


def threshold_vector(
    label_mapping: dict[str, int],
    thresholds: float | dict[str, float],
) -> np.ndarray:
    if isinstance(thresholds, (float, int)):
        values = np.full(len(label_mapping), float(thresholds), dtype=np.float32)
    else:
        missing = set(label_mapping) - set(thresholds)
        extra = set(thresholds) - set(label_mapping)
        if missing or extra:
            raise ValueError(f"threshold labels mismatch: missing={missing}, extra={extra}")
        values = np.zeros(len(label_mapping), dtype=np.float32)
        for label, index in label_mapping.items():
            values[index] = thresholds[label]
    if np.any((values <= 0) | (values >= 1)):
        raise ValueError("thresholds must be in (0, 1)")
    return values


def apply_thresholds(
    probabilities: np.ndarray,
    label_mapping: dict[str, int],
    thresholds: float | dict[str, float],
) -> np.ndarray:
    values = np.asarray(probabilities, dtype=np.float32)
    if values.shape[-1] != len(label_mapping):
        raise ValueError("probability shape does not match label_mapping")
    return (values >= threshold_vector(label_mapping, thresholds)).astype(np.int64)


def accepted_labels(
    probabilities: Sequence[float] | np.ndarray,
    label_mapping: dict[str, int],
    thresholds: float | dict[str, float],
) -> list[str]:
    predictions = apply_thresholds(
        np.asarray(probabilities, dtype=np.float32),
        label_mapping,
        thresholds,
    )
    ordered = sorted(label_mapping.items(), key=lambda item: item[1])
    return [label for label, index in ordered if predictions[index] == 1]


def compute_multilabel_metrics(
    y_true: np.ndarray,
    probabilities: np.ndarray,
    label_mapping: dict[str, int],
    thresholds: float | dict[str, float],
) -> dict[str, Any]:
    true_values = np.asarray(y_true, dtype=np.int64)
    predictions = apply_thresholds(probabilities, label_mapping, thresholds)
    if true_values.shape != predictions.shape:
        raise ValueError("truth and prediction shapes must match")

    micro = precision_recall_fscore_support(
        true_values,
        predictions,
        average="micro",
        zero_division=0,
    )
    macro = precision_recall_fscore_support(
        true_values,
        predictions,
        average="macro",
        zero_division=0,
    )
    sample = precision_recall_fscore_support(
        true_values,
        predictions,
        average="samples",
        zero_division=0,
    )
    per_label = precision_recall_fscore_support(
        true_values,
        predictions,
        average=None,
        zero_division=0,
    )
    ordered = sorted(label_mapping.items(), key=lambda item: item[1])
    return {
        "precision": float(micro[0]),
        "recall": float(micro[1]),
        "micro_f1": float(micro[2]),
        "macro_f1": float(macro[2]),
        "sample_f1": float(sample[2]),
        "exact_match_ratio": float(np.all(true_values == predictions, axis=1).mean()),
        "per_label": {
            label: {
                "precision": float(per_label[0][index]),
                "recall": float(per_label[1][index]),
                "f1": float(per_label[2][index]),
                "support": int(per_label[3][index]),
            }
            for label, index in ordered
        },
    }


def build_error_analysis(
    records: list[dict[str, Any]],
    y_true: np.ndarray,
    probabilities: np.ndarray,
    label_mapping: dict[str, int],
    thresholds: dict[str, float],
) -> list[dict[str, Any]]:
    predictions = apply_thresholds(probabilities, label_mapping, thresholds)
    ordered = sorted(label_mapping.items(), key=lambda item: item[1])
    errors: list[dict[str, Any]] = []
    for row_index, record in enumerate(records):
        true_labels = [
            label for label, index in ordered if y_true[row_index, index] == 1
        ]
        predicted_labels = [
            label for label, index in ordered if predictions[row_index, index] == 1
        ]
        if true_labels == predicted_labels:
            continue
        true_set = set(true_labels)
        predicted_set = set(predicted_labels)
        errors.append(
            {
                "id": record["id"],
                "text": record["text"],
                "true_labels": true_labels,
                "predicted_labels": predicted_labels,
                "scores": {
                    label: float(probabilities[row_index, index])
                    for label, index in ordered
                },
                "false_positive_labels": sorted(predicted_set - true_set),
                "false_negative_labels": sorted(true_set - predicted_set),
                "is_multi_intent_miss": len(true_labels) > 1,
                "is_hard_case": bool(record["is_hard_case"]),
            }
        )
    return sorted(
        errors,
        key=lambda item: (
            not item["is_hard_case"],
            not item["is_multi_intent_miss"],
            item["id"],
        ),
    )


def write_error_analysis(path: Path, errors: list[dict[str, Any]]) -> None:
    write_jsonl(path, errors)
