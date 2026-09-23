"""Fail-open usefulness filtering after the existing semantic memory retrieval."""

from __future__ import annotations

import hashlib
from collections.abc import Sequence
from time import perf_counter

from typesafe_sdk import Noul

from app.agent.memory.models import MemoryRetrievalRequest, MemoryRetrievalResult, RetrievedMemory
from app.config.settings import Settings

from .client import DecisionClient, DecisionResponse, JevDecisionError
from .telemetry import record_decision

BATCH_SIZE = 20


def _question_id(memory_id: str) -> str:
    return "memory_" + hashlib.sha256(memory_id.encode("utf-8")).hexdigest()[:24]


async def filter_memories(request: MemoryRetrievalRequest, result: MemoryRetrievalResult, *,
                          settings: Settings, client: DecisionClient,
                          trace_id: str | None = None) -> MemoryRetrievalResult:
    if not settings.jev_enabled or not settings.jev_memory_filter_enabled or not result.items:
        return result
    if result.user_id != request.user_id:
        raise ValueError("memory retrieval user mismatch")
    selected_ids: set[str] = set()
    scores: dict[str, float] = {}
    last_response: DecisionResponse | None = None
    reason: str | None = None
    started = perf_counter()
    try:
        if len({item.memory_id for item in result.items}) != len(result.items):
            raise ValueError("duplicate memory IDs")
        for offset in range(0, len(result.items), BATCH_SIZE):
            batch: Sequence[RetrievedMemory] = result.items[offset:offset + BATCH_SIZE]
            mapping = {_question_id(item.memory_id): item.memory_id for item in batch}
            if len(mapping) != len(batch):
                raise ValueError("duplicate memory IDs")
            state = {
                "query": request.query,
                "memories": [
                    {"memory_id": item.memory_id, "memory_type": item.memory_type.value,
                     "content": " ".join(item.content.split())[:1500],
                     "importance": item.importance, "updated_at": item.updated_at.isoformat()}
                    for item in batch
                ],
            }
            questions = {
                qid: Noul(instructions=f"Is memory_id {memory_id} useful for answering the query now?")
                for qid, memory_id in mapping.items()
            }
            response = await client.decide(state, questions)
            if set(response.answers) != set(mapping):
                raise ValueError("partial memory decision")
            last_response = response
            for qid, memory_id in mapping.items():
                answer = response.answers[qid]
                if answer.kind != "noul" or answer.noul is None or not 0 <= answer.noul <= 1:
                    raise ValueError("invalid memory decision")
                scores[memory_id] = answer.noul
                if answer.noul >= settings.jev_memory_filter_threshold:
                    selected_ids.add(memory_id)
    except Exception as exc:
        reason = str(exc) if isinstance(exc, JevDecisionError) else type(exc).__name__
        selected_ids = {item.memory_id for item in result.items}
    if settings.jev_shadow_mode:
        reason = reason or "shadow_mode"
    selected = result.items if reason else [item for item in result.items if item.memory_id in selected_ids]
    record_decision("memory_filter", last_response, candidate_count=len(result.items),
                    selected_count=len(selected_ids), applied_count=len(selected), fallback_reason=reason,
                    agreement=(len(selected_ids) == len(result.items)) if scores else None,
                    trace_id=trace_id, scores=scores, selected_ids=sorted(selected_ids),
                    requested_model=settings.jev_model,
                    latency_ms=round((perf_counter() - started) * 1000, 2))
    return result.model_copy(update={"items": selected})
