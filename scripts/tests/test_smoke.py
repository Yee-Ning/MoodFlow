import json
from unittest.mock import Mock

import pytest

from scripts import smoke


def probe(monkeypatch, *, chat_status="success", endpoint_status=200):
    def respond(base, path, payload=None):
        if payload == {"message": ""}:
            return 422, b"{}"
        if payload:
            return 200, json.dumps({"status": chat_status, "message": "reply"}).encode()
        return endpoint_status, b"{}"
    mock = Mock(side_effect=respond)
    monkeypatch.setattr(smoke, "request", mock)
    return mock


def test_default_probe_checks_services_and_validation_without_model_call(monkeypatch):
    mock = probe(monkeypatch)
    assert smoke.main([]) == 0
    assert mock.call_count == 4
    assert mock.call_args.args[2] == {"message": ""}


def test_chat_requires_success_and_nonempty_response(monkeypatch):
    mock = probe(monkeypatch)
    assert smoke.main(["--chat"]) == 0
    assert mock.call_args.args[2]["thread_id"].startswith("smoke-")
    probe(monkeypatch, chat_status="failed")
    with pytest.raises(SystemExit, match="GeneralChat check failed"):
        smoke.main(["--chat"])


def test_unavailable_service_fails_the_probe(monkeypatch):
    probe(monkeypatch, endpoint_status=503)
    with pytest.raises(SystemExit, match="Stack check failed"):
        smoke.main([])
