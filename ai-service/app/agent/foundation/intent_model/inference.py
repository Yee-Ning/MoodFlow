from typing import Any

import torch

from app.agent.foundation.intent_model.metrics import accepted_labels
from app.agent.foundation.intent_model.model import BgeMultiLabelClassifier


def predict_text(
    model: BgeMultiLabelClassifier,
    tokenizer: Any,
    text: str,
    label_mapping: dict[str, int],
    thresholds: dict[str, float],
    *,
    max_length: int,
    device: torch.device,
) -> dict[str, Any]:
    normalized_input = text.strip()
    if not normalized_input:
        raise ValueError("text must not be blank")
    encoded = tokenizer(
        normalized_input,
        truncation=True,
        padding="max_length",
        max_length=max_length,
        return_tensors="pt",
    )
    model_inputs = {
        key: value.to(device)
        for key, value in encoded.items()
        if key in {"input_ids", "attention_mask", "token_type_ids"}
        and isinstance(value, torch.Tensor)
    }
    model.eval()
    with torch.inference_mode():
        logits = model(**model_inputs)
        probabilities = torch.sigmoid(logits)[0].detach().cpu().numpy()

    ordered = sorted(label_mapping.items(), key=lambda item: item[1])
    scores = {label: float(probabilities[index]) for label, index in ordered}
    labels = accepted_labels(probabilities, label_mapping, thresholds)
    return {
        "text": normalized_input,
        "scores": scores,
        "accepted_labels": labels,
        "needs_llm": not labels,
    }
