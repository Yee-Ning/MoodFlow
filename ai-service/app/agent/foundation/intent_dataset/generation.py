import random
from collections.abc import Iterable, Sequence
from pathlib import Path
from typing import Any, Protocol

from app.agent.foundation.intent_dataset.config import load_taxonomy
from app.agent.foundation.intent_dataset.io import load_samples, write_jsonl
from app.agent.foundation.intent_dataset.schemas import (
    DatasetProvenance,
    DatasetSource,
    IntentDatasetSample,
)
from app.agent.foundation.routing import IntentType


class SyntheticExpansionAdapter(Protocol):
    """Provider-neutral contract for raw-only synthetic expansion."""

    def expand(
        self,
        sample: IntentDatasetSample,
        taxonomy: dict[str, Any],
        *,
        variants: int,
        random_generator: random.Random,
    ) -> Iterable[IntentDatasetSample]: ...


class DeterministicSeedExpansionAdapter:
    """Create a small set of conservative surface-form variants without an LLM."""

    @staticmethod
    def _candidate_texts(sample: IntentDatasetSample) -> list[str]:
        text = sample.text
        label_set = set(sample.labels)
        if label_set == {IntentType.GENERAL_CHAT}:
            if text == "你好":
                candidates = ["你好呀", "你好！", "嗨，你好"]
            elif text == "谢谢":
                candidates = ["谢谢你", "多谢啦", "感谢你"]
            elif text.startswith("晚上好"):
                candidates = [f"{text}！", f"{text}呀", f"嗨，{text}"]
            elif text.startswith("聊聊"):
                candidates = [
                    f"我们来{text}",
                    f"想{text}",
                    f"{text}，你怎么看",
                ]
            else:
                candidates = [
                    f"想和你聊聊，{text}",
                    f"{text}，你怎么看",
                    f"我们聊聊：{text}",
                ]
        elif label_set == {IntentType.WORLDLINE}:
            candidates = [
                f"请问，{text}",
                f"请设想一下，{text}",
                f"{text}呢？",
            ]
        else:
            candidates = [
                f"麻烦你，{text}",
                f"{text}，可以吗",
                f"请处理这个请求：{text}",
            ]
        if sample.is_multi_intent and "然后" in text:
            candidates.append(text.replace("然后", "，再", 1))
        return [candidate for candidate in candidates if candidate != text]

    def expand(
        self,
        sample: IntentDatasetSample,
        taxonomy: dict[str, Any],
        *,
        variants: int,
        random_generator: random.Random,
    ) -> Iterable[IntentDatasetSample]:
        del taxonomy
        candidates = self._candidate_texts(sample)
        random_generator.shuffle(candidates)
        for index, text in enumerate(candidates[:variants], start=1):
            yield IntentDatasetSample(
                id=f"syn-{sample.id}-{index:02d}",
                text=text,
                labels=sample.labels,
                source=DatasetSource.SYNTHETIC,
                is_hard_case=sample.is_hard_case,
                provenance=DatasetProvenance(
                    seed_id=sample.id,
                    generation_method="deterministic_surface_expansion_v1",
                ),
            )


def _validated_supplemental_samples(
    paths: Sequence[Path],
    expected_source: DatasetSource,
) -> list[IntentDatasetSample]:
    samples: list[IntentDatasetSample] = []
    for path in paths:
        for sample in load_samples(path):
            if sample.source is not expected_source:
                raise ValueError(
                    f"{path}: sample {sample.id} must have source={expected_source.value}"
                )
            samples.append(sample)
    return samples


def generate_dataset(
    input_path: Path,
    output_path: Path,
    taxonomy_path: Path,
    *,
    random_seed: int = 42,
    variants_per_seed: int = 1,
    synthetic_input_paths: Sequence[Path] = (),
    rule_bootstrap_input_paths: Sequence[Path] = (),
    adapter: SyntheticExpansionAdapter | None = None,
) -> dict[str, int]:
    if variants_per_seed < 0:
        raise ValueError("variants_per_seed must not be negative")

    taxonomy = load_taxonomy(taxonomy_path)
    manual_samples = load_samples(input_path)
    for sample in manual_samples:
        if sample.source is not DatasetSource.MANUAL:
            raise ValueError(f"manual seed {sample.id} must have source=manual")

    expansion_adapter = adapter or DeterministicSeedExpansionAdapter()
    random_generator = random.Random(random_seed)
    expanded_samples = [
        generated
        for sample in manual_samples
        for generated in expansion_adapter.expand(
            sample,
            taxonomy,
            variants=variants_per_seed,
            random_generator=random_generator,
        )
    ]
    supplemental_synthetic = _validated_supplemental_samples(
        synthetic_input_paths,
        DatasetSource.SYNTHETIC,
    )
    rule_bootstrap = _validated_supplemental_samples(
        rule_bootstrap_input_paths,
        DatasetSource.RULE_BOOTSTRAP,
    )
    output_samples = [
        *manual_samples,
        *expanded_samples,
        *supplemental_synthetic,
        *rule_bootstrap,
    ]
    ids = [sample.id for sample in output_samples]
    if len(ids) != len(set(ids)):
        raise ValueError("generated dataset contains duplicate sample ids")

    write_jsonl(output_path, output_samples)
    return {
        "manual": len(manual_samples),
        "synthetic": len(expanded_samples) + len(supplemental_synthetic),
        "rule_bootstrap": len(rule_bootstrap),
        "total": len(output_samples),
    }
