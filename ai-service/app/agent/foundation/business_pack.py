"""GeneralChat/Curator recipes using unchanged A/B pack interfaces."""

import json
from uuid import uuid4

from app.agent.foundation.agent_history_source import AgentHistorySource, ScopedHistoryMemorySource
from app.agent.foundation.agent_pack import AgentPackBuilder, AgentPackInputs, AgentPackInvocation
from app.agent.foundation.context_contracts import PackManifest
from app.agent.foundation.context_engineering import POLICIES, ContextItem, ContextPack
from app.agent.foundation.context_sources import SourceRegistry, SourceRequest, UserProfileSource, load_context_policy
from app.agent.foundation.hop_sources import ReceivedEnvelopeSource, SessionViewSource, build_hop_pack
from app.agent.foundation.routing import RouteTask
from app.agent.foundation.source_errors import SourceIntegrityError
from app.agent.handoff.hop_context import active_hop
from app.agent.handoff.registry import CAPABILITY_OWNERS
from app.llm.budget_strategy import prepare_sources
from app.llm.runtime import active_agent, active_ledger, active_manifest


def business_invocation(route, text, *, phase, prompt_refs, inputs, route_task_id=None, invocation_run_id=None):
    owner = CAPABILITY_OWNERS[route].value
    if owner not in {"general_chat", "curator"}:
        raise ValueError("business recipe only supports GeneralChat and Curator")
    ledger, hop = active_ledger.get(), active_hop.get()
    context = inputs.continuity_context or {}
    if hop is not None:
        hop.require_current()
        task, run_id = RouteTask.model_validate_json(hop.task_json), hop.run_id
        if hop.owner_agent != owner or task.route_key != route:
            raise SourceIntegrityError()
    else:
        task = RouteTask(task_id=route_task_id or f"call:{uuid4()}", route_key=route, input_text=text)
        run_id = ledger.run_id if ledger else invocation_run_id or context.get("run_id") or context.get("request_id") or f"call:{uuid4()}"
    if active_agent.get() not in {None, owner}:
        raise SourceIntegrityError()
    return AgentPackInvocation(owner_agent=owner, run_id=run_id, task=task, phase=phase,
        recipe_id=owner, recipe_version="v1", prompt_refs=prompt_refs, inputs=inputs)


def _reference(kind, **values):
    return kind + ":" + json.dumps(values, ensure_ascii=False, separators=(",", ":"), sort_keys=True)


class HistoryContextCollector:
    """Carry Source-verified ownership through the original message collector.

    Read the actual, post-degradation messages so their indices stay aligned.
    Ownership comes only from message metadata, never from matching text.
    """

    def __init__(self, delegate, invocation):
        self.delegate = delegate
        self.invocation = invocation
        self.history = AgentHistorySource(invocation)

    def collect(self, **sources):
        messages = self.history.collect(sources.get("messages"))
        items = self.delegate.collect(**{**sources, "messages": messages})
        private = {f"history:{index}" for index, message in enumerate(messages)
                   if self.history.owns(message.additional_kwargs)}
        private.update(f"tool:{message.tool_call_id}" for message in messages
                       if message.type == "tool" and self.history.owns(message.additional_kwargs))
        return [item.model_copy(update={"references": [*item.references,
                _reference("source_scope", source=item.source, user=self.history.scope["user_id"],
                    agent=self.invocation.owner_agent, run_id=self.invocation.run_id,
                    route_task_id=self.invocation.task.task_id)]})
                if item.id in private else item for item in items]


def _audit(pack, invocation):
    config = load_context_policy()["sources"]
    quota_refs = [_reference("quota", source=name, candidate_limit=config[name]["candidate_limit"],
                            tokens=config[name]["tokens"], **({"excerpt_characters": config[name]["excerpt_characters"]}
                            if "excerpt_characters" in config[name] else {}))
                  for name in ("user_profile", "history_memory", "session_view")]
    quota_refs.append(_reference("quota", source="received_envelope",
        candidate_limit=16 if invocation.owner_agent == "general_chat" else 24,
        tokens=768 if invocation.owner_agent == "general_chat" else 1024))
    items = []
    for item in pack.items:
        refs = list(item.references)
        if item.id == "system":
            refs.extend(quota_refs)
        if item.source in {"user_profile", "history_memory"} and not any(ref.startswith("source_scope:") for ref in refs):
            refs.append(_reference("source_scope", source=item.source,
                user=(invocation.inputs.continuity_context or {}).get("user_id"), agent="shared"))
        items.append(item.model_copy(update={"references": list(dict.fromkeys(refs))}))
    projection = PackManifest.model_validate(pack.manifest)
    refs_by_id = {item.id: tuple(item.references) for item in items}
    audit = projection.agent_context.model_copy(update={"source_selection": tuple(
        row.model_copy(update={"references": refs_by_id[row.item_id]}) for row in projection.agent_context.source_selection)})
    manifest = projection.model_copy(update={"agent_context": audit}).model_dump(mode="json")
    result = ContextPack.model_validate({**pack.__dict__, "items": items, "manifest": manifest})
    active_manifest.set(result.manifest)
    return result


def build_business_pack(invocation):
    """Validate history before degradation or Source reads; select by this task."""
    hop = active_hop.get()
    if hop is not None:
        hop.require_current()
        if (hop.run_id, hop.owner_agent, hop.task_json) != (
            invocation.run_id, invocation.owner_agent, invocation.task.model_dump_json()):
            raise SourceIntegrityError()
    history = AgentHistorySource(invocation)
    messages = history.collect(invocation.inputs.messages)
    context = dict(invocation.inputs.continuity_context or {})
    for name in ("key_events", "long_term_memory", "artifacts"):
        if name in context:
            context[name] = history.filter_rows(context[name])
    # Refresh profile/history through their scoped Sources, never trust an old
    # entry Pack's history candidate as this agent's private history.
    existing = invocation.inputs.registered_items
    if existing is None:
        existing = [ContextItem.model_validate(row) for row in context.get("entry_candidates", [])]
    existing = [item for item in history.filter_rows(existing) if item.source not in {"user_profile", "history_memory"}]
    if any(item.source in {"received_envelope", "session_view"} for item in existing):
        raise SourceIntegrityError()
    inputs = invocation.inputs.model_copy(update={"messages": messages, "continuity_context": context,
        "registered_items": existing, "history_drilldown": history.filter_rows(invocation.inputs.history_drilldown),
        "long_term_memory": history.filter_rows(invocation.inputs.long_term_memory),
        "tool_results": [row for row in invocation.inputs.tool_results or []
                         if isinstance(row, dict) and history.owns(row.get("metadata", row))],
        "artifact_projections": history.filter_rows(invocation.inputs.artifact_projections)})
    invocation = invocation.model_copy(update={"inputs": inputs})
    # Check cumulative limits before any optional Source is fetched.
    from app.agent.foundation.prompt_bindings import bind_prompts
    system, _ = bind_prompts(invocation.prompt_refs, owner=invocation.owner_agent, phase=invocation.phase)
    prepared = prepare_sources({**{key: getattr(inputs, key) for key in AgentPackInputs.model_fields},
        "query": invocation.task.input_text, "system": system}, reserved_output=POLICIES[invocation.task.route_key].reserved_output_tokens)
    prepared.pop("query")
    prepared.pop("system")
    invocation = invocation.model_copy(update={"inputs": AgentPackInputs(**prepared)})
    ledger = active_ledger.get()
    if ledger is not None:
        if (ledger.run_id, ledger.current_agent) != (invocation.run_id, invocation.owner_agent):
            raise SourceIntegrityError()
        ledger.check_task_budget()
        if ledger.remaining <= 0:
            ledger.fail_budget("l2")
    sources = [UserProfileSource(), ScopedHistoryMemorySource(invocation)]
    builder = AgentPackBuilder()
    builder.collector = HistoryContextCollector(builder.collector, invocation)
    trace_context = {key: invocation.inputs.continuity_context[key]
                     for key in ("task_id", "session_id", "request_id")
                     if invocation.inputs.continuity_context and key in invocation.inputs.continuity_context}
    if hop is not None and (hop.committed or hop.envelope_json is None):
        registry = SourceRegistry([ReceivedEnvelopeSource(), SessionViewSource(), *sources])
        pack = build_hop_pack(invocation, registry=registry, builder=builder, trace_context=trace_context).pack
    else:
        # B deliberately makes an Envelope without a Journal non-consumable.
        # Preserve the original runtime-only compatibility path through A,
        # without reading that Envelope or inventing a commit observation.
        if hop is not None and (hop.store is not None or hop.identity is not None or hop.envelope_audit is not None):
            raise SourceIntegrityError()
        store = None
        context = invocation.inputs.continuity_context or {}
        if context.get("session_id") and (ledger is None or invocation.owner_agent not in ledger.source_stops):
            from app.agent.foundation.long_horizon.runtime import get_long_horizon_service
            store = get_long_horizon_service().store
        candidates, _ = SourceRegistry(sources).collect(SourceRequest(invocation.task.input_text, context,
            history_store=store, allow_new_sources=ledger is None or invocation.owner_agent not in ledger.source_stops))
        invocation = invocation.model_copy(update={"inputs": invocation.inputs.model_copy(update={
            "registered_items": [*(invocation.inputs.registered_items or []), *candidates]})})
        pack = builder.build(invocation, intent={"routes": [invocation.task.route_key.value]}, trace_context=trace_context)
    return _audit(pack, invocation)
