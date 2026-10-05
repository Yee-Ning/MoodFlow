"""L1 budget gate for the final System One state and question framing."""

import json

from app.agent.foundation.context_engineering import ContextAssembler, ContextItem, ContextSelector, POLICIES
from app.agent.foundation.entry_context import active_triage_pack
from app.agent.foundation.routing import RouteKey
from app.llm.runtime import ProviderBudget, RuntimeLimitError, active_ledger, estimate_tokens
from app.rag.chunk.token_counter import UnicodeHeuristicTokenCounter


def prepare_decision_input(state, questions):
    counter = UnicodeHeuristicTokenCounter()
    policy = POLICIES[RouteKey.GENERAL_CHAT]
    ledger = active_ledger.get()
    total = min(policy.total_input_budget, ledger.remaining) if ledger else policy.total_input_budget
    budget = ProviderBudget(total - 256 - policy.safety_margin, total, policy.safety_margin)
    overhead = estimate_tokens({"state": {}, "questions": dict(questions)}) + 64
    text = json.dumps(state, ensure_ascii=False, default=str)
    items = [ContextItem(id="decision_request", type="user_query", source="user_query",
        content=text, token_count=counter.count(text), required=True, compressible=False)]
    lite = active_triage_pack.get()
    if lite is not None:
        items.extend(ContextAssembler().triage_items(lite))
    available = budget.input_tokens - overhead
    assembler = ContextAssembler()
    for _ in range(len(items) + 2):
        selected_policy = policy.model_copy(update={"total_input_budget": max(1, available),
            "reserved_output_tokens": 0, "safety_margin": 0})
        try:
            pack = ContextSelector(counter).build(items, selected_policy)
        except ValueError:
            if ledger is not None:
                ledger.blocked = "token_limit"
                raise RuntimeLimitError("token_limit") from None
            raise
        rendered = assembler.decision_state(pack)
        payload = {"state": rendered, "questions": dict(questions)}
        excess = estimate_tokens(payload) - budget.input_tokens
        if excess <= 0:
            budget.validate(payload, output_tokens=256)
            return rendered
        available = pack.budget - excess - 32
        items = pack.items
    budget.validate(payload, output_tokens=256)
    raise ValueError("decision input exceeds token budget")
