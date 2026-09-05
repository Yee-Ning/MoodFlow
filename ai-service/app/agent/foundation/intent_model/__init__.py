"""Standalone training primitives for the supervised multi-label intent model."""

from app.agent.foundation.intent_model.config import (
    TrainingConfig,
    resolve_device,
    set_random_seed,
)
from app.agent.foundation.intent_model.artifacts import (
    LoadedModelBundle,
    load_model_bundle,
    save_model_bundle,
)
from app.agent.foundation.intent_model.dataset import (
    IntentClassificationDataset,
    encode_multi_hot,
)
from app.agent.foundation.intent_model.inference import predict_text
from app.agent.foundation.intent_model.model import BgeMultiLabelClassifier

__all__ = [
    "BgeMultiLabelClassifier",
    "IntentClassificationDataset",
    "LoadedModelBundle",
    "TrainingConfig",
    "encode_multi_hot",
    "load_model_bundle",
    "predict_text",
    "resolve_device",
    "save_model_bundle",
    "set_random_seed",
]
