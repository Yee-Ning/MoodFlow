from unittest.mock import AsyncMock, Mock

import pytest

from app.agent.foundation.hybrid_routing import HybridIntentRouter
from app.agent.foundation.rule_routing import RuleRouter


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "text,routes",
    [
        ("先暂停播放，然后陪我聊聊", ["tool_calling", "general_chat"]),
        ("先探索周杰伦的音乐世界线，然后暂停播放", ["worldline", "tool_calling"]),
    ],
)
async def test_explicit_sequence_does_not_escalate_a_stray_prefix_to_models(
    text, routes
):
    model, llm = Mock(), AsyncMock()
    decision = await HybridIntentRouter(model, llm).route(text)
    assert decision.is_complete and not decision.needs_clarification
    assert [task.route_key.value for task in decision.tasks] == routes
    assert decision.tasks[1].depends_on == [decision.tasks[0].task_id]
    model.route.assert_not_called()
    llm.route.assert_not_awaited()


@pytest.mark.parametrize(
    "text", ["不要先暂停播放", "别陪我聊聊", "先知的创作背景", "先秦音乐的影响"]
)
def test_negation_and_entity_names_are_not_erased_as_sequence_prefixes(text):
    decision = RuleRouter().route(text)
    assert not decision.is_complete


def test_negated_companionship_does_not_add_an_unrequested_task():
    decision = RuleRouter().route("暂停播放，不要陪我聊聊")
    assert all(intent.sub_intent != "companionship" for intent in decision.intents)
