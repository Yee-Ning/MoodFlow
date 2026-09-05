import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import torch
from transformers import AutoTokenizer

from app.agent.foundation.intent_dataset.config import load_label_mapping
from app.agent.foundation.intent_model.config import TrainingConfig, resolve_device
from app.agent.foundation.intent_model.metrics import threshold_vector
from app.agent.foundation.intent_model.model import BgeMultiLabelClassifier


@dataclass(frozen=True)
class LoadedModelBundle:
    model: BgeMultiLabelClassifier
    tokenizer: Any
    label_mapping: dict[str, int]
    thresholds: dict[str, float]
    training_config: TrainingConfig
    device: torch.device


def _write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary_path = path.with_suffix(f"{path.suffix}.tmp")
    with temporary_path.open("w", encoding="utf-8", newline="\n") as handle:
        json.dump(value, handle, ensure_ascii=False, indent=2)
        handle.write("\n")
    temporary_path.replace(path)


def _read_json(path: Path) -> Any:
    with path.open("r", encoding="utf-8") as handle:
        return json.load(handle)


def save_model_bundle(
    output_dir: Path,
    *,
    model: BgeMultiLabelClassifier,
    tokenizer: Any,
    label_mapping: dict[str, int],
    thresholds: dict[str, float],
    training_config: TrainingConfig,
    metrics: dict[str, Any],
) -> None:
    """Save one self-contained encoder/head/config bundle for later inference."""

    threshold_vector(label_mapping, thresholds)
    output_dir.mkdir(parents=True, exist_ok=True)
    encoder_dir = output_dir / "encoder"
    encoder_dir.mkdir(parents=True, exist_ok=True)
    model.encoder.save_pretrained(encoder_dir)
    tokenizer.save_pretrained(encoder_dir)

    head_path = output_dir / "classifier_head.pt"
    temporary_head_path = output_dir / "classifier_head.pt.tmp"
    torch.save(model.classifier.state_dict(), temporary_head_path)
    temporary_head_path.replace(head_path)

    _write_json(output_dir / "label_mapping.json", label_mapping)
    _write_json(output_dir / "thresholds.json", thresholds)
    _write_json(output_dir / "training_config.json", training_config.as_dict())
    _write_json(output_dir / "metrics.json", metrics)


def load_model_bundle(
    model_dir: Path,
    *,
    device: str = "auto",
) -> LoadedModelBundle:
    """Load a saved bundle without consulting the original Hugging Face model."""

    resolved_device = resolve_device(device)
    label_mapping = load_label_mapping(model_dir / "label_mapping.json")
    raw_thresholds = _read_json(model_dir / "thresholds.json")
    if not isinstance(raw_thresholds, dict):
        raise ValueError("thresholds.json must contain a label-to-threshold object")
    thresholds = {str(label): float(value) for label, value in raw_thresholds.items()}
    threshold_vector(label_mapping, thresholds)

    raw_config = _read_json(model_dir / "training_config.json")
    if not isinstance(raw_config, dict):
        raise ValueError("training_config.json must contain an object")
    training_config = TrainingConfig.from_dict(raw_config)

    encoder_dir = model_dir / "encoder"
    tokenizer = AutoTokenizer.from_pretrained(encoder_dir, local_files_only=True)
    model = BgeMultiLabelClassifier.from_pretrained(
        str(encoder_dir),
        num_labels=len(label_mapping),
        dropout=training_config.dropout,
        pooling=training_config.pooling,
    )
    head_state = torch.load(
        model_dir / "classifier_head.pt",
        map_location=resolved_device,
        weights_only=True,
    )
    model.classifier.load_state_dict(head_state)
    model.to(resolved_device)
    model.eval()
    return LoadedModelBundle(
        model=model,
        tokenizer=tokenizer,
        label_mapping=label_mapping,
        thresholds=thresholds,
        training_config=training_config,
        device=resolved_device,
    )
