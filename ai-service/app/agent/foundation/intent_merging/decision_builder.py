from app.agent.foundation.intent_merging.merger import MergedIntentResult
from app.agent.foundation.routing import RouteDecision, RouteKey, RouteTask
from app.agent.foundation.rule_routing.coverage import calculate_coverage


class RouteDecisionBuilder:
    def build(self, original_text: str, merged: MergedIntentResult, *, run_id: str) -> RouteDecision:
        if not run_id.strip():
            raise ValueError("run_id must not be blank")
        pending = merged.needs_llm or bool(merged.unresolved_segments)
        clarification = merged.needs_clarification or (not merged.intents and not merged.needs_llm)
        complete = not pending and not clarification
        groups: dict[RouteKey, list[int]] = {}
        for item in merged.intents:
            route = RouteKey(item.intent.value)
            groups.setdefault(route, []).append(item.order)
        # V1 dispatch is all-or-nothing: preserve recognized intents while waiting for clarification.
        tasks = [RouteTask(task_id=f"{run_id}:{route.value}:{index}", route_key=route, intent_orders=orders)
                 for index, (route, orders) in enumerate(groups.items())] if complete else []
        resolved = [item for item in merged.intents if not any(
            item.start < segment.end and segment.start < item.end
            for segment in merged.unresolved_segments
        )]
        return RouteDecision(
            intents=merged.intents, unresolved_segments=merged.unresolved_segments,
            unresolved_text="；".join(segment.text for segment in merged.unresolved_segments),
            is_complete=complete, needs_semantic=False, needs_llm=merged.needs_llm,
            needs_clarification=clarification,
            coverage=calculate_coverage(original_text, resolved).ratio,
            tasks=tasks,
        )
