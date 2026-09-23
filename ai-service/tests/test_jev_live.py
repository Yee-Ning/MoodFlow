"""Opt-in TypeSafe integration; routine CI never contacts the live API."""

from __future__ import annotations

import os

import pytest
from dotenv import dotenv_values
from typesafe_sdk import Choice

from app.agent.decision.jev.client import JevClient
from app.config.settings import ENV_FILE, Settings


@pytest.mark.asyncio
async def test_live_system_one_choice():
    if os.getenv("JEV_LIVE_TEST") != "1":
        pytest.skip("set JEV_LIVE_TEST=1 to use the real TypeSafe API")
    key = os.getenv("TYPESAFE_API_KEY") or dotenv_values(ENV_FILE).get("TYPESAFE_API_KEY")
    if not key:
        pytest.skip("TYPESAFE_API_KEY is unavailable")
    settings = Settings(
        _env_file=None, api_key="test", base_url="https://example.com",
        model="test", timeout_seconds=10,
        SPRING_BOOT_BASE_URL="http://127.0.0.1:8080", SPRING_BOOT_TIMEOUT_SECONDS=10,
        TYPESAFE_API_KEY=key, JEV_MODEL="jev-latest",
    )
    result = await JevClient(settings).decide(
        "I was charged twice for my subscription.",
        {"category": Choice(instructions="Which category best matches this request?",
                            criteria={"billing": None, "technical": None, "other": None})},
    )
    answer = result.answers["category"]
    assert answer.kind == "choice"
    assert answer.choice in {"billing", "technical", "other"}
    assert answer.confidence is not None and 0 <= answer.confidence <= 1
    assert set(answer.probabilities or {}) == {"billing", "technical", "other"}
    assert result.requested_model == "jev-latest"
    assert result.response_model
    assert result.evaluation_timestamp
    assert result.latency_ms > 0


@pytest.mark.asyncio
async def test_live_probe_skips_without_key(monkeypatch):
    monkeypatch.setenv("JEV_LIVE_TEST", "1")
    monkeypatch.delenv("TYPESAFE_API_KEY", raising=False)
    monkeypatch.setattr("tests.test_jev_live.dotenv_values", lambda path: {})
    with pytest.raises(pytest.skip.Exception):
        await test_live_system_one_choice()
