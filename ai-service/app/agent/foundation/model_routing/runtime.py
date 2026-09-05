import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from app.agent.foundation.intent_model.artifacts import (
    LoadedModelBundle,
    load_model_bundle,
)
from app.agent.foundation.intent_model.inference import predict_text

logger = logging.getLogger(__name__)


class ModelRuntimeLoadError(RuntimeError):
    """Raised when a complete intent-model artifact cannot be restored."""


class ModelRuntimePredictionError(RuntimeError):
    """Raised when a runtime produces an invalid prediction contract."""


@dataclass(frozen=True)
class IntentModelPrediction:
    scores: dict[str, float]
    accepted_labels: tuple[str, ...]
    needs_llm: bool


class IntentPredictionRuntime(Protocol):
    """Backend-neutral boundary consumed by ModelRouter."""

    @property
    def label_mapping(self) -> dict[str, int]: ...

    @property
    def thresholds(self) -> dict[str, float]: ...

    def predict(self, text: str) -> IntentModelPrediction: ...


class IntentClassifierRuntime:
    """Long-lived PyTorch runtime backed by one saved training artifact."""

    REQUIRED_FILES = (
        "classifier_head.pt",
        "label_mapping.json",
        "thresholds.json",
        "training_config.json",
    )

    def __init__(self, model_dir: Path, *, device: str = "auto") -> None:
        self._model_dir = Path(model_dir)
        self._validate_artifact_paths()
        try:
            bundle = load_model_bundle(self._model_dir, device=device)
        except Exception as error:
            raise ModelRuntimeLoadError(
                f"failed to load intent model artifact from {self._model_dir}: {error}"
            ) from error
        self._validate_bundle(bundle)
        self._bundle = bundle
        logger.info(
            "Intent model initialized: path=%s device=%s labels=%d thresholds=%d",
            self._model_dir,
            bundle.device,
            len(bundle.label_mapping),
            len(bundle.thresholds),
        )

    def _validate_artifact_paths(self) -> None:
        if not self._model_dir.is_dir():
            raise FileNotFoundError(
                f"intent model directory does not exist: {self._model_dir}"
            )
        for relative_path in self.REQUIRED_FILES:
            path = self._model_dir / relative_path
            if not path.is_file():
                raise FileNotFoundError(f"required model artifact is missing: {path}")
        encoder_dir = self._model_dir / "encoder"
        if not encoder_dir.is_dir():
            raise FileNotFoundError(
                f"required encoder directory is missing: {encoder_dir}"
            )

    @staticmethod
    def _validate_bundle(bundle: LoadedModelBundle) -> None:
        mapping_size = len(bundle.label_mapping)
        output_size = bundle.model.classifier.out_features
        if output_size != mapping_size:
            raise ValueError(
                "classifier output dimension does not match label_mapping: "
                f"output={output_size}, labels={mapping_size}"
            )
        if set(bundle.thresholds) != set(bundle.label_mapping):
            raise ValueError("threshold labels do not match label_mapping")

    @property
    def label_mapping(self) -> dict[str, int]:
        return dict(self._bundle.label_mapping)

    @property
    def thresholds(self) -> dict[str, float]:
        return dict(self._bundle.thresholds)

    @property
    def device(self) -> str:
        return str(self._bundle.device)

    def predict(self, text: str) -> IntentModelPrediction:
        prediction = predict_text(
            self._bundle.model,
            self._bundle.tokenizer,
            text,
            self._bundle.label_mapping,
            self._bundle.thresholds,
            max_length=self._bundle.training_config.max_length,
            device=self._bundle.device,
        )
        scores = prediction.get("scores")
        accepted = prediction.get("accepted_labels")
        if not isinstance(scores, dict) or set(scores) != set(self._bundle.label_mapping):
            raise ModelRuntimePredictionError(
                "prediction scores do not match the loaded label_mapping"
            )
        if not isinstance(accepted, list) or any(
            label not in self._bundle.label_mapping for label in accepted
        ):
            raise ModelRuntimePredictionError(
                "prediction accepted_labels do not match the loaded label_mapping"
            )
        return IntentModelPrediction(
            scores={label: float(score) for label, score in scores.items()},
            accepted_labels=tuple(accepted),
            needs_llm=bool(prediction.get("needs_llm")),
        )
