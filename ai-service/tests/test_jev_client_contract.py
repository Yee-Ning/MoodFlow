"""Offline wire-contract and safe error observability checks for the real SDK."""

import json
import logging
from unittest.mock import patch

import httpx2
import pytest
from pydantic import SecretStr
from typesafe_sdk import AsyncTypeSafeClient, Choice

from app.agent.decision.jev.client import DecisionResponse, JevClient, JevDecisionError
from app.config.settings import Settings


def configured():
    return Settings(
        _env_file=None, api_key="offline", base_url="http://127.0.0.1:9",
        model="offline", timeout_seconds=5,
        SPRING_BOOT_BASE_URL="http://127.0.0.1:9", SPRING_BOOT_TIMEOUT_SECONDS=5,
    ).model_copy(update={"typesafe_api_key": SecretStr("offline-credential"), "jev_model": "jev-latest"})


QUESTIONS = {"category": Choice(instructions="Choose a category", criteria={"billing": None, "other": None})}
SUCCESS = {
    "model": "jev-1.13.0",
    "answers": {"category": {"type": "choice", "choice": "billing", "confidence": 1.0,
                              "probabilities": {"billing": 1.0, "other": 0.0}}},
    "usage": {"input_tokens": 30, "output_tokens": 12},
}


def sdk_with(handler):
    def create(**kwargs):
        return AsyncTypeSafeClient(**kwargs, base_url="https://api.typesafe.ai",
                                  transport=httpx2.MockTransport(handler))
    return create


@pytest.mark.asyncio
async def test_real_sdk_wire_contract_and_decision_response():
    requests = []

    def respond(request):
        requests.append(request)
        assert request.method == "POST"
        assert str(request.url) == "https://api.typesafe.ai/v1/systemone"
        assert request.headers["authorization"] == "Bearer offline-credential"
        assert request.headers["content-type"] == "application/json"
        assert json.loads(request.content) == {
            "state": "Charged twice", "model": "jev-latest",
            "questions": {"category": {"type": "choice", "instructions": "Choose a category",
                                        "criteria": {"billing": None, "other": None}}},
        }
        return httpx2.Response(200, json=SUCCESS)

    with patch("app.agent.decision.jev.client.AsyncTypeSafeClient", sdk_with(respond)):
        result = await JevClient(configured()).decide("Charged twice", QUESTIONS)
    assert len(requests) == 1
    assert isinstance(result, DecisionResponse)
    assert result.answers["category"].choice == "billing"
    assert result.usage == {"input_tokens": 30, "output_tokens": 12}


@pytest.mark.asyncio
@pytest.mark.parametrize("status,category", [(400, "TypeSafeBadRequestError"), (403, "TypeSafePermissionDeniedError"),
                                             (500, "TypeSafeInternalServerError")])
async def test_upstream_error_fields_logged_without_secrets_or_retry(caplog, status, category):
    attempts = []

    def respond(request):
        attempts.append(request)
        return httpx2.Response(status, json={"detail": {
            "error_type": "api_usage_error", "message": "Invalid request.\noffline-credential Bearer hidden token=private",
            "request": "not-for-logs", "api_key": "not-for-logs",
        }})

    with caplog.at_level(logging.WARNING, logger="app.agent.decision.jev.client"):
        with patch("app.agent.decision.jev.client.AsyncTypeSafeClient", sdk_with(respond)):
            with pytest.raises(JevDecisionError, match=f"^{category}$"):
                await JevClient(configured()).decide("Charged twice", QUESTIONS)
    assert len(attempts) == 1
    message = next(record.getMessage() for record in caplog.records
                   if record.name == "app.agent.decision.jev.client")
    assert f"http_status={status}" in message
    assert "upstream_error_type=api_usage_error" in message
    assert "upstream_message=Invalid request." in message
    assert all(secret not in message for secret in ("offline-credential", "hidden", "private", "not-for-logs", "\n"))


@pytest.mark.asyncio
@pytest.mark.parametrize("body", [None, "offline-credential", {"detail": "offline-credential"},
                                 {"detail": {"message": {"secret": "offline-credential"}}}])
async def test_unstructured_error_body_is_not_logged(caplog, body):
    with caplog.at_level(logging.WARNING, logger="app.agent.decision.jev.client"):
        with patch("app.agent.decision.jev.client.AsyncTypeSafeClient", sdk_with(
            lambda request: httpx2.Response(400, json=body)
        )):
            with pytest.raises(JevDecisionError, match="^TypeSafeBadRequestError$"):
                await JevClient(configured()).decide("Charged twice", QUESTIONS)
    message = next(record.getMessage() for record in caplog.records
                   if record.name == "app.agent.decision.jev.client")
    assert "http_status=400" in message
    assert "offline-credential" not in message


@pytest.mark.asyncio
async def test_transport_error_does_not_log_exception_text(caplog):
    def fail(request):
        raise httpx2.ConnectError("offline-credential")

    with caplog.at_level(logging.WARNING, logger="app.agent.decision.jev.client"):
        with patch("app.agent.decision.jev.client.AsyncTypeSafeClient", sdk_with(fail)):
            with pytest.raises(JevDecisionError, match="^TypeSafeAPIConnectionError$"):
                await JevClient(configured()).decide("Charged twice", QUESTIONS)
    message = next(record.getMessage() for record in caplog.records
                   if record.name == "app.agent.decision.jev.client")
    assert "http_status=None" in message
    assert "offline-credential" not in message
