"""Canonical transfer payloads bound to immutable journal receipts."""

import hashlib
import json

from app.agent.foundation.long_horizon.execution import ContinuityConflict
from app.agent.handoff.contracts import CompletedTaskSummary


class TransferIntegrityConflict(ValueError, ContinuityConflict):
    """Sanitized guard rejection; never includes the conflicting payload."""

    def __init__(self):
        super().__init__("transfer payload integrity conflict; execution rejected")


def payload_digest(payload) -> str:
    """Canonical UTF-8 JSON; preserve string contents and reject non-finite data."""
    encoded = json.dumps(
        {"contract": "handoff-payload:v1", "payload": payload},
        ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False,
    ).encode("utf-8")
    return "sha256:" + hashlib.sha256(encoded).hexdigest()


def transfer_payload(run, request, *, source_agent, tool_call_id, completed_count=None,
                     entities=None, evidence_refs=()):
    """Project the actual sender state at a hop, including every target parameter.

    Historical hops use their saved completed_count. Later task results and
    namespace/session identifiers are not part of that transfer's payload.
    """
    from app.agent.handoff.context import latest_entities

    count = len(run.results) if completed_count is None else completed_count
    results, pending = run.results[:count], run.plan.tasks[count:]
    if count < 0 or count > len(run.results) or not pending:
        raise ValueError("transfer payload has no pending task")
    return {
        "schema_version": 1,
        "run_id": run.run_id,
        "tool_call_id": tool_call_id,
        "source_agent": source_agent.value,
        "request": request.model_dump(mode="json"),
        "original_input": run.original_input,
        "active_task": pending[0].model_dump(mode="json"),
        "pending_tasks": [task.model_dump(mode="json") for task in pending],
        "completed_results": [CompletedTaskSummary(
            task_id=result.task_id, route_key=result.route_key,
            content=(result.content or "")[:1000],
        ).model_dump(mode="json") for result in results],
        "entities": (entities if entities is not None else latest_entities(results)).model_dump(mode="json"),
        "evidence_refs": [ref.model_dump(mode="json") for ref in evidence_refs],
    }
