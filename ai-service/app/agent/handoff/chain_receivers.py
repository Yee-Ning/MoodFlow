"""Wire frozen sender data to receivers, using the existing accepted envelope."""

import re

from langchain_core.messages import AIMessage

from app.agent.foundation.models import TaskStatus
from app.agent.foundation.routing import RouteKey
from app.agent.foundation.subgraphs.general_chat.conversation import capability_response
from app.agent.foundation.subgraphs.general_chat.results import (
    read_result_receipt,
    ResultReceipt,
)
from app.agent.foundation.subgraphs.general_chat.session import session_for
from app.agent.handoff.contracts import HandoffEnvelope
from app.agent.handoff.journey import ListeningJourney
from app.agent.handoff.registry import AgentKey


def accepted_envelope(state, run):
    raw = state.get("transfer_envelope")
    if raw is None:
        return None
    envelope = HandoffEnvelope.model_validate(raw)
    hop = run.hops[-1] if run.hops else None
    if (
        hop is None
        or envelope.run_id != run.run_id
        or envelope.original_input != run.original_input
        or envelope.tool_call_id != hop.tool_call_id
        or envelope.source_agent != hop.source_agent
        or envelope.request.target_agent != run.current_agent
        or envelope.active_task != run.pending_tasks[0]
        or [r.task_id for r in envelope.completed_results]
        != [r.task_id for r in run.results]
    ):
        raise ValueError("chain envelope does not match current run progress")
    return envelope


def explorer_result(run):
    return next(
        (
            result
            for result in reversed(run.results)
            if result.route_key == RouteKey.WORLDLINE
            and result.status is TaskStatus.SUCCESS
            and "listening_journey" in result.data
        ),
        None,
    )


def requested_receiver(run):
    """Match the already assigned chat clause, without creating or rerouting tasks."""
    text = run.pending_tasks[0].input_text
    if re.search(r"润色|导语|介绍.*歌单|歌单.*介绍", text):
        return "polish"
    if re.search(
        r"讲解|讲讲|解说|说明|节点|Journey|旅程|世界线|说说刚才", text, re.IGNORECASE
    ):
        return "narration"
    return None


def attach_receiver_summary(message, run):
    """Only the existing summary changes; full data stays in trusted run results."""
    if (
        run.current_agent is not AgentKey.CURATOR
        or message.invalid_tool_calls
        or len(message.tool_calls) != 1
        or message.tool_calls[0].get("name") != "transfer_to_general_chat_agent"
        or not run.results
    ):
        return message
    from app.agent.handoff.contracts import TransferArguments

    arguments = TransferArguments.model_validate(message.tool_calls[0]["args"])
    result = run.results[-1]
    if result.route_key != RouteKey.TOOL_CALLING:
        return message
    kind = requested_receiver(run)
    if kind == "polish" and result.data.get("curation", {}).get("polish_request"):
        summary = f"承接 Curator 任务 {result.task_id} 的歌单导语；完整 polish_request 在服务端已完成任务结果中，效果依 A/C 回执。"
    elif kind == "narration" and result.data.get("journey_consumption"):
        summary = f"讲解 Curator 任务 {result.task_id} 已消费的 Journey 节点；完整 narration 与 sources 在服务端已完成任务结果中，不宣称播放已执行。"
    else:
        return message
    return message.model_copy(
        update={
            "tool_calls": [
                {
                    **message.tool_calls[0],
                    "args": {**arguments.model_dump(), "context_summary": summary},
                }
            ]
        }
    )


async def receive_general_chat(state, run):
    envelope = accepted_envelope(state, run)
    if envelope is None or envelope.source_agent is not AgentKey.CURATOR:
        return None
    if not run.results or run.results[-1].route_key != RouteKey.TOOL_CALLING:
        return None
    sender = run.results[-1]
    curation = sender.data.get("curation", {})
    payload = curation.get("polish_request")
    consumption = sender.data.get("journey_consumption")
    kind = requested_receiver(run)
    if payload:
        if kind != "polish":
            return None
        # Do not expose selected/pending songs as receipted additions.
        accepted = set(curation["accepted_song_ids"])
        songs = payload["songs"]
        if {s["song_id"] for s in songs} != accepted:
            raise ValueError("polish songs differ from receipted additions")
        if any(s not in curation["selected_songs"] for s in songs):
            raise ValueError("polish song was not selected from the catalog")
    elif consumption:
        if kind != "narration":
            return None
        result = explorer_result(run)
        if result is None or result.task_id != consumption["explorer_task_id"]:
            raise ValueError("narration has no original Explorer result")
        journey = ListeningJourney.model_validate(result.data["listening_journey"])
        by_id = {node.node_id: node for node in journey.nodes}
        nodes = [by_id[identity] for identity in consumption["node_ids"]]
        payload = {
            "nodes": [
                {"narration": node.narration.model_dump(mode="json")} for node in nodes
            ],
            "sources": result.data["worldline_result"]["sources"],
        }
    else:
        return None
    receipt = await read_result_receipt(list(run.results))
    structured = [payload]
    if consumption and sender.data.get("client_actions"):
        from app.agent.foundation.long_horizon.operations import active_operations
        from app.agent.foundation.subgraphs.general_chat.results import _action_fact

        effect_refs = [
            _action_fact(action, active_operations.get())
            for action in sender.data["client_actions"]
        ]
        structured.append(
            {
                "songs": [],
                "result_facts": [
                    {"subject": fact.subject, "reference": fact.reference}
                    for fact in effect_refs
                ],
            }
        )
    if curation.get("polish_request"):
        # v1's conversational query reads eight recent writes. For a full polish
        # request, resolve its remaining operation refs using that same A reader.
        from app.agent.foundation.long_horizon.models import HistoryEventType
        from app.agent.foundation.long_horizon.operations import active_operations
        from app.agent.handoff.curator import _operation_fact

        operations = active_operations.get()
        facts = list(receipt.facts)
        if operations is not None:
            proposals = {
                event.payload["operation_id"]: event
                for event in operations.write_events()
                if event.event_type is HistoryEventType.TOOL_CALL
            }
            for claimed in payload["result_facts"]:
                proposal = proposals.get(claimed.get("reference"))
                if proposal is None or proposal.payload["name"] != claimed["subject"]:
                    continue
                _, fact = _operation_fact(
                    operations, proposal.payload["name"], proposal.payload["parameters"]
                )
                if fact.reference == claimed["reference"] and not any(
                    f.reference == fact.reference for f in facts
                ):
                    facts.append(fact)
        receipt = ResultReceipt(tuple(facts), receipt.tasks)
    session = session_for(state.get("continuity_context"))
    response = await capability_response(
        run.pending_tasks[0].input_text,
        state["routing_context"],
        list(run.results),
        receipt,
        session,
        structured_results=structured,
    )
    if (
        payload
        and curation.get("polish_request")
        and response.additional_kwargs.get("general_chat_structured")
    ):
        # Grounded lead-in, never a free-form rewrite of an untrusted draft.
        names = "、".join(f"《{s['name']}》" for s in songs[:3])
        selected = (
            f"围绕{names}等 {len(songs)} 首"
            if len(names) <= 700
            else f"围绕实际选中的 {len(songs)} 首"
        )
        lead = f"这份歌单面向“{curation['scene']}”，以 Curator 的实际目录选择为依据，{selected}已有加歌回执的歌曲展开；场景适配是策展判断。下列曲目和操作状态分别附来源，歌曲是否已确认以只读核对为准。"
        response = AIMessage(
            content=lead + "\n" + response.text,
            additional_kwargs=response.additional_kwargs,
        )
    return response
