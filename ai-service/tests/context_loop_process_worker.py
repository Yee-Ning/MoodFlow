"""Test-only subprocess: production coordinator/savers; controlled model/music."""

import asyncio
import json
import os
import sys

from app.agent.checkpointing import AsyncCompatiblePostgresSaver, create_checkpoint_serializer
from app.agent.foundation.long_horizon.accounting import DurableAccounting
from app.agent.foundation.long_horizon.coordinator import ContinuityCoordinator, ContinuityCheckpointer
from app.agent.foundation.long_horizon.migration import NamespaceMigrator
from app.agent.foundation.long_horizon.postgres_store import PostgresContinuityStore
from app.agent.foundation.long_horizon.runner import invoke
from app.agent.foundation.long_horizon.service import LongHorizonContextService
from app.agent.foundation.routing import RouteKey, RoutingContext
from app.agent.handoff.graph import create_handoff_graph
from app.agent.handoff.registry import AgentKey
from app.agent.handoff.state import HandoffRun
from tests.test_handoff_network import worldline, transfers
from tests.test_handoff_runtime import Router


def die():
    os._exit(73)


async def execute():
    mode, point, thread = sys.argv[1:4]
    store = PostgresContinuityStore(os.environ["CONTEXT_LOOP_TEST_DATABASE"])
    coordinator = ContinuityCoordinator(LongHorizonContextService(store))
    raw = AsyncCompatiblePostgresSaver(store._pool, serde=create_checkpoint_serializer())
    conversation = "handoff:v1:" + thread
    task = store.active_task(conversation)
    session = store.active_session(task.task_id) if task else None
    config = {"configurable": {"thread_id": conversation, "session_id": session.session_id}} if session else None
    row = await raw.aget_tuple(config) if config else None
    run = HandoffRun.model_validate(row.checkpoint["channel_values"]["handoff_run"]) if row and row.checkpoint["channel_values"].get("handoff_run") else None
    model = transfers((AgentKey.GENERAL_CHAT, AgentKey.CURATOR, AgentKey.EXPLORER))
    if run:
        model.responses = model.responses[len(run.hops):] or model.responses[-1:]
    graph = create_handoff_graph(checkpointer=ContinuityCheckpointer(raw, coordinator), transfer_journal=coordinator.journal,
                                 intent_router=Router(RouteKey.WORLDLINE), transfer_model=model, worldline_subgraph=worldline())
    if mode == "crash":
        original_put = raw.aput
        async def faulty_put(config, checkpoint, metadata, versions):
            values = checkpoint["channel_values"]
            run = values.get("handoff_run")
            if point == "before_checkpoint" and run and len(run["hops"]) == 2:
                die()
            result = await original_put(config, checkpoint, metadata, versions)
            if (point == "after_checkpoint" and run and len(run["hops"]) == 2
                or point == "after_result" and run and len(run["results"]) == 1
                or point == "after_final" and run and run["status"] == "completed"):
                die()
            return result
        raw.aput = faulty_put
        original_transfer = coordinator.writer.transfer
        def transfer(identity, key, body):
            result = original_transfer(identity, key, body)
            if point == "after_proposal" and body.phase == "proposed" and body.summary.hop_index == 1:
                die()
            return result
        coordinator.writer.transfer = transfer
        original_start = coordinator.before_agent
        async def before_agent(state, run):
            await original_start(state, run)
            if point == "target_started" and len(run.hops) == 2:
                die()
        coordinator.before_agent = before_agent
        original_reserve = DurableAccounting.reserve
        def reserve(accounting, ledger, amount, **usage_fields):
            result = original_reserve(accounting, ledger, amount, **usage_fields)
            if point == "model_dispatch":
                die()
            return result
        DurableAccounting.reserve = reserve
    if mode in {"migrate", "migration_crash"}:
        call = await coordinator.begin_call(graph=graph, graph_mode="handoff", thread_id=thread, message="",
            routing_context=RoutingContext(), request_id="migration", resume=True)
        try:
            if mode == "migration_crash":
                original_rollover = coordinator.service.rollover
                def rollover(*args, **kwargs):
                    original_rollover(*args, **kwargs)
                    die()
                coordinator.service.rollover = rollover
            config = await NamespaceMigrator(coordinator).migrate(call, migration_id="one")
            print(json.dumps({"migrated": True, "session_id": config["configurable"]["session_id"]}))
        finally:
            await call.close()
            store.close()
        return
    response = await invoke(graph=graph, coordinator=coordinator, graph_mode="handoff", thread_id=thread,
        message="探索歌手《周杰伦》的世界线" if mode == "crash" else "", routing_context=RoutingContext(),
        request_id="original" if mode == "crash" else "recovery", resume=mode != "crash")
    task = store.active_task(conversation)
    session = store.active_session(task.task_id)
    state = await graph.aget_state({"configurable": {"thread_id": conversation, "session_id": session.session_id}})
    run = HandoffRun.model_validate(state.values["handoff_run"])
    print(json.dumps({"status": str(response.status), "run_id": run.run_id, "started_at": run.started_at.isoformat(),
        "task_status": [str(result.status) for result in run.results], "public_errors": [result.error for result in run.results],
        "trace_codes": [item["code"] for item in state.values.get("handoff_trace", [])],
        "hops": len(run.hops), "result_count": len(run.results), "tokens": run.total_tokens,
        "completed": run.terminal, "session_id": session.session_id}))
    store.close()


if __name__ == "__main__":
    asyncio.run(execute())
