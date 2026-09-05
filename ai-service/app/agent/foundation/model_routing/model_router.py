from pathlib import Path

from app.agent.foundation.model_routing.runtime import (
    IntentClassifierRuntime,
    IntentPredictionRuntime,
    ModelRuntimePredictionError,
)
from app.agent.foundation.model_routing.schemas import ModelRouteResult
from app.agent.foundation.routing import IntentItem, IntentSource, IntentType


class ModelRouter:
    """Convert backend-neutral model predictions into shared routing contracts."""

    def __init__(
        self,
        model_dir: Path | None = None,
        *,
        device: str = "auto",
        runtime: IntentPredictionRuntime | None = None,
    ) -> None:
        if runtime is None and model_dir is None:
            raise ValueError("model_dir is required when runtime is not provided")
        if runtime is not None and model_dir is not None:
            raise ValueError("provide either model_dir or runtime, not both")
        self._runtime = (
            runtime
            if runtime is not None
            else IntentClassifierRuntime(Path(model_dir), device=device)
        )

    def route(self, text: str) -> ModelRouteResult:
        normalized_text = text.strip()
        if not normalized_text:
            raise ValueError("text must not be blank")

        prediction = self._runtime.predict(normalized_text)
        label_mapping = self._runtime.label_mapping
        ordered_labels = sorted(
            prediction.accepted_labels,
            key=lambda label: (
                -prediction.scores[label],
                label_mapping[label],
            ),
        )
        intents: list[IntentItem] = []
        for order, label in enumerate(ordered_labels):
            try:
                intent = IntentType[label]
            except KeyError as error:
                raise ModelRuntimePredictionError(
                    f"runtime returned an unknown IntentType name: {label}"
                ) from error
            if intent is IntentType.UNKNOWN:
                raise ModelRuntimePredictionError(
                    "runtime returned UNKNOWN as a trainable prediction"
                )
            intents.append(
                IntentItem(
                    intent=intent,
                    sub_intent=None,
                    confidence=prediction.scores[label],
                    source=IntentSource.MODEL,
                    evidence=normalized_text,
                    start=0,
                    end=len(normalized_text),
                    order=order,
                )
            )

        needs_llm = prediction.needs_llm or not intents
        return ModelRouteResult(
            intents=intents,
            scores=prediction.scores,
            accepted_labels=ordered_labels,
            unresolved_text=normalized_text if needs_llm else "",
            needs_llm=needs_llm,
        )
