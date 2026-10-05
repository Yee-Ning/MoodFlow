from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from app.agent.foundation.llm_routing.schemas import LLMRouteResult
    from app.agent.foundation.model_routing.schemas import ModelRouteResult
from app.agent.foundation.routing import IntentItem, IntentSource, IntentType, RouteDecision, UnresolvedSegment
from app.agent.foundation.rule_routing.coverage import calculate_coverage


@dataclass(frozen=True)
class SegmentResolution:
    segment: UnresolvedSegment
    model: ModelRouteResult
    llm: LLMRouteResult | None = None


@dataclass(frozen=True)
class MergedIntentResult:
    intents: list[IntentItem]
    unresolved_segments: list[UnresolvedSegment]
    needs_llm: bool
    needs_clarification: bool


class IntentMerger:
    """Pure merge: preserve rules, replace only explicitly escalated segments."""

    def merge(
        self, original_text: str, rule_result: RouteDecision,
        resolutions: list[SegmentResolution],
    ) -> MergedIntentResult:
        candidates = list(rule_result.intents)
        remaining = []
        pending_llm = False
        clarification = rule_result.needs_clarification
        by_span = {}
        for resolution in resolutions:
            span = (resolution.segment.start, resolution.segment.end)
            if span in by_span:
                raise ValueError("duplicate segment resolution")
            by_span[span] = resolution
        expected = {(s.start, s.end) for s in rule_result.unresolved_segments}
        if not set(by_span) <= expected:
            raise ValueError("resolution must belong to a rule-unresolved segment")

        for segment in rule_result.unresolved_segments:
            if original_text[segment.start:segment.end] != segment.text:
                raise ValueError("unresolved segment does not match original text")
            resolution = by_span.get((segment.start, segment.end))
            if resolution is None:
                remaining.append(segment)
                pending_llm = True
                continue
            if resolution.segment != segment:
                raise ValueError("resolution segment differs from rule segment")
            if resolution.llm is not None:
                llm = resolution.llm
                if llm.segment != segment:
                    raise ValueError("LLM result belongs to a different segment")
                chosen = list(llm.intents)
                for unresolved in llm.unresolved_segments:
                    if not segment.start <= unresolved.start < unresolved.end <= segment.end:
                        raise ValueError("LLM unresolved span is outside its segment")
                    if original_text[unresolved.start:unresolved.end] != unresolved.text:
                        raise ValueError("LLM unresolved evidence is not grounded")
                remaining.extend(llm.unresolved_segments)
                clarification |= llm.needs_clarification
                if llm.needs_clarification and not llm.unresolved_segments:
                    remaining.append(segment)
                expected_source = IntentSource.LLM
            elif resolution.model.needs_llm:
                # Escalated model candidates are provisional, not dispatchable.
                remaining.append(segment)
                pending_llm = True
                continue
            else:
                chosen = [IntentItem(**{
                    **item.model_dump(),
                    "start": item.start + segment.start,
                    "end": item.end + segment.start,
                }) for item in resolution.model.intents]
                expected_source = IntentSource.MODEL
            for item in chosen:
                if item.source is not expected_source:
                    raise ValueError("candidate source differs from router layer")
                if not segment.start <= item.start < item.end <= segment.end:
                    raise ValueError("candidate cannot escape its segment")
                if any(item.start < rule.end and rule.start < item.end for rule in rule_result.intents):
                    raise ValueError("semantic candidate cannot overwrite a locked rule span")
            candidates.extend(chosen)

        unique = {}
        for item in candidates:
            if item.intent is IntentType.UNKNOWN:
                raise ValueError("UNKNOWN cannot be merged into actionable intents")
            if original_text[item.start:item.end] != item.evidence:
                raise ValueError("candidate evidence does not match original text")
            key = (item.intent, item.sub_intent, item.start, item.end)
            previous = unique.get(key)
            if previous is None or (previous.source is item.source and item.confidence > previous.confidence):
                unique[key] = item
        ordered = sorted(unique.values(), key=lambda item: (item.start, item.order, item.end, item.intent.value))
        intents = [item.model_copy(update={"order": order}) for order, item in enumerate(ordered)]

        # A malformed partial result cannot silently hide uncovered content.
        remaining.extend(calculate_coverage(original_text, intents).unresolved_segments)
        spans = sorted({(s.start, s.end) for s in remaining})
        coalesced: list[tuple[int, int]] = []
        for start, end in spans:
            if coalesced and start < coalesced[-1][1]:
                coalesced[-1] = (coalesced[-1][0], max(end, coalesced[-1][1]))
            else:
                coalesced.append((start, end))
        unresolved = [UnresolvedSegment(text=original_text[start:end], start=start, end=end, order=i)
                      for i, (start, end) in enumerate(coalesced)]
        if unresolved and not pending_llm:
            clarification = True
        return MergedIntentResult(intents, unresolved, pending_llm, clarification)
