import json
from unittest.mock import Mock
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from threading import Thread

import pytest

from scripts import smoke


def test_local_probe_bypasses_ambient_proxy_and_preserves_http_errors(monkeypatch):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_GET(self):
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b"ok")

        def do_POST(self):
            self.send_response(422)
            self.end_headers()

    for key in ("HTTP_PROXY", "http_proxy", "HTTPS_PROXY", "https_proxy"):
        monkeypatch.setenv(key, "http://127.0.0.1:1")
    for key in ("NO_PROXY", "no_proxy"):
        monkeypatch.setenv(key, "")
    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    worker = Thread(target=server.serve_forever, daemon=True)
    worker.start()
    try:
        base = f"http://127.0.0.1:{server.server_port}"
        assert smoke.request(base, "/healthz") == (200, b"ok")
        assert smoke.request(base, "/api/ai/foundation", {"message": ""})[0] == 422
    finally:
        server.shutdown()
        server.server_close()
        worker.join(timeout=5)


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
