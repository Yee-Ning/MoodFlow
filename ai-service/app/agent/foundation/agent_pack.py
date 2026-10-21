"""Thin invocation-to-pipeline adapter; agent business wiring belongs to later rounds."""

from typing import Any

from langchain_core.messages import BaseMessage
from pydantic import Field

from app.agent.foundation.context_contracts import (
    AgentContextManifest,
    AgentOwner,
    AgentPhase,
    BudgetSnapshot,
    EnvelopeManifest,
    PackManifest,
    ProjectionContract,
    SessionManifest,
    SourceSelectionManifest,
)
from app.agent.foundation.context_engineering import (
    POLICIES,
    ContextCollector,
    ContextItem,
    ContextPack,
    ContextPolicy,
    ContextSelector,
    _call_intent,
    registered_policy,
)
from app.agent.foundation.prompt_bindings import bind_prompts
from app.agent.foundation.routing import RouteTask
from app.llm.budget_strategy import prepare_sources
from app.llm.runtime import RuntimeLimitError, active_ledger, active_manifest, bind_context_pack
from app.rag.chunk.token_counter import TokenCounter, UnicodeHeuristicTokenCounter


class AgentPackInputs(ProjectionContract):
    """Server-validated Collect inputs; no prompt/query override or Source provider.

    Business payload validation remains with the existing producer. This DTO is
    not a public request schema and does not authenticate an Envelope/session.
    """

    messages: list[BaseMessage] | None = None
    current_song: Any = None
    business_state: Any = None
    route_decision: Any = None
    structured_output: Any = None
    search_plan: Any = None
    tool_results: list[Any] | None = None
    rag_pack: Any = None
    rag_required_ids: set[str] | None = None
    handoff_projection: Any = None
    history_drilldown: list[Any] | None = None
    artifact_projections: list[Any] | None = None
    memory_source: Any = None
    long_term_memory: list[Any] | None = None
    continuity_context: dict[str, Any] | None = None
    registered_items: list[ContextItem] | None = None
    triage_items: list[ContextItem] | None = None


class AgentPackInvocation(ProjectionContract):
    owner_agent: AgentOwner
    run_id: str = Field(min_length=1, max_length=128)
    task: RouteTask
    phase: AgentPhase
    recipe_id: str = Field(min_length=1, max_length=128)
    recipe_version: str = Field(min_length=1, max_length=64)
    prompt_refs: tuple[str, ...] = Field(min_length=1)
    inputs: AgentPackInputs = Field(default_factory=AgentPackInputs)
    envelope: EnvelopeManifest | None = None
    session: SessionManifest | None = None


class AgentPackBuilder:
    def __init__(self, counter: TokenCounter | None = None):
        self.counter = counter or UnicodeHeuristicTokenCounter()
        self.collector = ContextCollector(self.counter)
        self.selector = ContextSelector(self.counter)

    def build(self, invocation: AgentPackInvocation, *, policy: ContextPolicy | None = None,
              trace_context: dict[str, str] | None = None, intent: dict[str, Any] | None = None,
              include_agent_context: bool = True) -> ContextPack:
        # Deep copy keeps mutable message/payload containers owned by the caller.
        invocation = invocation.model_copy(deep=True)
        system, prompts = bind_prompts(invocation.prompt_refs, owner=invocation.owner_agent, phase=invocation.phase)
        node = invocation.task.route_key
        selected_policy = registered_policy(policy or POLICIES[node])
        if selected_policy.node != node:
            raise ValueError("Pack policy must match the invocation route")
        ledger = active_ledger.get()
        if ledger is not None and (ledger.run_id != invocation.run_id or ledger.current_agent != invocation.owner_agent):
            raise ValueError("Pack invocation must match the active run and owner")
        if invocation.envelope is not None and invocation.envelope.run_id != invocation.run_id:
            raise ValueError("Envelope observation must match the invocation run")
        sources = {name: getattr(invocation.inputs, name) for name in AgentPackInputs.model_fields}
        sources = prepare_sources({**sources, "query": invocation.task.input_text, "system": system},
                                  reserved_output=selected_policy.reserved_output_tokens)
        # Prompt participates in cumulative checks above, but never in Collect.
        sources.pop("system")
        items = self.collector.collect(system="", **sources)
        if any(item.type == "system" or item.id == "system" for item in items):
            raise ValueError("dynamic inputs cannot supply a static system item")
        items.insert(0, ContextItem(id="system", type="system", source="system", content=system,
                                   token_count=self.counter.count(system), required=True, compressible=False))
        if ledger is not None:
            # Same balance cap as the entry pipeline; dispatch remains the reserve gate.
            selected_policy = selected_policy.model_copy(update={"total_input_budget": max(
                selected_policy.reserved_output_tokens + selected_policy.safety_margin + 1,
                min(selected_policy.total_input_budget, ledger.remaining))})
        try:
            pack = self.selector.build(items, selected_policy, trace_context=trace_context,
                                       intent=_call_intent(node, intent, sources))
        except ValueError:
            if ledger is not None:
                ledger.blocked = "token_limit"
                raise RuntimeLimitError("token_limit") from None
            raise
        if include_agent_context:
            budget = None if ledger is None else BudgetSnapshot(
                owner_remaining=ledger.agent_limit - ledger.usage[invocation.owner_agent]["tokens"] - ledger.reserved,
                run_remaining=ledger.task_limit - sum(row["tokens"] for row in ledger.usage.values()) - ledger.reserved,
                reserved_tokens=ledger.reserved,
                degradation_stages=tuple(row["stage"] for row in ledger.budget_trace if row["agent"] == invocation.owner_agent))
            audit = AgentContextManifest(recipe_id=invocation.recipe_id, recipe_version=invocation.recipe_version,
                owner_agent=invocation.owner_agent, route_key=node.value, phase=invocation.phase, prompts=prompts,
                envelope=invocation.envelope, session=invocation.session, budget_snapshot=budget,
                source_selection=tuple(SourceSelectionManifest(item_id=item.id, source=item.source, type=item.type,
                    references=tuple(item.references), final_tokens=item.token_count,
                    profile_version=item.metadata.get("version") if item.type == "user_profile" else None)
                    for item in pack.items))
            manifest = PackManifest.model_validate({**pack.manifest, "agent_context": audit}).model_dump(mode="json")
            pack = ContextPack.model_validate({**pack.__dict__, "manifest": manifest})
        active_manifest.set(pack.manifest)
        bind_context_pack(pack, selected_policy, self.selector)
        return pack
