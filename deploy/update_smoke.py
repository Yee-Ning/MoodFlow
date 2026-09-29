"""Strict-TLS public smoke for a completed MoodFlow update."""
from __future__ import annotations

import json
import ssl
import sys
import time
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

BASE = "https://47.83.162.191"


def request(path: str, payload: dict[str, str] | None = None, timeout: int = 30):
    body = None if payload is None else json.dumps(payload).encode("utf-8")
    headers = {} if body is None else {"Content-Type": "application/json"}
    return urlopen(Request(BASE + path, data=body, headers=headers), timeout=timeout,
                   context=ssl.create_default_context())


def run() -> dict[str, object]:
    result: dict[str, object] = {}
    for path in ("/", "/healthz", "/api/auth/status"):
        with request(path) as response:
            if response.status != 200:
                raise ValueError(f"http_status:{path}:{response.status}")
            if path == "/api/auth/status" and json.load(response).get("code") != 200:
                raise ValueError("auth_status_invalid")
            result[path] = "passed"
    with request("/api/ai/chat", {"message": "你好"}, timeout=90) as response:
        if response.status != 200 or not response.headers.get("Content-Type", "").startswith("text/event-stream"):
            raise ValueError("sse_content_type_invalid")
        seen: list[str] = []
        times: dict[str, float] = {}
        start = time.monotonic()
        for raw in response:
            if raw.startswith(b"event: "):
                event = raw[7:].decode("utf-8", "replace").strip()
                if event in {"update", "final", "done", "error"}:
                    seen.append(event)
                    times.setdefault(event, time.monotonic() - start)
                if event == "done":
                    break
        if "error" in seen or not all(event in seen for event in ("update", "final", "done")):
            raise ValueError("sse_missing_or_error_event")
        if not (seen.index("update") < seen.index("final") < seen.index("done")):
            raise ValueError("sse_event_order_invalid")
        if times["final"] - times["update"] < 0.05:
            raise ValueError("sse_stream_may_be_buffered")
        result["sse"] = {"status": "passed", "update_seconds": round(times["update"], 3),
                         "final_seconds": round(times["final"], 3)}
    for attempt in (1, 2):
        try:
            with request("/api/ai/worldline/sessions", {"query": "Pixies"}, timeout=180) as response:
                created = json.load(response)
            break
        except HTTPError as exc:
            if attempt == 2 or exc.code != 502:
                raise
    session_id = created.get("session_id")
    if not isinstance(session_id, str) or not session_id or "/" in session_id:
        raise ValueError("worldline_session_id_invalid")
    node_count = len(created.get("graph", {}).get("nodes", []))
    if node_count < 1:
        raise ValueError("worldline_graph_empty")
    with request("/api/ai/worldline/sessions/" + session_id, timeout=30) as response:
        recovered = json.load(response)
    if recovered.get("session_id") != session_id or recovered.get("revision") != created.get("revision"):
        raise ValueError("worldline_read_mismatch")
    result["worldline"] = {"status": "passed", "attempts": attempt,
                           "node_count": node_count}
    return result


def main() -> None:
    try:
        print(json.dumps(run(), ensure_ascii=False))
    except (HTTPError, URLError, OSError, ValueError, json.JSONDecodeError) as exc:
        # The response body and user/session contents must not enter deploy logs.
        print(f"public_smoke_failed:{type(exc).__name__}", file=sys.stderr)
        raise SystemExit(1) from None


if __name__ == "__main__":
    main()
