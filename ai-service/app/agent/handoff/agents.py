"""Agent execution reuses Foundation prompts, context budgets and guarded tools."""

import json

from langchain_core.messages import AIMessage, HumanMessage, ToolMessage

from app.agent.foundation.adapters import create_subgraph_adapter
from app.agent.foundation.agent_pack import AgentPackBuilder, AgentPackInputs, AgentPackInvocation
from app.agent.foundation.hop_sources import build_hop_pack
from app.agent.foundation.models import TaskResult, TaskStatus
from app.agent.foundation.routing import RouteKey
from app.agent.foundation.source_errors import SourceIntegrityError
from app.agent.foundation.subgraphs.current_song.assistant import (
    answer_current_song_task,
)
from app.agent.foundation.subgraphs.general_chat import create_general_chat_subgraph
from app.agent.foundation.subgraphs.general_chat.results import grounded_response, read_result_receipt
from app.agent.foundation.subgraphs.tool_calling import create_tool_calling_subgraph
from app.agent.foundation.subgraphs.tool_calling.assistant import all_tool_calling_tools
from app.agent.handoff.song_context import SongContextRead, create_song_context_tool
from app.agent.handoff.search_scope import scoped_search_tools
from app.agent.foundation.subgraphs.tool_calling.scope import scoped_playback_tools
from app.agent.handoff.context import exploration_projection
from app.agent.handoff.explorer import execute_exploration, get_worldline_subgraph
from app.agent.handoff.curator import curation_request, execute_curation
from app.agent.handoff.state import HandoffRun
from app.agent.handoff.hop_context import active_hop
from app.agent.handoff.registry import next_agent_for_task
from app.agent.handoff.progress import create_combination_status_tool, latest_combination_progress
from app.agent.handoff.tools import create_transfer_tools
from app.llm.client import get_base_chat_model
from app.llm.runtime import instrument_chat_model


def task_view(state: dict, run: HandoffRun) -> dict:
    # Control calls stay paired in checkpoint history, but never enter a
    # Foundation text-only prompt (which would discard their ToolMessages).
    history = [
        item
        for item in state["messages"]
        if not isinstance(item, ToolMessage)
        and not (
            isinstance(item, AIMessage) and (item.tool_calls or item.invalid_tool_calls)
        )
    ]
    active_ids = {task.task_id for task in run.plan.tasks}
    prior_results = [
        result
        for result in state.get("task_results", [])
        if result.task_id not in active_ids
    ]
    continuity = dict(state.get("continuity_context", {}))
    projection = exploration_projection(run.results)
    if projection is not None:
        continuity["handoff"] = {
            "window_handoff": continuity.get("handoff"),
            "completed_exploration": projection,
        }
    return {
        **state,
        "messages": history,
        "route_decision": run.plan,
        "current_input": run.original_input,
        # Adapter dispatch must use this run's completion prefix. Keep older
        # entity context only when it cannot masquerade as a completed task.
        "task_results": [*prior_results, *run.results],
        "continuity_context": continuity,
    }


class _RelayCollector:
    """Keep budget preprocessing from importing ambient business continuity."""

    def __init__(self, delegate):
        self.delegate = delegate

    def collect(self, **sources):
        return self.delegate.collect(
            query=sources["query"], system=sources["system"],
            business_state=sources.get("business_state"),
            registered_items=sources.get("registered_items"),
        )


async def choose_transfer(
    state: dict, run: HandoffRun, *, model=None, feedback=None
) -> AIMessage:
    tools = create_transfer_tools(run.current_agent, run)
    task = run.pending_tasks[0]
    invocation = AgentPackInvocation(
        owner_agent=run.current_agent.value, run_id=run.run_id, task=task,
        recipe_id="handoff", recipe_version="v1", phase="relay",
        prompt_refs=("handoff-transfer/v1",) + (("transfer-retry/v1",) if feedback else ()),
    )
    hop = active_hop.get()
    if hop is not None:
        hop.require_current()
        if (hop.run_id, hop.owner_agent, hop.task_json) != (
            run.run_id, run.current_agent.value, task.model_dump_json()
        ):
            raise SourceIntegrityError()
    builder = AgentPackBuilder()
    builder.collector = _RelayCollector(builder.collector)
    if hop is not None and (hop.committed or hop.envelope_json is None):
        # B selects only route_path. A missing incoming Envelope remains empty.
        pack = build_hop_pack(invocation, builder=builder).pack
    else:
        # An incoming Envelope without a Journal is not a formal Source.
        # Preserve A's runtime-only path without inventing a commit observation.
        if hop is not None and (hop.store is not None or hop.identity is not None or hop.envelope_audit is not None):
            raise SourceIntegrityError()
        target = next_agent_for_task(run.current_agent, task.route_key)
        invocation = invocation.model_copy(update={"inputs": AgentPackInputs(business_state={
            "task_id": task.task_id, "route_key": task.route_key.value,
            "current_agent": run.current_agent.value, "next_agent": target.value if target is not None else None,
        })})
        pack = builder.build(invocation, intent={"routes": [task.route_key.value]})
    response = (
        await instrument_chat_model(model or get_base_chat_model())
        .bind_tools(tools)
        .ainvoke(pack.messages)
    )
    if not isinstance(response, AIMessage):
        raise ValueError("transfer model returned an invalid message")
    # Explorer supplies its server-built queue in the existing summary slot.
    # All other agents, transfer targets, tasks and guard paths are unchanged.
    from app.agent.handoff.registry import AgentKey
    if run.current_agent is AgentKey.EXPLORER:
        from app.agent.handoff.journey import attach_journey_transfer
        response = attach_journey_transfer(response, run)
    from app.agent.handoff.chain_receivers import attach_receiver_summary

    response = attach_receiver_summary(response, run)
    return response


async def execute_owned_task(
    state: dict,
    run: HandoffRun,
    runtime,
    *,
    general_model=None,
    curator_model=None,
    song_model=None,
    worldline_subgraph=None,
    provider=None,
    tool_tools=None,
    subgraphs=None,
) -> TaskResult:
    task = run.pending_tasks[0]
    view = task_view(state, run)
    if subgraphs and task.route_key in subgraphs:
        output = await create_subgraph_adapter(
            task.route_key, subgraphs[task.route_key]
        )(view, runtime)
        return output["task_results"][0]
    context_tool = create_song_context_tool(
        state["routing_context"], run.results, provider=provider
    )
    if task.route_key is RouteKey.WORLDLINE:
        return await execute_exploration(
            view,
            run,
            runtime,
            graph=worldline_subgraph or get_worldline_subgraph(),
            context_tool=context_tool,
        )
    if task.route_key is RouteKey.CURRENT_SONG:
        from app.agent.handoff.song_context import invoke_song_context
        read = SongContextRead.model_validate_json(await invoke_song_context(context_tool, {}))
        if read.status is not TaskStatus.SUCCESS:
            return TaskResult(
                task_id=task.task_id,
                route_key=task.route_key,
                status=read.status,
                content=read.message,
                error=read.message if read.status is TaskStatus.FAILED else None,
                data={"reason": read.reason},
            )
        response = await answer_current_song_task(
            view["messages"],
            task.input_text,
            read.song,
            state["routing_context"],
            model=instrument_chat_model(song_model or get_base_chat_model()),
            continuity_context=view.get("continuity_context"),
            route_task_id=task.task_id,
        )
        if (
            response.tool_calls
            or response.invalid_tool_calls
            or not response.text.strip()
        ):
            raise ValueError("song analysis must be a nonempty read-only answer")
        return TaskResult(
            task_id=task.task_id,
            route_key=task.route_key,
            status=TaskStatus.SUCCESS,
            content=response.text.strip()[:2_000],
            data={
                "song": {
                    "song_id": read.song.song_id,
                    "name": read.song.name,
                    "artist": read.song.artist,
                    "album": read.song.album,
                    "lyrics_status": read.song.lyrics_status,
                },
                "target_source": read.target_source,
                "rag_used": False,
            },
        )
    if task.route_key is RouteKey.TOOL_CALLING:
        from app.agent.handoff.journey_consumer import consume_journey

        consumed = await consume_journey(
            state,
            run,
            runtime,
            tools=tool_tools if tool_tools is not None else all_tool_calling_tools(),
        )
        if consumed is not None:
            return consumed
        request = curation_request(task.input_text)
        if request is not None:
            return await execute_curation(
                task, view, runtime, request=request,
                model=instrument_chat_model(curator_model or get_base_chat_model()),
                tools=tool_tools if tool_tools is not None else all_tool_calling_tools(),
            )
        # Bind the same allowlist to model and executor, including the read tool.
        graph = create_tool_calling_subgraph(
            model=instrument_chat_model(curator_model or get_base_chat_model()),
            tools=scoped_playback_tools(task.input_text, scoped_search_tools(task.input_text, [
                *(tool_tools if tool_tools is not None else all_tool_calling_tools()),
                context_tool,
            ])),
        )
    else:
        from app.agent.handoff.chain_receivers import receive_general_chat

        async def chat(messages, text, routing_context, prior_results):
            received = await receive_general_chat(state, run)
            if received is not None:
                return received

            from app.agent.foundation.long_horizon.write_reconciliation import (
                answer_write_status_query,
            )
            from app.agent.foundation.long_horizon.recovery_receipts import (
                answer_recovery_query,
            )
            from app.agent.foundation.subgraphs.general_chat.session import (
                preference_context,
                preference_safe_history,
                session_for,
            )
            from app.agent.foundation.subgraphs.general_chat.song_dialogue import (
                is_song_conversation,
                pending_switch,
                render_song_read,
                safe_song_payload,
                unavailable_song_response,
            )

            answer = await answer_recovery_query(
                text
            ) or await answer_write_status_query(text)
            if answer is not None:
                return AIMessage(content=answer)
            history = preference_safe_history(messages)
            if history and isinstance(history[-1], HumanMessage):
                history.pop()
            status_tool = create_combination_status_tool(view.get("task_results", []))
            receipt = await read_result_receipt(view.get("task_results", []))
            session = session_for(view.get("continuity_context"))
            song_read = None
            if is_song_conversation(text) or session.song_choices:
                switching = pending_switch(receipt, prior_results)
                if switching is not None:
                    return switching
            read_tools = {context_tool.name: context_tool}
            progress = latest_combination_progress(view.get("task_results", []))
            if progress and len(progress["tasks"]) > 1:
                read_tools[status_tool.name] = status_tool
            bound = instrument_chat_model(
                general_model or get_base_chat_model()
            ).bind_tools(list(read_tools.values()))
            for attempt in range(3):
                from app.agent.foundation.agent_pack import AgentPackInputs
                from app.agent.foundation.agent_history_source import bind_produced_message
                from app.agent.foundation.business_pack import business_invocation, build_business_pack

                invocation = business_invocation(RouteKey.GENERAL_CHAT, text,
                    phase="answer" if attempt == 0 else "read_observation",
                    prompt_refs=("general-chat-base/v1", "general-chat-handoff-read/v1",
                                 "general-chat-result-evidence/v1", "general-chat-preferences/v1"),
                    route_task_id=task.task_id,
                    invocation_run_id=run.run_id,
                    inputs=AgentPackInputs(messages=history,
                    business_state=routing_context.model_dump_json(),
                    structured_output=json.dumps(
                        {
                            "result_receipt": receipt.projection(),
                            **preference_context(session),
                            "prior_task_results": [
                                {
                                    "status": item.status.value,
                                    "content": (item.content or "")[:800],
                                }
                                for item in prior_results[-8:]
                            ],
                        },
                        ensure_ascii=False,
                    ),
                    continuity_context=view.get("continuity_context")))
                pack = build_business_pack(invocation)
                response = await bound.ainvoke(pack.messages)
                if not isinstance(response, AIMessage) or response.invalid_tool_calls:
                    raise ValueError("invalid chat message")
                if not response.tool_calls:
                    if (
                        is_song_conversation(text)
                        or session.song_choices
                        or song_read is not None
                    ):
                        from app.agent.handoff.song_context import invoke_song_context

                        try:
                            if song_read is None:
                                song_read = await invoke_song_context(context_tool, {})
                            return render_song_read(song_read, text, receipt, session)
                        except Exception:
                            return unavailable_song_response()
                    return grounded_response(response, text, receipt)
                if attempt == 2 or len(response.tool_calls) != 1:
                    raise ValueError("chat context tool limit exceeded")
                call = response.tool_calls[0]
                if call["name"] not in read_tools or not call.get("id"):
                    raise ValueError("chat tool is outside its read-only domain")
                from app.agent.handoff.song_context import invoke_song_context

                if call["name"] == context_tool.name:
                    try:
                        content = await invoke_song_context(
                            context_tool, call["args"], tool_call_id=call["id"]
                        )
                        song_read = content
                        content = safe_song_payload(content)
                    except Exception:
                        return unavailable_song_response()
                else:
                    content = json.dumps(
                        await status_tool.ainvoke(call["args"]), ensure_ascii=False
                    )
                history.extend(
                    [
                        bind_produced_message(response, invocation),
                        bind_produced_message(ToolMessage(
                            content=content,
                            tool_call_id=call["id"],
                            name=call["name"],
                        ), invocation),
                    ]
                )
            raise RuntimeError("unreachable chat loop")

        graph = create_general_chat_subgraph(assistant=chat)
    output = await create_subgraph_adapter(task.route_key, graph)(view, runtime)
    return output["task_results"][0]
