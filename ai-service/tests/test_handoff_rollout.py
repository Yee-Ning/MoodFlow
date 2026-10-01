from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from app.agent.handoff.rollout import select_chat_graph
from tests.test_jev_decision_layer import settings


def configuration(**updates):
    return SimpleNamespace(
        **{
            "agent_graph_mode": "foundation",
            "handoff_canary_enabled": False,
            "handoff_canary_percentage": 10,
            "handoff_canary_salt": "moodflow-handoff-v1",
            **updates,
        }
    )


def test_default_configuration_is_ten_percent_but_closed():
    value = settings()
    assert value.handoff_canary_percentage == 10
    assert value.handoff_canary_enabled is False
    assert {select_chat_graph(value, f"session-{index}") for index in range(1000)} == {
        "foundation"
    }


def test_session_assignment_is_stable_across_calls_and_settings_instances():
    first = configuration(handoff_canary_enabled=True)
    second = configuration(handoff_canary_enabled=True)
    assignments = [
        select_chat_graph(first, f"session-{index}") for index in range(10000)
    ]
    assert 850 <= assignments.count("handoff") <= 1150
    assert assignments == [
        select_chat_graph(second, f"session-{index}") for index in range(10000)
    ]


@pytest.mark.parametrize("mode", ["legacy", "handoff"])
def test_explicit_existing_modes_are_preserved(mode):
    assert (
        select_chat_graph(
            configuration(agent_graph_mode=mode, handoff_canary_enabled=True), "known"
        )
        == mode
    )


def test_missing_session_zero_percent_and_closed_rollback_use_foundation():
    assert (
        select_chat_graph(configuration(handoff_canary_enabled=True), None)
        == "foundation"
    )
    assert (
        select_chat_graph(
            configuration(handoff_canary_enabled=True, handoff_canary_percentage=0),
            "known",
        )
        == "foundation"
    )
    assert (
        select_chat_graph(
            configuration(handoff_canary_enabled=True, handoff_canary_percentage=100),
            "known",
        )
        == "handoff"
    )
    assert (
        select_chat_graph(
            configuration(handoff_canary_enabled=False, handoff_canary_percentage=100),
            "known",
        )
        == "foundation"
    )


@pytest.mark.parametrize("percentage", [-1, 101])
def test_percentage_validation_rejects_invalid_configuration(percentage, monkeypatch):
    original = settings()
    values = original.model_dump()
    values.pop("handoff_canary_percentage")
    monkeypatch.setenv("HANDOFF_CANARY_PERCENTAGE", str(percentage))
    with pytest.raises(ValidationError):
        original.__class__(_env_file=None, **values)


@pytest.mark.parametrize(
    "enabled,percentage,expected",
    [(False, 100, "foundation"), (True, 100, "handoff"), (True, 0, "foundation")],
)
def test_chat_api_dispatches_selected_graph_without_changing_public_sse(
    monkeypatch, enabled, percentage, expected
):
    from fastapi.testclient import TestClient
    from unittest.mock import AsyncMock
    from app.main import app
    from app.schemas.chat import FoundationResponse, FinalStreamEvent

    observed = []

    def stream(mode):
        async def invoke(**kwargs):
            observed.append((mode, kwargs["thread_id"]))
            yield FinalStreamEvent(
                response=FoundationResponse(
                    status="success", message="验证回复", results=[]
                )
            )

        return invoke

    monkeypatch.setattr(
        "app.api.chat.get_settings",
        lambda: configuration(
            handoff_canary_enabled=enabled, handoff_canary_percentage=percentage
        ),
    )
    monkeypatch.setattr(
        "app.api.chat.stream_foundation_assistant", stream("foundation")
    )
    monkeypatch.setattr("app.api.chat.stream_handoff_assistant", stream("handoff"))
    monkeypatch.setattr(
        "app.api.chat.UserIdentityProvider.resolve", AsyncMock(return_value=None)
    )
    response = TestClient(app).post(
        "/api/ai/chat", json={"message": "你好", "thread_id": "stable-session"}
    )
    assert response.status_code == 200
    assert observed == [(expected, "stable-session")]
    assert "event: final" in response.text and response.text.endswith(
        "event: done\ndata: {}\n\n"
    )
