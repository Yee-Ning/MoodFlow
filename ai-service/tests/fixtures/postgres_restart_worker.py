from __future__ import annotations

import asyncio
import json
import operator
import sys
from typing import Annotated, TypedDict

from langgraph.graph import END, START, StateGraph

from app.agent.checkpointing import (
    close_runtime_checkpointer,
    create_runtime_checkpointer,
    initialize_runtime_checkpointer,
)
from app.agent.foundation.long_horizon.models import (
    HandoffDraft,
    HistoryEventType,
    HistoryPointer,
    RolloverReason,
)
from app.agent.foundation.long_horizon.runtime import (
    close_long_horizon_service,
    get_long_horizon_service,
)
from app.agent.foundation.routing import RouteKey


class RecoveryState(TypedDict, total=False):
    request: str
    route: str
    evidence: list[str]
    steps: Annotated[list[str], operator.add]


def build_recovery_graph():
    builder = StateGraph(RecoveryState)

    def router(state: RecoveryState) -> RecoveryState:
        return {"route": "worldline", "steps": [f"router:{state['request']}"]}

    def retrieval(state: RecoveryState) -> RecoveryState:
        return {
            "evidence": [f"evidence:{state['request']}"],
            "steps": [f"retrieval:{state['request']}"],
        }

    builder.add_node("router", router)
    builder.add_node("retrieval", retrieval)
    builder.add_edge(START, "router")
    builder.add_edge("router", "retrieval")
    builder.add_edge("retrieval", END)
    return builder.compile(checkpointer=create_runtime_checkpointer())


async def create(conversation_id: str, task_id: str) -> dict:
    await initialize_runtime_checkpointer()
    service = get_long_horizon_service()
    session = service.start_task(conversation_id, task_id=task_id)
    config = {
        "configurable": {
            "thread_id": conversation_id,
            "session_id": session.session_id,
        }
    }
    graph = build_recovery_graph()
    output = await graph.ainvoke(
        {"request": "initial", "steps": []},
        config,
    )
    route_event = service.record_event(
        task_id=task_id,
        session_id=session.session_id,
        event_type=HistoryEventType.ROUTE_DECISION,
        node="router",
        source="restart_e2e",
        payload={"route": output["route"]},
    )
    artifact = service.create_artifact(
        task_id=task_id,
        session_id=session.session_id,
        artifact_type="retrieval_evidence",
        payload=output["evidence"],
        projection={"count": len(output["evidence"])},
    )
    service.record_event(
        task_id=task_id,
        session_id=session.session_id,
        event_type=HistoryEventType.RETRIEVAL,
        node="retrieval",
        source="restart_e2e",
        storage_ref=artifact.ref,
        references=[HistoryPointer(kind="artifact", target_id=artifact.ref)],
    )
    rollover = service.rollover(
        session.session_id,
        HandoffDraft(
            goal="restart recovery",
            constraints=["preserve evidence"],
            constraint_refs={
                "preserve evidence": [
                    HistoryPointer(kind="event", target_id=route_event.event_id)
                ]
            },
            current_stage="retrieval complete",
            next_actions=["continue after restart"],
            artifact_refs=[artifact.ref],
            evidence_refs=[
                HistoryPointer(kind="artifact", target_id=artifact.ref)
            ],
            revalidate_fields=["current_song_id"],
            last_known_state={"current_song_id": 1},
        ),
        RolloverReason.EXPLICIT_CHECKPOINT,
    )
    result = {
        "old_session_id": session.session_id,
        "new_session_id": rollover.new_session.session_id,
        "handoff_id": rollover.handoff.handoff_id,
        "steps": output["steps"],
    }
    close_long_horizon_service()
    await close_runtime_checkpointer()
    return result


async def recover(conversation_id: str, task_id: str) -> dict:
    await initialize_runtime_checkpointer()
    service = get_long_horizon_service()
    sessions = service.store.sessions_for_task(task_id)
    old_session, new_session = sessions
    graph = build_recovery_graph()
    config = {
        "configurable": {
            "thread_id": conversation_id,
            "session_id": old_session.session_id,
        }
    }
    checkpoint = await create_runtime_checkpointer().aget_tuple(config)
    if checkpoint is None:
        raise RuntimeError("checkpoint was not recovered")
    checkpoint_id = checkpoint.config["configurable"]["checkpoint_id"]
    before_steps = list(checkpoint.checkpoint["channel_values"]["steps"])
    continued = await graph.ainvoke(
        {"request": "continued", "steps": []},
        config,
    )
    cold = service.cold_start(
        new_session.session_id,
        live_state={"current_song_id": 2},
    )
    context = service.build_active_context(
        RouteKey.GENERAL_CHAT,
        session_id=new_session.session_id,
        query="continue",
        system="recovery test",
        live_state={"current_song_id": 2},
    )
    history = service.query_task_history(task_id)
    result = {
        "checkpoint_id": checkpoint_id,
        "checkpoint_distinct_from_session": checkpoint_id
        != old_session.session_id,
        "before_steps": before_steps,
        "continued_steps": continued["steps"],
        "history_types": sorted({event.event_type.value for event in history}),
        "handoff_id": cold.handoff.handoff_id if cold.handoff else None,
        "artifact_count": cold.artifacts[0].projection["count"],
        "changed_fields": cold.changed_fields,
        "context_within_budget": context.total_tokens <= context.budget,
        "session_count": len(sessions),
    }
    close_long_horizon_service()
    await close_runtime_checkpointer()
    return result


async def main() -> None:
    mode, conversation_id, task_id = sys.argv[1:4]
    if mode == "create":
        result = await create(conversation_id, task_id)
    elif mode == "recover":
        result = await recover(conversation_id, task_id)
    else:
        raise ValueError(f"unknown mode:{mode}")
    print(json.dumps(result, ensure_ascii=False))


if __name__ == "__main__":
    asyncio.run(main())
