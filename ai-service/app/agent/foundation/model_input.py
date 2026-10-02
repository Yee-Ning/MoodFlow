"""Adapt selected ContextPacks to Responses inputs and verify final framing."""

from dataclasses import dataclass

from langchain_core.messages import HumanMessage, SystemMessage

from app.agent.foundation.context_engineering import POLICIES, ContextPack, ContextSelector, build_context_async
from app.llm.client import _provider_compatible_schema
from app.llm.runtime import ProviderBudget, RuntimeLimitError, active_ledger, estimate_tokens


@dataclass(frozen=True)
class BudgetedModelInput:
    pack: ContextPack
    input_items: list[dict]
    instructions: str
    budget: ProviderBudget


def responses_input(pack):
    """These auxiliary calls accept only their system constraints and user JSON."""
    instructions, items = [], []
    for message in pack.messages:
        if isinstance(message, SystemMessage):
            instructions.append(str(message.content))
        elif isinstance(message, HumanMessage):
            items.append({"role": "user", "content": str(message.content)})
        else:
            raise ValueError("unsupported auxiliary model message")
    return items, "\n".join(instructions)


async def prepare_model_input(node, *, schema, schema_name, renderer=responses_input, policy=None, **sources):
    policy = policy or POLICIES[node]
    ledger = active_ledger.get()
    total = min(policy.total_input_budget, ledger.remaining) if ledger else policy.total_input_budget
    budget = ProviderBudget(total - policy.reserved_output_tokens - policy.safety_margin, total, policy.safety_margin)
    framing = {
        "model": "provider-model", "input": [], "instructions": "",
        "reasoning": {"effort": "none"}, "max_output_tokens": policy.reserved_output_tokens,
        "temperature": 0, "text": {"format": {
            "type": "json_schema", "name": schema_name,
            "schema": _provider_compatible_schema(schema), "strict": True,
        }},
    }
    overhead = estimate_tokens(framing) + 64
    available = budget.input_tokens - overhead
    if available <= 0:
        budget.validate(framing, output_tokens=policy.reserved_output_tokens)
        if ledger:
            ledger.blocked = "token_limit"
            raise RuntimeLimitError("token_limit")
        raise ValueError("model schema exceeds input budget")
    selection_policy = policy.model_copy(update={"total_input_budget": available + policy.reserved_output_tokens + policy.safety_margin})
    pack = await build_context_async(node, policy=selection_policy, **sources)
    # JSON rendering can add overhead to tags/framing. Re-select only from the
    # surviving pack; never rebuild from raw sources or resurrect dropped text.
    for _ in range(len(pack.items) + 2):
        items, instructions = renderer(pack)
        framed = {**framing, "input": items, "instructions": instructions}
        excess = estimate_tokens(framed) - budget.input_tokens
        if excess <= 0:
            budget.validate(framed, output_tokens=policy.reserved_output_tokens)
            return BudgetedModelInput(pack, items, instructions, budget)
        available = pack.budget - excess - 32
        if available <= 0:
            break
        selection_policy = selection_policy.model_copy(update={"total_input_budget": available + policy.reserved_output_tokens + policy.safety_margin})
        try:
            pack = ContextSelector().build(pack.items, selection_policy)
        except ValueError:
            if ledger:
                ledger.blocked = "token_limit"
                raise RuntimeLimitError("token_limit") from None
            raise
    budget.validate(framed, output_tokens=policy.reserved_output_tokens)
    raise ValueError("model payload cannot be assembled within budget")
