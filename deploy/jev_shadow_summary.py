"""Summarize anonymous V5 Shadow events from the current ai-service logs.

Usage: docker compose ... logs --no-color ai-service | python3 -m deploy.jev_shadow_summary
Raw log lines and user queries are never included in the JSON output.
"""
from __future__ import annotations

import json
import statistics
import sys
from collections import Counter
from collections.abc import Iterable


def _tokens(usage: object) -> int:
    if not isinstance(usage, dict):
        return 0
    total = usage.get("total_tokens")
    if isinstance(total, int) and not isinstance(total, bool):
        return total
    return sum(value for key, value in usage.items()
               if key in {"input_tokens", "output_tokens", "prompt_tokens", "completion_tokens"}
               and isinstance(value, int) and not isinstance(value, bool))


def _latency(values: list[float]) -> dict[str, float | None]:
    if not values:
        return {"avg": None, "p50": None, "p95": None}
    ordered = sorted(values)

    def percentile(frac: float) -> float:
        at = (len(ordered) - 1) * frac
        low = int(at)
        high = min(low + 1, len(ordered) - 1)
        return round(ordered[low] + (ordered[high] - ordered[low]) * (at - low), 2)

    return {"avg": round(statistics.mean(ordered), 2),
            "p50": percentile(.5), "p95": percentile(.95)}


def summarize(lines: Iterable[str]) -> dict[str, object]:
    groups: dict[str, list[dict[str, object]]] = {name: [] for name in
                                                   ("production", "acceptance", "synthetic")}
    failures: Counter[str] = Counter()
    dropped: Counter[str] = Counter()
    for line in lines:
        if "jev.v5_shadow_failure " in line:
            try:
                event = json.loads(line.split("jev.v5_shadow_failure ", 1)[1])
            except json.JSONDecodeError:
                continue
            kind = event.get("sample_kind", "production")
            if kind in groups:
                failures[kind] += 1
            continue
        if "jev.v5_shadow_dropped capacity sample_kind=" in line:
            kind = line.rsplit("sample_kind=", 1)[1].strip()
            if kind in groups:
                dropped[kind] += 1
            continue
        if "jev.v5_shadow " not in line:
            continue
        try:
            event = json.loads(line.split("jev.v5_shadow ", 1)[1])
        except json.JSONDecodeError:
            continue
        kind = event.get("sample_kind", "production")
        if kind in groups and event.get("decision_type") == "router_v5_shadow":
            groups[kind].append(event)

    output: dict[str, object] = {}
    for kind, events in groups.items():
        count = len(events)
        routes: Counter[str] = Counter()
        for event in events:
            routes.update(route for route in event.get("candidate_routes", [])
                          if isinstance(route, str))
        normal = [float(event["total_router_latency_ms"]) for event in events
                  if event.get("fallback_reason") is None
                  and isinstance(event.get("total_router_latency_ms"), (int, float))]
        fallback = [float(event["total_router_latency_ms"]) for event in events
                    if event.get("fallback_reason") is not None
                    and isinstance(event.get("total_router_latency_ms"), (int, float))]
        output[kind] = {
            "sample_size": count,
            "single_count": sum(event.get("router_source") == "jev_single" for event in events),
            "multi_count": sum(event.get("router_source") == "jev_multi" for event in events),
            "fallback_count": sum(event.get("fallback_reason") is not None for event in events),
            "timeout_count": sum(event.get("fallback_reason") == "timeout" for event in events),
            "route_comparable_count": sum(event.get("route_agreement") is not None for event in events),
            "route_agreement_count": sum(event.get("route_agreement") is True for event in events),
            "task_count_agreement_count": sum(event.get("task_count_agreement") is True for event in events),
            "clarify_disagreement_count": sum(event.get("clarify_disagreement") is True for event in events),
            "candidate_subgraph_dispatch_count": sum(int(event.get("candidate_subgraph_dispatch_count", 0))
                                                     for event in events),
            "candidate_tool_action_dispatch_count": sum(int(event.get("candidate_tool_action_dispatch_count", 0))
                                                       for event in events),
            "route_distribution": dict(sorted(routes.items())),
            "normal_latency_ms": _latency(normal),
            "fallback_latency_ms": _latency(fallback),
            "api_calls_per_query": round(sum(int(event.get("api_calls", 0)) for event in events) / count, 2)
                                   if count else None,
            "tokens_per_query": round(sum(_tokens(event.get("usage")) for event in events) / count, 2)
                                if count else None,
            "shadow_failure_count": failures[kind],
            "dropped_count": dropped[kind],
        }
    return output


def main() -> None:
    print(json.dumps(summarize(sys.stdin), ensure_ascii=False, sort_keys=True))


if __name__ == "__main__":
    main()
