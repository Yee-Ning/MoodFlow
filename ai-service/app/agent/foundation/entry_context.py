"""Rule preflight, bounded triage, then intent-aware L1 packing."""

from contextlib import contextmanager
from contextvars import ContextVar
from uuid import uuid4

from langchain_core.messages import AIMessage, HumanMessage

from app.agent.foundation.context_engineering import (
    ContextAssembler, ContextCollector, ContextPack, ContextTrace,
    ContextTraceEntry, _truncate, build_context, context_manifest,
)
from app.agent.foundation.context_sources import SourceRegistry, SourceRequest, load_context_policy
from app.agent.foundation.intent_merging import IntentMerger, RouteDecisionBuilder
from app.agent.foundation.routing import RouteDecision
from app.agent.foundation.rule_routing import RuleRouter
from app.rag.chunk.token_counter import UnicodeHeuristicTokenCounter

active_triage_pack: ContextVar[ContextPack | None] = ContextVar("entry_triage_pack", default=None)


@contextmanager
def triage_scope(pack):
    token = active_triage_pack.set(pack)
    try:
        yield
    finally:
        active_triage_pack.reset(token)


def rule_preflight(text, router=None):
    match = (router or RuleRouter()).route(text)
    if not match.is_complete:
        return None
    return RouteDecisionBuilder().build(text, IntentMerger().merge(text, match, []), run_id=uuid4().hex)


def intent_section(decision: RouteDecision):
    return {"routes": list(dict.fromkeys(task.route_key.value for task in decision.tasks)),
            "intents": [{"intent": item.intent.value, "confidence": item.confidence,
                         "source": item.source.value} for item in decision.intents]}


def build_lite_pack(state, *, budget=None):
    config = load_context_policy()["lite"]
    limit = config["input_tokens"] if budget is None else budget
    counter, assembler = UnicodeHeuristicTokenCounter(), ContextAssembler()
    continuity = state.get("continuity_context", {})
    history = [message for message in state.get("messages", []) if isinstance(message, (AIMessage, HumanMessage))]
    if history and isinstance(history[-1], HumanMessage) and history[-1].text.strip() == state["current_input"].strip():
        history = history[:-1]
    # Lite intentionally excludes tool protocols and privileged full sources.
    history = [HumanMessage(content=message.text) if isinstance(message, HumanMessage)
               else AIMessage(content=message.text) for message in history[-config["history_messages"]:]]
    collected = ContextCollector(counter).collect(query=state["current_input"], system="",
        messages=history, handoff_projection=continuity.get("handoff"))
    selected, trace = [], []

    def total(items):
        return sum(counter.count(str(message.content)) for message in assembler.assemble(items))

    # Reserve the current query first, then projection, then the newest turns.
    ordered = sorted(collected, key=lambda item: (item.type != "user_query", item.type != "handoff_projection"))
    optional_history = [item for item in ordered if item.type == "conversation"]
    ordered = [item for item in ordered if item.type != "conversation"] + list(reversed(optional_history))
    for item in ordered:
        available = max(0, limit - total(selected + [item.model_copy(update={"content": ""})]))
        if item.type == "handoff_projection":
            available = min(available, config["projection_tokens"])
        shortened = _truncate(item.content, available, counter)
        updated = item.model_copy(update={"content": shortened, "token_count": counter.count(shortened)})
        if shortened:
            selected.append(updated)
        trace.append(ContextTraceEntry(item_id=item.id, stage="final" if shortened == item.content else "dropped",
            reason="lite_included" if shortened == item.content else "lite_truncated",
            token_before=item.token_count, token_after=updated.token_count))
    # Restore chronological order for conversation; Assemble puts current input last.
    selected.sort(key=lambda item: collected.index(next(raw for raw in collected if raw.id == item.id)))
    messages = assembler.assemble(selected)
    tokens = total(selected)
    if tokens > limit:
        raise ValueError("lite context exceeds input budget")
    return ContextPack(items=selected, total_tokens=tokens, budget=limit,
        dropped_items=[row.item_id for row in trace if row.token_after == 0], compressed_items=[],
        messages=messages, trace=ContextTrace(entries=trace),
        manifest=context_manifest(collected, selected, trace, tokens, limit))


class EntryContextEngine:
    def __init__(self, *, registry=None, profile_store=None, history_store=None, rule_router=None):
        self.registry = registry or SourceRegistry()
        self.profile_store = profile_store
        self.history_store = history_store
        self.rule_router = rule_router

    def preflight(self, state):
        return rule_preflight(state["current_input"], self.rule_router)

    async def complete(self, state, decision, *, lite=None):
        import asyncio
        from app.llm.runtime import active_ledger
        ledger = active_ledger.get()
        if ledger is not None:
            ledger.check_task_budget()
            if ledger.usage[ledger.current_agent]["tokens"] >= ledger.agent_limit:
                ledger.fail_budget("l2")
        context = dict(state.get("continuity_context", {}))
        history_store = self.history_store
        if history_store is None and context.get("session_id"):
            from app.agent.foundation.long_horizon.runtime import get_long_horizon_service
            history_store = get_long_horizon_service().store
        candidates, statuses = await asyncio.to_thread(self.registry.collect, SourceRequest(
            state["current_input"], context, self.profile_store, history_store,
            allow_new_sources=ledger is None or ledger.current_agent not in ledger.source_stops,
        ))
        section = intent_section(decision)
        context.update(entry_source_audit=getattr(candidates, "source_audit", {}),
                       entry_candidates=[item.model_dump(mode="json") for item in candidates], entry_intent=section)
        packs = {}
        # Multi-intent entry uses each route's existing L1 policy, never a policy union.
        for route in dict.fromkeys(task.route_key for task in decision.tasks):
            pack = build_context(route, system="Context for the current MoodFlow task.",
                query=state["current_input"], messages=state.get("messages", []),
                continuity_context=context, intent={**section, "routes": [route.value]})
            packs[route.value] = pack.manifest
        return {"route_decision": decision, "continuity_context": context,
                "context_manifest": {"lite": lite.manifest if lite is not None else None,
                                     "full": packs, "source_status": statuses},
                "entry_intent": section}
