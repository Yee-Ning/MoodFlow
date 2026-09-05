from pathlib import Path
from typing import Any

import torch
from torch.utils.data import Dataset

from app.agent.foundation.intent_dataset.io import load_samples
from app.agent.foundation.intent_dataset.schemas import IntentDatasetSample
from app.agent.foundation.routing import IntentType


def encode_multi_hot(
    labels: list[IntentType] | list[str],
    label_mapping: dict[str, int],
) -> torch.Tensor:
    vector = torch.zeros(len(label_mapping), dtype=torch.float32)
    for raw_label in labels:
        label_name = raw_label.name if isinstance(raw_label, IntentType) else raw_label
        if label_name == IntentType.UNKNOWN.name:
            raise ValueError("UNKNOWN is not a trainable label")
        if label_name not in label_mapping:
            raise ValueError(f"label is missing from label_mapping: {label_name}")
        vector[label_mapping[label_name]] = 1.0
    return vector


class IntentClassificationDataset(Dataset[dict[str, Any]]):
    def __init__(
        self,
        path: Path,
        tokenizer: Any,
        label_mapping: dict[str, int],
        *,
        max_length: int,
    ) -> None:
        if max_length <= 0:
            raise ValueError("max_length must be positive")
        self.samples = load_samples(path)
        self.tokenizer = tokenizer
        self.label_mapping = label_mapping
        self.max_length = max_length

    def __len__(self) -> int:
        return len(self.samples)

    def __getitem__(self, index: int) -> dict[str, Any]:
        sample = self.samples[index]
        encoded = self.tokenizer(
            sample.text,
            truncation=True,
            padding="max_length",
            max_length=self.max_length,
            return_tensors="pt",
        )
        item: dict[str, Any] = {
            key: value.squeeze(0)
            for key, value in encoded.items()
            if isinstance(value, torch.Tensor)
        }
        item.update(
            {
                "labels": encode_multi_hot(sample.labels, self.label_mapping),
                "sample_id": sample.id,
                "text": sample.text,
                "is_hard_case": sample.is_hard_case,
                "is_multi_intent": sample.is_multi_intent,
            }
        )
        return item

    def sample_at(self, index: int) -> IntentDatasetSample:
        return self.samples[index]
