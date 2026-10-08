"""Keep the paired eval's comparison and failure accounting reproducible."""

from types import SimpleNamespace

import pytest

from app.agent.foundation.entry_context import active_triage_pack
from app.agent.foundation.routing import RouteDecision, RouteKey, RouteTask
from evals.context_entry_phase1 import evaluate, packs


def case():
    return {"case_id": "contextual", "query": "继续聊爵士", "history": "刚才讨论爵士的历史。",
            "context": {}, "expected_routes": ["general_chat"], "expected_clarify": False}


@pytest.mark.asyncio
async def test_paired_eval_consumes_distinct_packs_and_records_trace():
    seen, progress = [], []

    class Router:
        async def route_with_trace(self, query, _context):
            seen.append(active_triage_pack.get().total_tokens)
            return SimpleNamespace(
                decision=RouteDecision(is_complete=True, tasks=[RouteTask(
                    task_id="test", route_key=RouteKey.GENERAL_CHAT, input_text=query)]),
                router_source="jev_v5", fallback_reason=None, api_calls=1, response_model="test-model")

    result = await evaluate(Router(), [case()], progress=lambda rows: progress.append(len(rows)))
    assert seen[0] < seen[1]
    assert result["accuracy"] == {"lite": 1, "full": 1}
    assert result["regression_gate_passed"] and progress == [1]
    assert result["results"][0]["lite"]["response_model"] == "test-model"
    assert active_triage_pack.get() is None


@pytest.mark.asyncio
async def test_paired_eval_counts_lite_failure_and_fails_regression_gate():
    _, lite, _ = packs(case())

    class Router:
        async def route(self, query, _context):
            if active_triage_pack.get().total_tokens == lite.total_tokens:
                raise RuntimeError("private provider detail")
            return RouteDecision(is_complete=True, tasks=[RouteTask(
                task_id="test", route_key=RouteKey.GENERAL_CHAT, input_text=query)])

    result = await evaluate(Router(), [case()])
    assert result["accuracy"] == {"lite": 0, "full": 1}
    assert not result["regression_gate_passed"]
    assert result["results"][0]["lite"]["error_type"] == "RuntimeError"
    assert "private provider detail" not in str(result)
    assert active_triage_pack.get() is None
