from collections.abc import Sequence
from typing import Any

import numpy as np
from sklearn.metrics import f1_score

from app.agent.foundation.intent_model.metrics import compute_multilabel_metrics


def threshold_grid(step: float = 0.05) -> tuple[float, ...]:
    if not 0 < step < 1:
        raise ValueError("threshold step must be in (0, 1)")
    values = np.arange(0.1, 0.900001, step)
    return tuple(round(float(value), 4) for value in values)


def _best_candidate(scores: list[tuple[float, float]]) -> tuple[float, float]:
    return max(scores, key=lambda item: (item[1], -abs(item[0] - 0.5), -item[0]))


def tune_thresholds(
    y_true: np.ndarray,
    probabilities: np.ndarray,
    label_mapping: dict[str, int],
    *,
    candidates: Sequence[float] | None = None,
) -> dict[str, Any]:
    grid = tuple(candidates or threshold_grid())
    if not grid:
        raise ValueError("threshold candidate grid must not be empty")

    global_scores = [
        (
            threshold,
            compute_multilabel_metrics(
                y_true,
                probabilities,
                label_mapping,
                threshold,
            )["macro_f1"],
        )
        for threshold in grid
    ]
    global_threshold, global_macro_f1 = _best_candidate(global_scores)

    per_label_thresholds: dict[str, float] = {}
    per_label_f1: dict[str, float] = {}
    for label, index in sorted(label_mapping.items(), key=lambda item: item[1]):
        label_scores = [
            (
                threshold,
                float(
                    f1_score(
                        y_true[:, index],
                        probabilities[:, index] >= threshold,
                        zero_division=0,
                    )
                ),
            )
            for threshold in grid
        ]
        best_threshold, best_f1 = _best_candidate(label_scores)
        per_label_thresholds[label] = best_threshold
        per_label_f1[label] = best_f1

    tuned_metrics = compute_multilabel_metrics(
        y_true,
        probabilities,
        label_mapping,
        per_label_thresholds,
    )
    return {
        "objective": "macro_f1",
        "global_threshold": global_threshold,
        "global_macro_f1": global_macro_f1,
        "per_label_thresholds": per_label_thresholds,
        "per_label_f1": per_label_f1,
        "tuned_metrics": tuned_metrics,
    }
