import json
from pathlib import Path

import pytest

from app.agent.decision.jev.capability_criteria import (
    CONTRACT_PATH, ROUTER_QUESTION_INSTRUCTIONS, project_criteria,
    router_criteria,
)
from app.agent.decision.jev.client import DecisionAnswer, DecisionResponse
from app.agent.decision.jev.router import compare_route
from app.agent.foundation.routing import RouteDecision, RouteKey, RouteTask
from app.config.settings import Settings


def test_contract_projection_has_all_current_capability_boundaries():
    contract = json.loads(CONTRACT_PATH.read_text(encoding="utf-8"))
    criteria = router_criteria()
    assert criteria == project_criteria(contract)
    assert list(criteria) == ["current_song", "tool_calling", "worldline", "general_chat"]
    for spec in contract["routes"]:
        rendered = criteria[spec["route"]]
        for field in ("core_capabilities", "supported_intents", "can_handle",
                      "requires", "cannot_handle", "positive_examples", "negative_examples"):
            assert all(value in rendered for value in spec[field])
    assert "Generic music theory" in criteria["current_song"]
    assert "Music catalog or lyric lookup" in criteria["tool_calling"]
    assert "General music concepts" in criteria["general_chat"]
    assert "Unsupported counterfactual history" in criteria["worldline"]


def test_contract_change_propagates_and_invalid_spec_fails_closed():
    contract = json.loads(CONTRACT_PATH.read_text(encoding="utf-8"))
    contract["routes"][2]["can_handle"].append("unique capability probe")
    assert "unique capability probe" in project_criteria(contract)["general_chat"]
    contract["routes"][2]["can_handle"] = []
    with pytest.raises(ValueError, match="general_chat.can_handle"):
        project_criteria(contract)
    contract = json.loads(CONTRACT_PATH.read_text(encoding="utf-8"))
    contract["routes"].append(contract["routes"][0])
    with pytest.raises(ValueError, match="one spec per route"):
        project_criteria(contract)


@pytest.mark.asyncio
async def test_production_choice_uses_contract_criteria_and_preserves_shadow():
    class CaptureClient:
        async def decide(self, state, questions):
            assert state == {"query": "什么是和声？"}
            question = questions["route"]
            assert question.instructions == ROUTER_QUESTION_INSTRUCTIONS
            assert question.criteria == router_criteria()
            return DecisionResponse(
                "jev-latest", "test-model", "2026-09-29T00:00:00+00:00", 1.0, None,
                {"route": DecisionAnswer("choice", choice="worldline", confidence=.99,
                                          probabilities={key.value: .25 for key in RouteKey})},
            )

    settings = Settings(_env_file=None, api_key="test", base_url="https://example.com", model="test",
                        timeout_seconds=10, SPRING_BOOT_BASE_URL="https://spring.example.com",
                        SPRING_BOOT_TIMEOUT_SECONDS=10)
    settings = settings.model_copy(update={"jev_shadow_mode": True, "jev_router_confidence_threshold": .8})
    baseline = RouteDecision(is_complete=True, coverage=1.0, tasks=[
        RouteTask(task_id="baseline:general_chat", route_key=RouteKey.GENERAL_CHAT,
                  input_text="什么是和声？")
    ])
    assert await compare_route("什么是和声？", baseline, settings=settings, client=CaptureClient()) == baseline


@pytest.mark.asyncio
async def test_missing_contract_preserves_existing_route(monkeypatch):
    def missing_contract():
        raise FileNotFoundError("contract missing")

    class UnusedClient:
        async def decide(self, state, questions):
            raise AssertionError("Jev must not be called without criteria")

    monkeypatch.setattr("app.agent.decision.jev.router.router_criteria", missing_contract)
    settings = Settings(_env_file=None, api_key="test", base_url="https://example.com", model="test",
                        timeout_seconds=10, SPRING_BOOT_BASE_URL="https://spring.example.com",
                        SPRING_BOOT_TIMEOUT_SECONDS=10)
    baseline = RouteDecision(is_complete=True, coverage=1.0, tasks=[
        RouteTask(task_id="baseline:general_chat", route_key=RouteKey.GENERAL_CHAT,
                  input_text="什么是和声？")
    ])
    assert await compare_route("什么是和声？", baseline, settings=settings, client=UnusedClient()) == baseline


def test_production_image_includes_single_contract_file():
    root = Path(__file__).resolve().parents[2]
    dockerignore = (root / ".dockerignore").read_text(encoding="utf-8")
    dockerfile = (root / "deploy/ai.Dockerfile").read_text(encoding="utf-8")
    assert "!ai-service/app/**" in dockerignore
    assert "COPY ai-service/app/ app/" in dockerfile
    assert (root / "ai-service/app/agent/decision/jev/config/router_capability_contract.json").is_file()
