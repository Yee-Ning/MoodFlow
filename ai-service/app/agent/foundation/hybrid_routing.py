import asyncio
import re
from contextvars import ContextVar
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol
from uuid import uuid4

from app.agent.foundation.intent_merging import IntentMerger, RouteDecisionBuilder, SegmentResolution
from app.agent.foundation.llm_routing import LLMRouter, LLMRouteRequest
from app.agent.foundation.model_routing.model_router import ModelRouter
from app.agent.foundation.model_routing.schemas import ModelRouteResult
from app.agent.foundation.routing import RouteDecision, RoutingContext
from app.agent.foundation.rule_routing import RuleRouter


# Optional request-local accounting used by the V5 Legacy fallback. The
# classifier remains unchanged and independently usable by the old router.
classifier_fallback_counter: ContextVar[list[int] | None] = ContextVar(
    "classifier_fallback_counter", default=None
)

_SEMANTIC_COMPLEXITY = re.compile(
    r"(?:别|不要|不用|无需|并非|不是|如果|假如|要是|除非|否则|"
    r"并|而且|顺便|同时|以及|然后|再|\b(?:do not|don't|if|unless|then)\b)",
    re.IGNORECASE,
)


class ModelRoutingBackend(Protocol):
    def route(self, text: str) -> ModelRouteResult: ...


class AsyncIntentRouter(Protocol):
    async def route(self, text: str, context: RoutingContext | None = None) -> RouteDecision: ...


@dataclass(frozen=True)
class EscalationPolicy:
    """Escalate rejected, ambiguous, or near-threshold model decisions."""

    boundary_margin: float = 0.0
    escalate_multilabel: bool = True
    escalate_semantic_complexity: bool = True

    def __post_init__(self):
        if not 0 <= self.boundary_margin < 1:
            raise ValueError("boundary_margin must be in [0, 1)")

    def reason(
        self,
        result: ModelRouteResult,
        thresholds: dict[str, float],
        text: str = "",
    ) -> str | None:
        if result.needs_llm or not result.intents:
            return "model_rejected"
        if self.escalate_multilabel and len(result.intents) > 1:
            return "multi_label_ambiguity"
        if self.escalate_semantic_complexity and _SEMANTIC_COMPLEXITY.search(text):
            return "semantic_complexity"
        if self.boundary_margin and any(
            result.scores[label] - thresholds[label] <= self.boundary_margin
            for label in result.accepted_labels
        ):
            return "near_threshold"
        return None


class HybridIntentRouter:
    """Request-local orchestration over long-lived, injected routing backends."""

    def __init__(
        self, model_router: ModelRoutingBackend, llm_router: LLMRouter,
        *, rule_router: RuleRouter | None = None,
        policy: EscalationPolicy | None = None, thresholds: dict[str, float] | None = None,
    ):
        self._rule = rule_router if rule_router is not None else RuleRouter()
        self._model = model_router
        self._llm = llm_router
        self._policy = policy or EscalationPolicy()
        self._thresholds = dict(thresholds or {})
        if self._policy.boundary_margin and not self._thresholds:
            raise ValueError("boundary policy requires artifact thresholds")
        self._merger = IntentMerger()
        self._builder = RouteDecisionBuilder()

    async def route(self, text: str, context: RoutingContext | None = None) -> RouteDecision:
        if not text.strip() or len(text) > 4_000:
            raise ValueError("query must contain 1..4000 nonblank characters")
        context = context or RoutingContext()
        rule = self._rule.route(text, context)
        resolutions = []
        for segment in rule.unresolved_segments:
            # The CPU/GPU forward must not block the asynchronous graph's event loop.
            model = await asyncio.to_thread(self._model.route, segment.text)
            counter = classifier_fallback_counter.get()
            if counter is not None:
                counter[0] += 1
            reason = self._policy.reason(model, self._thresholds, segment.text)
            llm = None
            if reason is not None:
                model = model.model_copy(update={"needs_llm": True, "unresolved_text": segment.text})
                llm = await self._llm.route(LLMRouteRequest(
                    original_text=text, segment=segment, routing_context=context,
                    model_scores=model.scores, model_intents=model.intents,
                    escalation_reason=reason,
                ))
            resolutions.append(SegmentResolution(segment, model, llm))
        merged = self._merger.merge(text, rule, resolutions)
        return self._builder.build(text, merged, run_id=uuid4().hex)


def create_hybrid_router(
    *, model_dir: Path | None = None, taxonomy_path: Path | None = None,
    device: str = "auto", boundary_margin: float = 0.0,
    escalate_multilabel: bool = True,
    escalate_semantic_complexity: bool = True,
) -> HybridIntentRouter:
    # The factory is called once when constructing the application graph.
    from app.agent.foundation.model_routing.runtime import IntentClassifierRuntime

    service_root = Path(__file__).resolve().parents[3]
    runtime = IntentClassifierRuntime(model_dir or service_root / "artifacts/intent_model", device=device)
    return HybridIntentRouter(
        ModelRouter(runtime=runtime),
        LLMRouter(taxonomy_path or service_root / "data/intent/taxonomy.json"),
        policy=EscalationPolicy(
            boundary_margin=boundary_margin,
            escalate_multilabel=escalate_multilabel,
            escalate_semantic_complexity=escalate_semantic_complexity,
        ),
        thresholds=runtime.thresholds,
    )
