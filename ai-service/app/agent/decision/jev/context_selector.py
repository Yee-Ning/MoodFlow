"""Select only optional context, preserving required and runtime items."""

from __future__ import annotations

import hashlib
from collections.abc import Sequence
from time import perf_counter

from typesafe_sdk import Noul

from app.agent.foundation.context_engineering import ContextItem
from app.config.settings import Settings

from .client import DecisionClient, DecisionResponse, JevDecisionError
from .telemetry import record_decision

BATCH_SIZE = 20
SELECTABLE_TYPES = frozenset({
    "conversation", "long_term_memory", "rag_evidence", "history_drilldown",
    "artifact_projection", "handoff_projection", "tool_result",
})


def _question_id(item_id: str) -> str:
    return "context_" + hashlib.sha256(item_id.encode("utf-8")).hexdigest()[:24]


async def select_context(items: list[ContextItem], query: str, *,
                         settings: Settings, client: DecisionClient,
                         trace_id: str | None = None) -> list[ContextItem]:
    if not settings.jev_enabled or not settings.jev_context_select_enabled:
        return items
    optional = [item for item in items if not item.required and item.type in SELECTABLE_TYPES]
    if not optional:
        return items
    scores: dict[str, float] = {}
    selected_ids: set[str] = set()
    last_response: DecisionResponse | None = None
    reason: str | None = None
    started = perf_counter()
    try:
        if len({item.id for item in optional}) != len(optional):
            raise ValueError("duplicate context IDs")
        for offset in range(0, len(optional), BATCH_SIZE):
            batch: Sequence[ContextItem] = optional[offset:offset + BATCH_SIZE]
            mapping = {_question_id(item.id): item.id for item in batch}
            if len(mapping) != len(batch):
                raise ValueError("duplicate context IDs")
            response = await client.decide(
                {"query": query, "candidates": [
                    {"id": item.id, "type": item.type, "content": item.content[:1500]}
                    for item in batch
                ]},
                {qid: Noul(instructions=f"Is context item {item_id} useful for this query?")
                 for qid, item_id in mapping.items()},
            )
            if set(response.answers) != set(mapping):
                raise ValueError("partial context decision")
            last_response = response
            for qid, item_id in mapping.items():
                answer = response.answers[qid]
                if answer.kind != "noul" or answer.noul is None or not 0 <= answer.noul <= 1:
                    raise ValueError("invalid context decision")
                scores[item_id] = answer.noul
                if answer.noul >= settings.jev_context_select_threshold:
                    selected_ids.add(item_id)
    except Exception as exc:
        reason = str(exc) if isinstance(exc, JevDecisionError) else type(exc).__name__
    if settings.jev_shadow_mode:
        reason = reason or "shadow_mode"
    selected = (items if reason else [item for item in items
              if item.required or item.type not in SELECTABLE_TYPES or item.id in selected_ids])
    record_decision("context_select", last_response, candidate_count=len(optional),
                    selected_count=len(selected_ids) if scores else len(optional),
                    applied_count=sum(item.id in selected_ids for item in optional) if not reason else len(optional),
                    fallback_reason=reason, agreement=len(selected_ids) == len(optional) if scores else None,
                    trace_id=trace_id, scores=scores, selected_ids=sorted(selected_ids),
                    required_preserved=all(item in selected for item in items if item.required),
                    requested_model=settings.jev_model,
                    latency_ms=round((perf_counter() - started) * 1000, 2))
    return selected
