"""Cumulative-budget preprocessing, before the unchanged L1 collector."""

import json

from langchain_core.messages import AIMessage, HumanMessage, ToolMessage

from app.llm.budget import policy
from app.llm.runtime import active_ledger
from app.rag.chunk.token_counter import UnicodeHeuristicTokenCounter


def prepare_sources(sources, *, reserved_output):
    ledger = active_ledger.get()
    if ledger is None:
        return sources
    ledger.check_task_budget()
    values = dict(sources)
    counter = UnicodeHeuristicTokenCounter()

    def fits():
        projected = sum(counter.count(str(value)) for value in values.values() if value is not None)
        return projected + reserved_output + 64 <= ledger.remaining

    if fits():
        return values
    task_remaining = ledger.task_limit - sum(row["tokens"] for row in ledger.usage.values()) - ledger.reserved
    # If even required input plus reserved output cannot fit the task, changing
    # an agent's history cannot recover it. Refuse before fetching/model dispatch.
    optional = {"messages", "registered_items", "continuity_context", "long_term_memory", "memory_source", "history_drilldown"}
    required = sum(counter.count(str(value)) for key, value in values.items() if key not in optional and value is not None)
    if required + reserved_output + 64 > task_remaining:
        ledger.fail_budget("l3")
    stages = ("truncate_history", "summarize_history", "stop_sources")
    messages = list(values.get("messages") or [])
    protocol = [message for message in messages if isinstance(message, ToolMessage)
                or isinstance(message, AIMessage) and message.tool_calls]
    plain = [message for message in messages if message not in protocol]
    for stage in stages:
        ledger.budget_event("l2", stage)
        if stage == "truncate_history":
            recent = plain[-policy()["history_messages"]:]
            values["messages"] = [message for message in messages if message in protocol or message in recent]
        elif stage == "summarize_history":
            # Extractive summary is deterministic and consumes no model tokens.
            # Tool protocols, current input and structured constraints stay intact.
            from app.agent.foundation.context_engineering import _truncate
            text = json.dumps([{"role": message.type, "text": message.text}
                               for message in values.get("messages", []) if message not in protocol], ensure_ascii=False)
            summary = _truncate(text, policy()["summary_tokens"], counter)
            values["messages"] = [HumanMessage(content="历史摘录（仅作参考）：" + summary), *protocol] if summary else protocol
        else:
            ledger.source_stops.add(ledger.current_agent)
            context = dict(values.get("continuity_context") or {})
            context["entry_candidates"] = []
            context.pop("long_term_memory", None)
            values.update(continuity_context=context, registered_items=[], long_term_memory=[], memory_source=None,
                          history_drilldown=[])
        if fits():
            return values
    task_remaining = ledger.task_limit - sum(row["tokens"] for row in ledger.usage.values()) - ledger.reserved
    agent_remaining = ledger.agent_limit - ledger.usage[ledger.current_agent]["tokens"] - ledger.reserved
    ledger.fail_budget("l3" if task_remaining <= agent_remaining else "l2")
