import math
import random
from collections import Counter, defaultdict
from dataclasses import dataclass
from pathlib import Path

from app.agent.foundation.intent_dataset.cleaning import normalized_text_key
from app.agent.foundation.intent_dataset.io import load_samples, write_jsonl
from app.agent.foundation.intent_dataset.schemas import (
    DatasetSource,
    IntentDatasetSample,
    TRAINABLE_INTENTS,
    label_signature,
)

_SPLIT_NAMES = ("train", "val", "test")


@dataclass(frozen=True)
class _SampleGroup:
    key: str
    samples: tuple[IntentDatasetSample, ...]
    label_counts: Counter[str]
    combination_counts: Counter[tuple[str, ...]]
    multi_count: int
    hard_count: int

    @property
    def size(self) -> int:
        return len(self.samples)

    @property
    def labels(self) -> frozenset[str]:
        return frozenset(self.label_counts)


@dataclass
class _SplitMetrics:
    size: int
    label_counts: Counter[str]
    combination_counts: Counter[tuple[str, ...]]
    multi_count: int
    hard_count: int

    @classmethod
    def from_samples(cls, samples: list[IntentDatasetSample]) -> "_SplitMetrics":
        metrics = cls(0, Counter(), Counter(), 0, 0)
        for sample in samples:
            metrics.add_sample(sample)
        return metrics

    def add_sample(self, sample: IntentDatasetSample) -> None:
        self.size += 1
        self.label_counts.update(label.name for label in sample.labels)
        self.combination_counts[label_signature(sample.labels)] += 1
        self.multi_count += int(sample.is_multi_intent)
        self.hard_count += int(sample.is_hard_case)

    def add_group(self, group: _SampleGroup) -> None:
        self.size += group.size
        self.label_counts.update(group.label_counts)
        self.combination_counts.update(group.combination_counts)
        self.multi_count += group.multi_count
        self.hard_count += group.hard_count


def _group_key(sample: IntentDatasetSample) -> str:
    if sample.provenance is not None and sample.provenance.seed_id is not None:
        return f"seed:{sample.provenance.seed_id}"
    return f"text:{normalized_text_key(sample.text)}"


def _build_groups(samples: list[IntentDatasetSample]) -> list[_SampleGroup]:
    grouped: dict[str, list[IntentDatasetSample]] = defaultdict(list)
    for sample in samples:
        grouped[_group_key(sample)].append(sample)
    return [_make_group(key, members) for key, members in grouped.items()]


def _make_group(
    key: str,
    samples: list[IntentDatasetSample] | tuple[IntentDatasetSample, ...],
) -> _SampleGroup:
    label_counts: Counter[str] = Counter()
    combinations: Counter[tuple[str, ...]] = Counter()
    for sample in samples:
        label_counts.update(label.name for label in sample.labels)
        combinations[label_signature(sample.labels)] += 1
    return _SampleGroup(
        key=key,
        samples=tuple(sorted(samples, key=lambda sample: sample.id)),
        label_counts=label_counts,
        combination_counts=combinations,
        multi_count=sum(sample.is_multi_intent for sample in samples),
        hard_count=sum(sample.is_hard_case for sample in samples),
    )


def _project_group_for_split(
    group: _SampleGroup,
    split_name: str,
) -> _SampleGroup | None:
    if split_name != "test":
        return group
    manual_samples = [
        sample for sample in group.samples if sample.source is DatasetSource.MANUAL
    ]
    if not manual_samples:
        return None
    return _make_group(group.key, manual_samples)


def _apportion(
    total: int,
    ratios: dict[str, float],
    *,
    ensure_coverage: bool = False,
) -> dict[str, int]:
    counts = {name: 0 for name in _SPLIT_NAMES}
    positive = [name for name in _SPLIT_NAMES if ratios[name] > 0]
    remaining = total
    if ensure_coverage and total >= len(positive):
        for name in positive:
            counts[name] = 1
        remaining -= len(positive)
    raw = {name: remaining * ratios[name] for name in _SPLIT_NAMES}
    floors = {name: math.floor(raw[name]) for name in _SPLIT_NAMES}
    for name in _SPLIT_NAMES:
        counts[name] += floors[name]
    leftovers = remaining - sum(floors.values())
    ranked = sorted(
        _SPLIT_NAMES,
        key=lambda name: (raw[name] - floors[name], -_SPLIT_NAMES.index(name)),
        reverse=True,
    )
    for name in ranked[:leftovers]:
        counts[name] += 1
    return counts


def _effective_size_targets(
    groups: list[_SampleGroup],
    manual_test_count: int,
    ratios: dict[str, float],
) -> dict[str, int]:
    raw_total = sum(group.size for group in groups) + manual_test_count
    if ratios["test"] == 0:
        return _apportion(raw_total, ratios)

    projected_test_samples = 0
    projected_loss = 0
    for group in groups:
        projected = _project_group_for_split(group, "test")
        if projected is None:
            continue
        projected_test_samples += projected.size
        projected_loss += group.size - projected.size
    if projected_test_samples == 0 or projected_loss == 0:
        return _apportion(raw_total, ratios)

    average_loss = projected_loss / projected_test_samples
    estimated_test_count = round(
        ratios["test"]
        * (raw_total + average_loss * manual_test_count)
        / (1 + ratios["test"] * average_loss)
    )
    estimated_loss = average_loss * max(0, estimated_test_count - manual_test_count)
    estimated_final_total = max(
        manual_test_count,
        round(raw_total - estimated_loss),
    )
    targets = _apportion(estimated_final_total, ratios)
    if targets["test"] < manual_test_count:
        shortfall = manual_test_count - targets["test"]
        targets["test"] = manual_test_count
        for split_name in ("train", "val"):
            removable = min(shortfall, targets[split_name])
            targets[split_name] -= removable
            shortfall -= removable
            if shortfall == 0:
                break
    return targets


def _validate_unique_texts(samples: list[IntentDatasetSample], context: str) -> None:
    seen: dict[str, IntentDatasetSample] = {}
    seen_ids: set[str] = set()
    for sample in samples:
        if sample.id in seen_ids:
            raise ValueError(f"{context} contains duplicate id: {sample.id}")
        seen_ids.add(sample.id)
        key = normalized_text_key(sample.text)
        existing = seen.get(key)
        if existing is not None:
            raise ValueError(
                f"{context} contains normalized duplicate texts: "
                f"{existing.id} and {sample.id}"
            )
        seen[key] = sample


def _metric_delta(current: int, target: int, increment: int) -> float:
    scale = max(target, 1)
    before = ((current - target) / scale) ** 2
    after = ((current + increment - target) / scale) ** 2
    return after - before


def _choose_for_coverage(
    groups: list[_SampleGroup],
    missing_labels: set[str],
    metrics: _SplitMetrics,
    size_target: int,
    random_order: dict[str, float],
    split_name: str,
) -> _SampleGroup | None:
    candidates = [
        group
        for group in groups
        if group.labels & missing_labels and _group_allowed_in_split(group, split_name)
    ]
    if not candidates:
        return None

    def preference(group: _SampleGroup) -> tuple[int, int, int, float]:
        projected = _project_group_for_split(group, split_name)
        if projected is None:
            raise AssertionError("coverage candidate must be eligible for split")
        covers = len(projected.labels & missing_labels)
        co_covers_boundary = int(metrics.multi_count == 0 and projected.multi_count > 0)
        co_covers_hard = int(metrics.hard_count == 0 and projected.hard_count > 0)
        overflow = max(0, metrics.size + projected.size - size_target)
        return (
            covers,
            co_covers_boundary + co_covers_hard,
            -overflow,
            random_order[group.key],
        )

    return max(candidates, key=preference)


def _group_allowed_in_split(group: _SampleGroup, split_name: str) -> bool:
    return _project_group_for_split(group, split_name) is not None


def _assign_group(
    group: _SampleGroup,
    split_name: str,
    split_samples: dict[str, list[IntentDatasetSample]],
    metrics: dict[str, _SplitMetrics],
) -> int:
    projected = _project_group_for_split(group, split_name)
    if projected is None:
        raise ValueError(f"group {group.key} is not eligible for {split_name}")
    split_samples[split_name].extend(projected.samples)
    metrics[split_name].add_group(projected)
    return group.size - projected.size


def _assignment_cost(
    group: _SampleGroup,
    metrics: _SplitMetrics,
    size_target: int,
    label_targets: dict[str, int],
    combination_targets: dict[tuple[str, ...], int],
    multi_target: int,
    hard_target: int,
) -> float:
    cost = 8.0 * _metric_delta(metrics.size, size_target, group.size)
    for label, increment in group.label_counts.items():
        cost += 3.0 * _metric_delta(
            metrics.label_counts[label],
            label_targets[label],
            increment,
        )
    for combination, increment in group.combination_counts.items():
        cost += 0.5 * _metric_delta(
            metrics.combination_counts[combination],
            combination_targets[combination],
            increment,
        )
    cost += _metric_delta(metrics.multi_count, multi_target, group.multi_count)
    cost += _metric_delta(metrics.hard_count, hard_target, group.hard_count)
    overflow = max(0, metrics.size + group.size - size_target)
    cost += 4.0 * overflow / max(size_target, 1)
    return cost


def _assert_no_leakage(split_samples: dict[str, list[IntentDatasetSample]]) -> None:
    text_splits: dict[str, set[str]] = defaultdict(set)
    seed_splits: dict[str, set[str]] = defaultdict(set)
    for split_name, samples in split_samples.items():
        for sample in samples:
            text_splits[normalized_text_key(sample.text)].add(split_name)
            if sample.provenance is not None and sample.provenance.seed_id is not None:
                seed_splits[sample.provenance.seed_id].add(split_name)
    leaked_texts = [key for key, splits in text_splits.items() if len(splits) > 1]
    leaked_seeds = [key for key, splits in seed_splits.items() if len(splits) > 1]
    if leaked_texts or leaked_seeds:
        raise ValueError(
            "split leakage detected: "
            f"normalized_texts={leaked_texts}, provenance_seeds={leaked_seeds}"
        )


def split_dataset(
    input_path: Path,
    output_dir: Path,
    *,
    train_ratio: float = 0.8,
    val_ratio: float = 0.1,
    test_ratio: float = 0.1,
    random_seed: int = 42,
    manual_test_path: Path | None = None,
) -> dict[str, object]:
    ratios = {
        "train": train_ratio,
        "val": val_ratio,
        "test": test_ratio,
    }
    if any(ratio < 0 for ratio in ratios.values()):
        raise ValueError("split ratios must not be negative")
    if not math.isclose(sum(ratios.values()), 1.0, abs_tol=1e-9):
        raise ValueError("split ratios must sum to 1.0")
    if train_ratio == 0:
        raise ValueError("train ratio must be greater than zero")

    input_samples = load_samples(input_path)
    _validate_unique_texts(input_samples, "cleaned dataset")
    manual_test = (
        load_samples(manual_test_path)
        if manual_test_path is not None and manual_test_path.exists()
        else []
    )
    _validate_unique_texts(manual_test, "manual test dataset")
    if any(sample.source is not DatasetSource.MANUAL for sample in manual_test):
        raise ValueError("manual test dataset may only contain source=manual samples")

    manual_texts = {normalized_text_key(sample.text): sample for sample in manual_test}
    manual_seed_ids = {
        sample.provenance.seed_id
        for sample in manual_test
        if sample.provenance is not None and sample.provenance.seed_id is not None
    }
    pool: list[IntentDatasetSample] = []
    for sample in input_samples:
        key = normalized_text_key(sample.text)
        if key in manual_texts:
            manual_labels = label_signature(manual_texts[key].labels)
            if label_signature(sample.labels) != manual_labels:
                raise ValueError(
                    f"manual test label conflict for normalized text: {sample.text}"
                )
            continue
        seed_id = sample.provenance.seed_id if sample.provenance is not None else None
        if seed_id is not None and seed_id in manual_seed_ids:
            continue
        pool.append(sample)

    ids = [sample.id for sample in [*pool, *manual_test]]
    if len(ids) != len(set(ids)):
        raise ValueError("dataset and manual test data contain duplicate ids")

    groups = _build_groups(pool)
    random_generator = random.Random(random_seed)
    random_order = {group.key: random_generator.random() for group in groups}
    label_group_frequency = Counter(
        label for group in groups for label in group.labels
    )
    groups.sort(
        key=lambda group: (
            min(label_group_frequency[label] for label in group.labels),
            -len(group.labels),
            -group.hard_count,
            random_order[group.key],
        )
    )

    all_samples = [*pool, *manual_test]
    size_targets = _effective_size_targets(groups, len(manual_test), ratios)
    total_labels = Counter(
        label.name for sample in all_samples for label in sample.labels
    )
    total_combinations = Counter(label_signature(sample.labels) for sample in all_samples)
    total_multi = sum(sample.is_multi_intent for sample in all_samples)
    total_hard = sum(sample.is_hard_case for sample in all_samples)
    label_targets = {
        split_name: {
            label.name: _apportion(
                total_labels[label.name], ratios, ensure_coverage=True
            )[split_name]
            for label in TRAINABLE_INTENTS
        }
        for split_name in _SPLIT_NAMES
    }
    combination_targets = {
        split_name: {
            combination: _apportion(count, ratios)[split_name]
            for combination, count in total_combinations.items()
        }
        for split_name in _SPLIT_NAMES
    }
    multi_targets = _apportion(total_multi, ratios, ensure_coverage=True)
    hard_targets = _apportion(total_hard, ratios, ensure_coverage=True)

    split_samples = {"train": [], "val": [], "test": list(manual_test)}
    metrics = {
        name: _SplitMetrics.from_samples(split_samples[name]) for name in _SPLIT_NAMES
    }
    unassigned = list(groups)
    excluded_derived_samples = 0

    labels_with_enough_groups = {
        label.name
        for label in TRAINABLE_INTENTS
        if sum(label.name in group.labels for group in groups)
        + int(any(label in sample.labels for sample in manual_test))
        >= sum(ratio > 0 for ratio in ratios.values())
    }
    for split_name in ("test", "val", "train"):
        if ratios[split_name] == 0:
            continue
        missing = labels_with_enough_groups - set(metrics[split_name].label_counts)
        while missing:
            selected = _choose_for_coverage(
                unassigned,
                missing,
                metrics[split_name],
                size_targets[split_name],
                random_order,
                split_name,
            )
            if selected is None:
                break
            excluded_derived_samples += _assign_group(
                selected,
                split_name,
                split_samples,
                metrics,
            )
            unassigned.remove(selected)
            missing = labels_with_enough_groups - set(metrics[split_name].label_counts)

    if total_multi >= sum(ratio > 0 for ratio in ratios.values()):
        for split_name in ("test", "val", "train"):
            if ratios[split_name] == 0 or metrics[split_name].multi_count > 0:
                continue
            candidates = [
                group
                for group in unassigned
                if group.multi_count > 0
                and _group_allowed_in_split(group, split_name)
            ]
            if candidates:
                selected = min(
                    candidates,
                    key=lambda group: (
                        max(
                            0,
                            metrics[split_name].size
                            + _project_group_for_split(group, split_name).size
                            - size_targets[split_name],
                        ),
                        random_order[group.key],
                    ),
                )
                excluded_derived_samples += _assign_group(
                    selected,
                    split_name,
                    split_samples,
                    metrics,
                )
                unassigned.remove(selected)

    if total_hard >= sum(ratio > 0 for ratio in ratios.values()):
        for split_name in ("test", "val", "train"):
            if ratios[split_name] == 0 or metrics[split_name].hard_count > 0:
                continue
            candidates = [
                group
                for group in unassigned
                if group.hard_count > 0
                and _group_allowed_in_split(group, split_name)
            ]
            if candidates:
                selected = min(
                    candidates,
                    key=lambda group: (
                        max(
                            0,
                            metrics[split_name].size
                            + _project_group_for_split(group, split_name).size
                            - size_targets[split_name],
                        ),
                        random_order[group.key],
                    ),
                )
                excluded_derived_samples += _assign_group(
                    selected,
                    split_name,
                    split_samples,
                    metrics,
                )
                unassigned.remove(selected)

    for group in unassigned:
        eligible_splits = [
            name
            for name in _SPLIT_NAMES
            if ratios[name] > 0 and _group_allowed_in_split(group, name)
        ]
        selected_name = min(
            eligible_splits,
            key=lambda split_name: (
                _assignment_cost(
                    _project_group_for_split(group, split_name),
                    metrics[split_name],
                    size_targets[split_name],
                    label_targets[split_name],
                    combination_targets[split_name],
                    multi_targets[split_name],
                    hard_targets[split_name],
                ),
                random_order[group.key] + _SPLIT_NAMES.index(split_name) / 10,
            ),
        )
        excluded_derived_samples += _assign_group(
            group,
            selected_name,
            split_samples,
            metrics,
        )

    for samples in split_samples.values():
        samples.sort(key=lambda sample: sample.id)
    _assert_no_leakage(split_samples)
    output_dir.mkdir(parents=True, exist_ok=True)
    for split_name, samples in split_samples.items():
        write_jsonl(output_dir / f"{split_name}.jsonl", samples)

    return {
        "seed": random_seed,
        "ratios": ratios,
        "counts": {name: len(split_samples[name]) for name in _SPLIT_NAMES},
        "manual_test_samples": len(manual_test),
        "excluded_derived_samples": excluded_derived_samples,
        "group_count": len(groups),
    }
