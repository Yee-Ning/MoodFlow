"""Transport-neutral execution; a disconnected SSE consumer cannot cancel it."""

import asyncio
from datetime import UTC, datetime

from app.agent.foundation.models import TaskResult
from app.agent.foundation.response import foundation_response
from app.agent.foundation.routing import RouteDecision
from app.agent.handoff.registry import AgentKey
from app.agent.handoff.state import AgentUsage, HandoffRun, RunLimits
from app.agent.handoff.integrity import TransferIntegrityConflict
from app.llm.runtime import BUDGET_LIMIT_MESSAGE, RequestLedger, RuntimeLimitError, request_scope
from app.schemas.chat import ClientActionStreamEvent, FinalStreamEvent, MessageStreamEvent, UpdateStreamEvent
from app.schemas.context import MusicAgentContext

from .accounting import active_accounting
from .coordinator import active_call
from .operations import active_operations
from app.agent.handoff.observability import observe_execution

workers = set()


@observe_execution
async def produce(*, message, thread_id, routing_context, graph_mode="foundation", session_cookie=None,
                  user_id=None, request_id=None, resume=False, new_session=False, decisions=(),
                  coordinator=None, graph=None, telemetry_sample_kind="production", emit=lambda event: None,
                  memory_runtime=None):
    from app.agent.foundation.streaming import get_foundation_graph, get_handoff_graph, _retrieve_query_memories, _extract_memories
    from .coordinator import get_continuity_coordinator

    coordinator = coordinator or get_continuity_coordinator()
    graph = graph or (get_handoff_graph() if graph_mode == "handoff" else get_foundation_graph())
    # Preserve injected savers, while giving old compiled graphs the same hooks
    # as the production factories. Never replace an existing persistence backend.
    from app.agent.checkpointing import create_memory_checkpointer
    from app.agent.handoff.continuity import JournalCheckpointer
    from .coordinator import ContinuityCheckpointer
    saver = graph.checkpointer if graph.checkpointer is not None else create_memory_checkpointer()
    adapters, current = [], saver
    while current is not None:
        adapters.append(current)
        current = getattr(current, "saver", None)
    if graph_mode == "handoff" and not any(isinstance(adapter, JournalCheckpointer) for adapter in adapters):
        saver = JournalCheckpointer(saver, coordinator.journal)
    if not any(isinstance(adapter, ContinuityCheckpointer) for adapter in adapters):
        saver = ContinuityCheckpointer(saver, coordinator)
    graph.checkpointer = saver
    call = await coordinator.begin_call(graph=graph, graph_mode=graph_mode, thread_id=thread_id, message=message,
        routing_context=routing_context, user_id=user_id, request_id=request_id, resume=resume,
        new_session=new_session, decisions=decisions, session_cookie=session_cookie)
    token = active_call.set(call)
    accounting_token = active_accounting.set(call.accounting)
    operations_token = active_operations.set(call.operations)
    emitted = set()
    loop = asyncio.get_running_loop()
    def action_sink(action):
        if action.action_id not in emitted:
            emitted.add(action.action_id)
            loop.call_soon_threadsafe(emit, ClientActionStreamEvent(action=action))
    call.action_sink = action_sink

    async def heartbeat():
        while True:
            await asyncio.sleep(coordinator.locks.ttl / 3)
            await asyncio.to_thread(call.lease.renew)

    renew = asyncio.create_task(heartbeat())
    budget_scope = None
    try:
        if call.blocked_response:
            response = foundation_response(call.blocked_response, [], needs_clarification=True)
            emit(FinalStreamEvent(response=response))
            return response
        if call.graph_input is not None:
            state_values = call.graph_input
            context = call.graph_input["continuity_context"]
            context["telemetry_sample_kind"] = telemetry_sample_kind
        else:
            row = await graph.checkpointer.aget_tuple(call.config)
            state = await graph.aget_state({**call.config, "configurable": {**call.config["configurable"], "checkpoint_id": row.checkpoint["id"]}})
            if not state.next:
                output = state.values
                return await _final(call, output, emit)
            state_values = state.values
            for raw in state.values.get("task_results", []):
                result = raw if isinstance(raw, TaskResult) else TaskResult.model_validate(raw)
                emitted.update(action["action_id"] for action in result.data.get("client_actions", []))
        # Include auxiliary memory calls as well as graph calls. Handoff's
        # per-node scopes restore these same receipts under the same request ID.
        context = state_values["continuity_context"]
        saved_run = HandoffRun.model_validate(state_values["handoff_run"]) if state_values.get("handoff_run") else None
        limits = saved_run.limits if saved_run else RunLimits()
        started = saved_run.started_at if saved_run else datetime.fromisoformat(context["started_at"])
        remaining = limits.run_timeout_seconds - (datetime.now(UTC) - started).total_seconds()
        ledger = RequestLedger(tuple(AgentUsage(agent=key) for key in AgentKey), AgentKey.GENERAL_CHAT,
            limits, deadline=remaining, run_id=saved_run.run_id if saved_run else context["run_id"])
        budget_scope = request_scope(ledger)
        budget_scope.__enter__()
        if call.graph_input is not None:
            await _retrieve_query_memories(context=context, user_id=user_id, message=message,
                enabled=memory_runtime is None, memory_runtime=memory_runtime, request_id=call.identity.request_id)
        async def run_graph():
            streamed_nodes = set()
            visible_scope = None
            has_visible_text = False
            async for part in graph.astream(call.graph_input, call.config,
                context=MusicAgentContext(session_cookie=session_cookie, user_id=user_id),
                stream_mode=["messages", "updates"], subgraphs=True, version="v2", durability="sync"):
                if part["type"] == "messages":
                    # Only Foundation's two visible answer models stream raw
                    # text. Handoff and tool/control chunks require validation.
                    if graph_mode == "handoff":
                        continue
                    chunk, metadata = part["data"]
                    namespace = part.get("ns", ())
                    scope = namespace[0] if namespace else ""
                    node = scope.split(":", 1)[0]
                    if (metadata.get("langgraph_node") != "agent"
                        or metadata.get("ls_integration") != "langchain_chat_model"
                        or node not in {"current_song_subgraph", "general_chat_subgraph"}):
                        continue
                    delta = str(chunk.text)
                    if delta:
                        if visible_scope != scope and has_visible_text:
                            emit(MessageStreamEvent(delta="\n"))
                        visible_scope = scope
                        has_visible_text = True
                        streamed_nodes.add(node)
                        emit(MessageStreamEvent(delta=delta))
                    continue
                if part["type"] != "updates" or part.get("ns"):
                    continue
                for node, update in part["data"].items():
                    public_node = {"general_chat_agent": "general_chat_subgraph", "curator_agent": "tool_calling_subgraph",
                                   "explorer_agent": "worldline_subgraph"}.get(node, node)
                    if node == "curator_agent" and isinstance(update, dict) and update.get("task_results"):
                        first = update["task_results"][0]
                        first = first if isinstance(first, TaskResult) else TaskResult.model_validate(first)
                        if str(first.route_key) == "current_song":
                            public_node = "current_song_subgraph"
                    if public_node in {"prepare_context", "intent_router", "final_response", "clarify", "aggregate_results", "development_route",
                                       "tool_calling_subgraph", "general_chat_subgraph", "worldline_subgraph", "current_song_subgraph"}:
                        emit(UpdateStreamEvent(node=public_node, status="completed"))
                    if not isinstance(update, dict):
                        continue
                    for raw in update.get("task_results", []):
                        result = raw if isinstance(raw, TaskResult) else TaskResult.model_validate(raw)
                        if result.content and node not in streamed_nodes:
                            if has_visible_text:
                                emit(MessageStreamEvent(delta="\n"))
                            emit(MessageStreamEvent(delta=result.content))
                            has_visible_text = True
                    streamed_nodes.discard(node)
                    visible_scope = None
        await run_graph()
        call.accounting.bind(ledger, ledger.run_id)
        latest_config = {**call.config, "configurable": {key: value for key, value in call.config["configurable"].items() if key != "checkpoint_id"}}
        output = (await graph.aget_state(latest_config)).values
        if not _budget_refused(call, output):
            await _extract_memories(user_id=user_id, thread_id=thread_id, user_message=message,
                                   enabled=bool(message), memory_runtime=memory_runtime,
                                   request_id=call.identity.request_id)
        response = await _final(call, output, emit)
        coordinator.history.archive(call.identity)
        return response
    except RuntimeLimitError as exc:
        if exc.reason != "token_limit":
            raise
        # Recover completed public results from the latest durable checkpoint.
        # A refusal consumes no additional model calls and does not replay tools.
        latest = {**call.config, "configurable": {key: value for key, value in call.config["configurable"].items()
                                                 if key != "checkpoint_id"}}
        values = (await graph.aget_state(latest)).values
        route = RouteDecision.model_validate(values["route_decision"]) if values.get("route_decision") else None
        active = {task.task_id for task in route.tasks} if route else set()
        results = [item if isinstance(item, TaskResult) else TaskResult.model_validate(item)
                   for item in values.get("task_results", [])]
        results = [item for item in results if item.task_id in active]
        response = foundation_response(BUDGET_LIMIT_MESSAGE, results, needs_clarification=True)
        emit(FinalStreamEvent(response=response))
        return response
    except TransferIntegrityConflict:
        response = foundation_response(
            "转交载荷完整性核验失败，已拒绝执行；需要检查原始转交记录。", [], needs_clarification=True,
        )
        emit(FinalStreamEvent(response=response))
        return response
    finally:
        if budget_scope is not None:
            budget_scope.__exit__(None, None, None)
        renew.cancel()
        await asyncio.gather(renew, return_exceptions=True)
        active_operations.reset(operations_token)
        active_accounting.reset(accounting_token)
        active_call.reset(token)
        call.action_sink = None
        await call.close()


def _budget_refused(call, output):
    raw = output.get("handoff_run")
    run_id = raw["run_id"] if raw else output.get("continuity_context", {}).get("run_id")
    if not run_id:
        return False
    return any(
        event.phase == "budget" and event.budget_stage in {"honest_degradation", "refuse"}
        or event.phase == "settled" and event.output_tokens is not None and event.max_output_tokens is not None
        and event.output_tokens > event.max_output_tokens
        for event in call.accounting.events(run_id)
    )


async def _final(call, output, emit):
    from app.agent.foundation.streaming import _public_response

    text = output.get("final_response")
    if not isinstance(text, str) or not text.strip():
        raise RuntimeError("graph returned no visible final response")
    if output.get("handoff_run"):
        results = list(HandoffRun.model_validate(output["handoff_run"]).results)
    else:
        route = output.get("route_decision")
        route = RouteDecision.model_validate(route) if route else None
        active = {task.task_id for task in route.tasks} if route else set()
        results = [item if isinstance(item, TaskResult) else TaskResult.model_validate(item)
                   for item in output.get("task_results", [])]
        results = [item for item in results if item.task_id in active]
    # after_checkpoint has already written the final visible answer; transport
    # replay consumes the same state and does not create a second history event.
    route = output.get("route_decision")
    clarification = RouteDecision.model_validate(route).needs_clarification if route else False
    budget_refused = _budget_refused(call, output)
    if budget_refused:
        text = BUDGET_LIMIT_MESSAGE
        raw = output.get("handoff_run")
        run_id = raw["run_id"] if raw else output["continuity_context"]["run_id"]
        from .models import HistoryEventType
        identity = call.identity.model_copy(update={"run_id": run_id})
        call.coordinator.history.public_text(identity, "budget-final:" + run_id,
                                            HistoryEventType.ASSISTANT_MESSAGE, text)
    response = _public_response(text.strip(), results, needs_clarification=budget_refused or bool(output.get("handoff_needs_input")) or clarification,
                                execution_failed=bool(output.get("handoff_failed")))
    emit(FinalStreamEvent(response=response))
    return response


def spawn_worker(coroutine):
    task = asyncio.create_task(coroutine)
    workers.add(task)
    def completed(done):
        workers.discard(done)
        # Retrieve detached failures. Public error details remain generic.
        if not done.cancelled():
            done.exception()
    task.add_done_callback(completed)
    return task


async def invoke(**kwargs):
    return await asyncio.shield(spawn_worker(produce(**kwargs)))


async def stream(**kwargs):
    queue = asyncio.Queue(maxsize=256)
    attached = True
    def emit(event):
        if not attached:
            return
        if queue.full():
            queue.get_nowait()
        queue.put_nowait(event)
    task = spawn_worker(produce(**kwargs, emit=emit))
    try:
        while not task.done() or not queue.empty():
            if queue.empty():
                ready = asyncio.create_task(queue.get())
                done, _ = await asyncio.wait({ready, task}, return_when=asyncio.FIRST_COMPLETED)
                if ready in done:
                    yield ready.result()
                else:
                    ready.cancel()
                    await asyncio.gather(ready, return_exceptions=True)
            else:
                yield queue.get_nowait()
        await asyncio.shield(task)
    finally:
        attached = False


async def drain_workers(timeout=30):
    pending = list(workers)
    if not pending:
        return
    done, unfinished = await asyncio.wait(pending, timeout=timeout)
    for task in unfinished:
        task.cancel()
    await asyncio.gather(*done, *unfinished, return_exceptions=True)
