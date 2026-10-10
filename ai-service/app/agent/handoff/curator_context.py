"""Project catalog evidence, then let the existing Agent Pack select whole rows."""

import json

from langchain_core.messages import HumanMessage, SystemMessage

from app.agent.foundation.agent_pack import AgentPackBuilder, AgentPackInputs
from app.agent.foundation.business_pack import business_invocation
from app.agent.foundation.context_engineering import POLICIES
from app.agent.foundation.context_sources import source_item
from app.agent.foundation.routing import RouteKey
from app.agent.handoff.curator_prompts import PLAN_PROMPT, SELECTION_PROMPT
from app.llm.runtime import ProviderBudget, active_ledger, estimate_tokens, instrument_continuity_model
from app.rag.chunk.token_counter import CallableTokenCounter, UnicodeHeuristicTokenCounter


def candidate_projection(candidate):
    """Keep selection facts and evidence identities; full evidence stays server-side."""
    row = {key: candidate[key] for key in (
        "song_id", "name", "artists", "album", "liked", "discovered", "search_sources",
    ) if key in candidate}
    row["knowledge_sources"] = [{
        **{key: value[key] for key in ("knowledge_id", "kind", "start_year", "end_year") if key in value},
        "sources": [{
            "source_id": source.get("source_id"),
            **{key: source.get("metadata", {}).get(key)
               for key in ("claim_id", "parent_id", "chunk_id")},
        } for source in value.get("sources", [])],
    } for value in candidate.get("knowledge_sources", [])]
    return row


class CurationCandidateCollector:
    def __init__(self, delegate, candidates):
        self.delegate, self.candidates = delegate, candidates

    def collect(self, **sources):
        items = self.delegate.collect(**sources)
        count = len(self.candidates)
        for position, candidate in enumerate(self.candidates):
            projected = candidate_projection(candidate)
            references = [f"catalog:song:{candidate['song_id']}"]
            references.extend(
                str(value["knowledge_id"]) for value in candidate.get("knowledge_sources", [])
            )
            item = source_item(
                f"curation-candidate:{candidate['song_id']}", "business_state",
                {"candidates": [projected]}, source="curation_candidates",
                intents=[RouteKey.TOOL_CALLING.value], references=references,
                metadata={"token_before_projection": self.delegate.counter.count(
                    json.dumps({"candidates": [candidate]}, ensure_ascii=False)),
                    "projection_reason": "catalog_evidence_projection"},
            )
            items.append(item.model_copy(update={
                "token_count": self.delegate.counter.count(item.content),
                "priority": 75, "relevance": (count - position) / count,
                "compressible": False,
            }))
        return items


def prepare_curation_pack(text, prompt, payload, continuity, model):
    reference, phase = {
        PLAN_PROMPT: ("curator-plan/v1", "tool_step"),
        SELECTION_PROMPT: ("curator-selection/v1", "tool_observation"),
    }[prompt]
    candidates = payload.get("candidates", [])
    controls = {key: value for key, value in payload.items() if key != "candidates"}
    invocation = business_invocation(
        RouteKey.TOOL_CALLING, text, phase=phase, prompt_refs=(reference,),
        inputs=AgentPackInputs(messages=[], structured_output=controls,
                              continuity_context=continuity),
    )
    accounted = instrument_continuity_model(model)
    schemas = ([tool.tool_call_schema.model_json_schema() for tool in accounted.tools]
               if accounted.tools else getattr(accounted.model, "kwargs", {}).get("tools", []))
    framing = {"messages": [SystemMessage(content=""), HumanMessage(content="")], "tools": schemas}
    base = POLICIES[RouteKey.TOOL_CALLING]
    ledger = active_ledger.get()
    total = min(base.total_input_budget, ledger.remaining) if ledger else base.total_input_budget
    reserved = max(base.reserved_output_tokens, accounted.output_tokens)
    budget = ProviderBudget(total - reserved - base.safety_margin, total, base.safety_margin)
    # Count serialized content with the same escaping as the final provider gate.
    # Policy only tightens to reserve framing; all dropping stays in ContextSelector.
    counter = CallableTokenCounter(
        lambda value: UnicodeHeuristicTokenCounter().count(json.dumps(value, ensure_ascii=False)),
        name="curation-provider-json:v1",
    )
    policy = base.model_copy(update={
        "total_input_budget": total - estimate_tokens(framing),
        "reserved_output_tokens": reserved,
    })
    builder = AgentPackBuilder(counter)
    builder.collector = CurationCandidateCollector(builder.collector, candidates)
    pack = builder.build(invocation, policy=policy,
                         intent={"routes": [invocation.task.route_key.value]})
    budget.validate({"messages": pack.messages, "tools": schemas}, output_tokens=reserved)
    visible = {int(item.id.split(":", 1)[1]) for item in pack.items
               if item.id.startswith("curation-candidate:")}
    return pack, accounted, visible
