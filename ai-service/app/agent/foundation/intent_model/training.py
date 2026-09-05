from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np
import torch
from torch import nn
from torch.optim import AdamW
from torch.utils.data import DataLoader
from transformers import AutoTokenizer

from app.agent.foundation.intent_dataset.config import load_label_mapping
from app.agent.foundation.intent_model.artifacts import (
    LoadedModelBundle,
    load_model_bundle,
    save_model_bundle,
)
from app.agent.foundation.intent_model.config import (
    TrainingConfig,
    resolve_device,
    set_random_seed,
)
from app.agent.foundation.intent_model.dataset import IntentClassificationDataset
from app.agent.foundation.intent_model.metrics import (
    apply_thresholds,
    build_error_analysis,
    compute_multilabel_metrics,
    write_error_analysis,
)
from app.agent.foundation.intent_model.model import BgeMultiLabelClassifier
from app.agent.foundation.intent_model.thresholds import threshold_grid, tune_thresholds


@dataclass(frozen=True)
class EvaluationOutput:
    loss: float
    y_true: np.ndarray
    probabilities: np.ndarray
    records: list[dict[str, Any]]


def _model_inputs(batch: dict[str, Any], device: torch.device) -> dict[str, torch.Tensor]:
    return {
        key: value.to(device)
        for key, value in batch.items()
        if key in {"input_ids", "attention_mask", "token_type_ids"}
        and isinstance(value, torch.Tensor)
    }


def evaluate_loader(
    model: BgeMultiLabelClassifier,
    loader: DataLoader[dict[str, Any]],
    *,
    device: torch.device,
) -> EvaluationOutput:
    criterion = nn.BCEWithLogitsLoss()
    losses: list[float] = []
    truths: list[np.ndarray] = []
    probabilities: list[np.ndarray] = []
    records: list[dict[str, Any]] = []
    model.eval()
    with torch.no_grad():
        for batch in loader:
            labels = batch["labels"].to(device)
            logits = model(**_model_inputs(batch, device))
            loss = criterion(logits, labels)
            losses.extend([float(loss.item())] * labels.shape[0])
            truths.append(labels.detach().cpu().numpy())
            probabilities.append(torch.sigmoid(logits).detach().cpu().numpy())
            for index in range(labels.shape[0]):
                records.append(
                    {
                        "id": batch["sample_id"][index],
                        "text": batch["text"][index],
                        "is_hard_case": bool(batch["is_hard_case"][index]),
                        "is_multi_intent": bool(batch["is_multi_intent"][index]),
                    }
                )
    if not truths:
        raise ValueError("cannot evaluate an empty dataset")
    return EvaluationOutput(
        loss=float(np.mean(losses)),
        y_true=np.concatenate(truths, axis=0),
        probabilities=np.concatenate(probabilities, axis=0),
        records=records,
    )


def _subset_metrics(
    output: EvaluationOutput,
    label_mapping: dict[str, int],
    thresholds: dict[str, float],
    record_key: str,
) -> dict[str, Any]:
    indices = [index for index, record in enumerate(output.records) if record[record_key]]
    if not indices:
        return {"samples": 0, "errors": 0, "metrics": None}
    subset_true = output.y_true[indices]
    subset_probabilities = output.probabilities[indices]
    subset_predictions = apply_thresholds(
        subset_probabilities, label_mapping, thresholds
    )
    return {
        "samples": len(indices),
        "errors": int(np.any(subset_true != subset_predictions, axis=1).sum()),
        "metrics": compute_multilabel_metrics(
            subset_true,
            subset_probabilities,
            label_mapping,
            thresholds,
        ),
    }


def _evaluation_summary(
    output: EvaluationOutput,
    label_mapping: dict[str, int],
    thresholds: dict[str, float],
) -> dict[str, Any]:
    return {
        "samples": len(output.records),
        "loss": output.loss,
        "metrics": compute_multilabel_metrics(
            output.y_true,
            output.probabilities,
            label_mapping,
            thresholds,
        ),
        "multi_intent": _subset_metrics(
            output,
            label_mapping,
            thresholds,
            "is_multi_intent",
        ),
        "hard_cases": _subset_metrics(
            output,
            label_mapping,
            thresholds,
            "is_hard_case",
        ),
    }


def train_model(
    *,
    train_path: Path,
    val_path: Path,
    test_path: Path,
    label_mapping_path: Path,
    output_dir: Path,
    config: TrainingConfig,
) -> dict[str, Any]:
    set_random_seed(config.random_seed)
    device = resolve_device(config.device)
    label_mapping = load_label_mapping(label_mapping_path)
    tokenizer = AutoTokenizer.from_pretrained(config.model_name)
    datasets = {
        "train": IntentClassificationDataset(
            train_path, tokenizer, label_mapping, max_length=config.max_length
        ),
        "validation": IntentClassificationDataset(
            val_path, tokenizer, label_mapping, max_length=config.max_length
        ),
        "test": IntentClassificationDataset(
            test_path, tokenizer, label_mapping, max_length=config.max_length
        ),
    }
    generator = torch.Generator().manual_seed(config.random_seed)
    train_loader = DataLoader(
        datasets["train"],
        batch_size=config.batch_size,
        shuffle=True,
        generator=generator,
    )
    val_loader = DataLoader(
        datasets["validation"], batch_size=config.batch_size, shuffle=False
    )
    test_loader = DataLoader(
        datasets["test"], batch_size=config.batch_size, shuffle=False
    )

    model = BgeMultiLabelClassifier.from_pretrained(
        config.model_name,
        num_labels=len(label_mapping),
        dropout=config.dropout,
        pooling=config.pooling,
    ).to(device)
    optimizer = AdamW(
        model.parameters(),
        lr=config.learning_rate,
        weight_decay=config.weight_decay,
    )
    criterion = nn.BCEWithLogitsLoss()
    initial_thresholds = {label: config.threshold for label in label_mapping}

    print(
        f"device={device} model={config.model_name} labels={len(label_mapping)} "
        f"train={len(datasets['train'])} val={len(datasets['validation'])} "
        f"test={len(datasets['test'])}"
    )
    print(
        f"batch_size={config.batch_size} learning_rate={config.learning_rate} "
        f"epochs={config.epochs} max_length={config.max_length} seed={config.random_seed}"
    )

    history: list[dict[str, Any]] = []
    best_macro_f1 = -1.0
    best_epoch = 0
    for epoch in range(1, config.epochs + 1):
        model.train()
        epoch_losses: list[float] = []
        for batch in train_loader:
            optimizer.zero_grad(set_to_none=True)
            labels = batch["labels"].to(device)
            logits = model(**_model_inputs(batch, device))
            loss = criterion(logits, labels)
            loss.backward()
            optimizer.step()
            epoch_losses.append(float(loss.item()))

        validation_output = evaluate_loader(model, val_loader, device=device)
        validation_metrics = compute_multilabel_metrics(
            validation_output.y_true,
            validation_output.probabilities,
            label_mapping,
            config.threshold,
        )
        epoch_record = {
            "epoch": epoch,
            "train_loss": float(np.mean(epoch_losses)),
            "validation_loss": validation_output.loss,
            "validation_metrics": validation_metrics,
        }
        history.append(epoch_record)
        print(
            f"epoch={epoch}/{config.epochs} train_loss={epoch_record['train_loss']:.6f} "
            f"val_loss={validation_output.loss:.6f} "
            f"micro_f1={validation_metrics['micro_f1']:.4f} "
            f"macro_f1={validation_metrics['macro_f1']:.4f} "
            f"precision={validation_metrics['precision']:.4f} "
            f"recall={validation_metrics['recall']:.4f}"
        )
        if validation_metrics[config.best_metric] > best_macro_f1:
            best_macro_f1 = validation_metrics[config.best_metric]
            best_epoch = epoch
            save_model_bundle(
                output_dir,
                model=model,
                tokenizer=tokenizer,
                label_mapping=label_mapping,
                thresholds=initial_thresholds,
                training_config=config,
                metrics={
                    "status": "checkpoint",
                    "best_epoch": best_epoch,
                    "selection_metric": config.best_metric,
                    "history": history,
                },
            )
            print(f"saved_best_checkpoint epoch={epoch} macro_f1={best_macro_f1:.4f}")

    best_bundle = load_model_bundle(output_dir, device=str(device))
    validation_output = evaluate_loader(best_bundle.model, val_loader, device=device)
    tuning = tune_thresholds(
        validation_output.y_true,
        validation_output.probabilities,
        label_mapping,
        candidates=threshold_grid(config.threshold_step),
    )
    thresholds = tuning["per_label_thresholds"]
    test_output = evaluate_loader(best_bundle.model, test_loader, device=device)
    metrics = {
        "best_epoch": best_epoch,
        "selection_metric": config.best_metric,
        "best_validation_macro_f1_at_default_threshold": best_macro_f1,
        "history": history,
        "threshold_tuning": tuning,
        "validation": _evaluation_summary(
            validation_output, label_mapping, thresholds
        ),
        "test": _evaluation_summary(test_output, label_mapping, thresholds),
    }
    save_model_bundle(
        output_dir,
        model=best_bundle.model,
        tokenizer=best_bundle.tokenizer,
        label_mapping=label_mapping,
        thresholds=thresholds,
        training_config=config,
        metrics=metrics,
    )
    errors = build_error_analysis(
        test_output.records,
        test_output.y_true,
        test_output.probabilities,
        label_mapping,
        thresholds,
    )
    for error in errors:
        error["split"] = "test"
    write_error_analysis(output_dir / "error_analysis.jsonl", errors)
    print(
        f"best_epoch={best_epoch} tuned_test_micro_f1="
        f"{metrics['test']['metrics']['micro_f1']:.4f} tuned_test_macro_f1="
        f"{metrics['test']['metrics']['macro_f1']:.4f}"
    )
    return metrics


def evaluate_saved_model(
    *,
    model_dir: Path,
    dataset_path: Path,
    batch_size: int | None = None,
    device: str = "auto",
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    bundle: LoadedModelBundle = load_model_bundle(model_dir, device=device)
    dataset = IntentClassificationDataset(
        dataset_path,
        bundle.tokenizer,
        bundle.label_mapping,
        max_length=bundle.training_config.max_length,
    )
    loader = DataLoader(
        dataset,
        batch_size=batch_size or bundle.training_config.batch_size,
        shuffle=False,
    )
    output = evaluate_loader(bundle.model, loader, device=bundle.device)
    summary = _evaluation_summary(output, bundle.label_mapping, bundle.thresholds)
    errors = build_error_analysis(
        output.records,
        output.y_true,
        output.probabilities,
        bundle.label_mapping,
        bundle.thresholds,
    )
    return summary, errors
