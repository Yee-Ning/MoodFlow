"""Atomic optional filtering keeps required refusal and ordinary ranking intact."""

import pytest

from app.agent.foundation.context_engineering import ContextAssembler, ContextItem, ContextSelector, POLICIES
from app.agent.foundation.routing import RouteKey
from app.rag.chunk.token_counter import UnicodeHeuristicTokenCounter


def item(identity, content, **values):
    return ContextItem(id=identity, type="business_state", source="test_candidates",
                       content=content, token_count=UnicodeHeuristicTokenCounter().count(content),
                       **values)


def policy(tokens):
    return POLICIES[RouteKey.GENERAL_CHAT].model_copy(update={
        "total_input_budget": tokens, "reserved_output_tokens": 0,
        "safety_margin": 0, "source_budgets": {},
    })


def test_oversized_optional_does_not_displace_small_item():
    huge = item("huge", "large " * 1000, relevance=1)
    small = item("small", "small", relevance=.1)
    pack = ContextSelector().build([huge, small], policy(64))
    assert [value.id for value in pack.items] == ["small"]
    assert pack.dropped_items == ["huge"]
    assert "large" not in str(pack.messages)


def test_atomic_drop_does_not_claim_a_shared_evidence_identity():
    metadata = {"citation": "source-one", "evidence_id": "shared"}
    huge = item("huge", "large " * 1000).model_copy(update={"type": "rag_evidence", "metadata": metadata})
    small = item("small", "small").model_copy(update={"type": "rag_evidence", "metadata": metadata})
    selected = policy(64).model_copy(update={"allowed_types": frozenset({"rag_evidence"})})
    pack = ContextSelector().build([huge, small], selected)
    assert [value.id for value in pack.items] == ["small"]
    assert "small" not in pack.dropped_items


def test_required_oversized_item_keeps_existing_refusal():
    huge = item("required", "large " * 1000, required=True, compressible=False)
    with pytest.raises(ValueError, match="required context exceeds"):
        ContextSelector().build([huge], policy(64))


def test_exact_single_item_budget_is_inclusive():
    value = item("boundary", "small")
    count = sum(UnicodeHeuristicTokenCounter().count(str(message.content))
                for message in ContextAssembler().assemble([value]))
    assert [row.id for row in ContextSelector().build([value], policy(count)).items] == ["boundary"]
    assert ContextSelector().build([value], policy(count - 1)).dropped_items == ["boundary"]


def test_feasible_items_keep_original_relevance_order():
    high, low = item("high", "a " * 16, relevance=1), item("low", "b " * 16, relevance=.1)
    count = sum(UnicodeHeuristicTokenCounter().count(str(message.content))
                for message in ContextAssembler().assemble([high]))
    pack = ContextSelector().build([high, low], policy(count))
    assert [value.id for value in pack.items] == ["high"]
    assert pack.dropped_items == ["low"]
