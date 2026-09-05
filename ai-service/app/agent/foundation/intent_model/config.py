import random
from dataclasses import asdict, dataclass, fields
from typing import Any, Literal

import numpy as np
import torch


PoolingMode = Literal["cls", "mean"]


@dataclass(frozen=True)
class TrainingConfig:
    model_name: str = "BAAI/bge-small-zh-v1.5"
    max_length: int = 128
    batch_size: int = 16
    learning_rate: float = 2e-5
    epochs: int = 3
    weight_decay: float = 0.01
    dropout: float = 0.1
    random_seed: int = 42
    threshold: float = 0.5
    threshold_step: float = 0.05
    pooling: PoolingMode = "cls"
    best_metric: str = "macro_f1"
    device: str = "auto"

    def __post_init__(self) -> None:
        if not self.model_name.strip():
            raise ValueError("model_name must not be blank")
        if self.max_length <= 0:
            raise ValueError("max_length must be positive")
        if self.batch_size <= 0:
            raise ValueError("batch_size must be positive")
        if self.learning_rate <= 0:
            raise ValueError("learning_rate must be positive")
        if self.epochs <= 0:
            raise ValueError("epochs must be positive")
        if self.weight_decay < 0:
            raise ValueError("weight_decay must not be negative")
        if not 0 <= self.dropout < 1:
            raise ValueError("dropout must be in [0, 1)")
        if not 0 < self.threshold < 1:
            raise ValueError("threshold must be in (0, 1)")
        if not 0 < self.threshold_step < 1:
            raise ValueError("threshold_step must be in (0, 1)")
        if self.pooling not in ("cls", "mean"):
            raise ValueError("pooling must be 'cls' or 'mean'")
        if self.best_metric != "macro_f1":
            raise ValueError("best_metric currently supports only macro_f1")
        if self.device not in ("auto", "cuda", "mps", "cpu"):
            raise ValueError("device must be auto, cuda, mps, or cpu")

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, values: dict[str, Any]) -> "TrainingConfig":
        supported = {field.name for field in fields(cls)}
        return cls(**{key: value for key, value in values.items() if key in supported})


def resolve_device(requested: str = "auto") -> torch.device:
    if requested == "cuda":
        if not torch.cuda.is_available():
            raise RuntimeError("CUDA was requested but is not available")
        return torch.device("cuda")
    if requested == "mps":
        if not hasattr(torch.backends, "mps") or not torch.backends.mps.is_available():
            raise RuntimeError("MPS was requested but is not available")
        return torch.device("mps")
    if requested == "cpu":
        return torch.device("cpu")
    if requested != "auto":
        raise ValueError("device must be auto, cuda, mps, or cpu")
    if torch.cuda.is_available():
        return torch.device("cuda")
    if hasattr(torch.backends, "mps") and torch.backends.mps.is_available():
        return torch.device("mps")
    return torch.device("cpu")


def set_random_seed(seed: int) -> None:
    random.seed(seed)
    np.random.seed(seed)
    torch.manual_seed(seed)
    if torch.cuda.is_available():
        torch.cuda.manual_seed_all(seed)
        torch.backends.cudnn.deterministic = True
        torch.backends.cudnn.benchmark = False
    if hasattr(torch, "mps") and hasattr(torch.mps, "manual_seed"):
        torch.mps.manual_seed(seed)
