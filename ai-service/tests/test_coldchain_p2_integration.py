"""Keep receipt grounding and scoped Agent Packs together after integration."""

import logging
from hashlib import sha256

import pytest
from langchain_core.messages import AIMessage, HumanMessage

from app.agent.foundation.agent_history_source import bind_produced_message
from app.agent.foundation.routing import RoutingContext
from app.agent.foundation.prompt_bindings import PROMPT_BINDINGS, bind_prompts
from app.agent.foundation.subgraphs.general_chat.assistant import answer_general_chat
from app.agent.foundation.long_horizon.operations import active_operations
from tests.test_agent_pack_impl_c import BoundaryModel, call_manifests, invocation
from tests.test_general_chat_results_v1 import projected_result


@pytest.mark.asyncio
@pytest.mark.parametrize("claim", [{"ack": "applied"}, {"result_state": "confirmed"}])
async def test_unverified_receipt_stays_generated_with_scoped_pack_and_audit(caplog, claim):
    caplog.set_level(logging.INFO, logger="app.llm.runtime")
    result = projected_result()
    result.data["client_actions"][0].update(claim)
    foreign = bind_produced_message(
        AIMessage(content="FOREIGN_CURATOR_PRIVATE"), invocation("curator")
    )
    query = "刚才暂停完成了吗？"
    model = BoundaryModel(AIMessage(content="已经全部完成，已经在播了。"))
    token = active_operations.set(None)
    try:
        response = await answer_general_chat(
            [foreign, HumanMessage(content=query)], query, RoutingContext(), [result],
            model=model, continuity_context={"user_id": "alice"},
            route_task_id="integration:general-chat",
        )
    finally:
        active_operations.reset(token)

    assert "待客户端执行/确认" in response.text
    assert "已经在播了" not in response.text
    assert len(model.calls) == 1
    prompt = "\n".join(str(message.content) for message in model.calls[0])
    assert "FOREIGN_CURATOR_PRIVATE" not in prompt
    assert "result_facts" in prompt and "generated" in prompt
    manifests = call_manifests(caplog)
    assert len(manifests) == 1
    audit = manifests[0]["agent_context"]
    assert audit["recipe_id"] == "general_chat" and audit["phase"] == "answer"
    assert [row["prompt_id"] for row in audit["prompts"]] == [
        "general-chat-base", "general-chat-result-evidence", "general-chat-preferences",
    ]
    assert audit["prompts"][1]["variant"] == "result-evidence"
    assert audit["prompts"][2]["variant"] == "preferences"
    assert audit["prompts"][2]["version"] == "v1"
    assert audit["prompts"][2]["content_digest"] == (
        "sha256:d077ede03d1941f9c41c81cd4ee4b2d70d5045096c64a26b7dc289dd523b9bc8"
    )


def test_result_evidence_variant_preserves_frozen_v1_and_has_separate_identity():
    base = PROMPT_BINDINGS["general-chat-base/v1"]
    variant = PROMPT_BINDINGS["general-chat-result-evidence/v1"]
    assert base.manifest.content_digest == (
        "sha256:4626a71fee1d1d7adaa979cd27837dbed5c93da9b4e5ab2a28da5b11dc6e38e9"
    )
    assert variant.variant == "result-evidence" and variant.version == "v1"
    assert "result_facts" not in base.text and "result_facts" in variant.text
    digest = "ed669d9597851b7daa15e3e3a2f08d7d1f9d07cf59fdc3ccf3fa9d4fcd7c6256"
    assert sha256(variant.text.encode("utf-8")).hexdigest() == digest
    assert variant.manifest.content_digest == "sha256:" + digest
    combined, audit = bind_prompts((base.reference, variant.reference), owner="general_chat", phase="answer")
    assert combined == base.text + variant.text
    assert audit == (base.manifest, variant.manifest)
    with pytest.raises(ValueError, match="one base prompt"):
        bind_prompts((variant.reference,), owner="general_chat", phase="answer")
    with pytest.raises(ValueError, match="owner and phase"):
        bind_prompts((base.reference, variant.reference), owner="general_chat", phase="relay")
