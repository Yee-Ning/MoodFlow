from pathlib import Path

from app.agent.foundation.llm_routing.prompt import build_routing_prompt
from app.agent.foundation.llm_routing.runtime import (
    LLMIntentRuntime, LLMRoutingError, ProviderLLMIntentRuntime,
)
from app.agent.foundation.llm_routing.schemas import LLMRouteRequest, LLMRouteResult, LLMStructuredOutput
from app.agent.foundation.routing import IntentItem, IntentSource, UnresolvedSegment
from app.agent.foundation.rule_routing.coverage import calculate_coverage
from app.agent.structured_output import FailureType, StructuredOutputError, StructuredOutputService


class LLMRouter:
    def __init__(self, taxonomy_path: Path, *, runtime: LLMIntentRuntime | None = None):
        self._runtime = runtime if runtime is not None else ProviderLLMIntentRuntime()
        self._instructions = build_routing_prompt(taxonomy_path)

    async def route(self, request: LLMRouteRequest) -> LLMRouteResult:
        converted: LLMRouteResult | None = None

        def validate_evidence(output: LLMStructuredOutput) -> None:
            nonlocal converted
            converted = self._convert(request, output)

        async def generate(attempt: int, feedback: str | None):
            instructions = self._instructions
            if feedback:
                instructions += f"\n{feedback}"
            return await self._runtime.classify(
                request, instructions=instructions, retry=attempt > 1,
            )

        service = StructuredOutputService(
            "intent_routing", LLMStructuredOutput,
            semantic_validator=validate_evidence,
        )
        try:
            await service.run(generate)
        except StructuredOutputError as exc:
            if exc.failure_type is FailureType.MODEL_ERROR:
                raise LLMRoutingError("LLM routing model unavailable") from exc
            raise LLMRoutingError(
                "LLM routing output failed schema or evidence validation"
            ) from exc
        assert converted is not None
        return converted

    @staticmethod
    def _convert(request: LLMRouteRequest, output: LLMStructuredOutput) -> LLMRouteResult:
        segment = request.segment
        local = []
        for order, candidate in enumerate(output.intents):
            start, end = candidate.start, candidate.end
            if end > len(segment.text) or segment.text[start:end] != candidate.evidence:
                # LLMs can quote correctly but miscount Chinese characters. Correct only
                # uniquely grounded exact quotations; never guess between repeated spans.
                start = segment.text.find(candidate.evidence)
                if start < 0 or segment.text.find(candidate.evidence, start + 1) >= 0:
                    raise ValueError("LLM evidence must identify an unambiguous local span")
                end = start + len(candidate.evidence)
            local.append(IntentItem(
                intent=candidate.intent, confidence=candidate.confidence,
                source=IntentSource.LLM, evidence=candidate.evidence,
                start=start, end=end, order=order,
            ))
        for index, item in enumerate(local):
            if any(
                item.intent is not other.intent
                and item.start == other.start
                and item.end == other.end
                for other in local[index + 1:]
            ):
                raise ValueError(
                    "different intents must cite distinct evidence spans"
                )
        remaining = calculate_coverage(segment.text, local).unresolved_segments
        unresolved = [UnresolvedSegment(
            text=item.text, start=item.start + segment.start, end=item.end + segment.start,
            order=item.order,
        ) for item in remaining]
        needs_clarification = output.needs_clarification or bool(unresolved) or not local
        if needs_clarification and not unresolved:
            unresolved = [segment]
        return LLMRouteResult(
            segment=segment,
            intents=[item.model_copy(update={"start": item.start + segment.start, "end": item.end + segment.start}) for item in local],
            unresolved_segments=unresolved,
            needs_clarification=needs_clarification,
        )
