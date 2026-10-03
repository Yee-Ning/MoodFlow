"""Local, opt-in live acceptance instrumentation. No mock providers or responses.

Run from ai-service: .venv/Scripts/python.exe -m scripts.validation7_runtime
The dedicated loopback database/container must already have its migrations.
Authentication travels through the existing browser/Spring session boundary.
Headers, credentials, prompts and private checkpoints are never exported.
"""

import json
import os
import re
import threading
from contextvars import ContextVar
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace

os.environ.update({
    "CHECKPOINT_BACKEND": "postgres", "PERSISTENCE_BACKEND": "postgres",
    "DATABASE_URL": "postgresql://postgres@127.0.0.1:15457/postgres",
    "MEMORY_BACKEND": "disabled", "HANDOFF_CANARY_ENABLED": "false",
    "SPRING_BOOT_BASE_URL": "http://127.0.0.1:8080",
    "NO_PROXY": "localhost,127.0.0.1,::1",
    "no_proxy": "localhost,127.0.0.1,::1",
    "PYTHONUTF8": "1", "OPENBLAS_NUM_THREADS": "1", "OMP_NUM_THREADS": "1",
})

import httpx  # noqa: E402
from fastapi import HTTPException, Request  # noqa: E402
from langchain_core.messages import ToolMessage  # noqa: E402
from pydantic import BaseModel, ConfigDict  # noqa: E402

from scripts.validation7_guards import (  # noqa: E402
    SpendingGate, current_run_playlists, require_owned_playlist, require_test_name, require_test_user,
)

ROOT = Path(__file__).resolve().parents[2]
EVIDENCE = ROOT / ".dev" / "validation-7"
EVIDENCE.mkdir(parents=True, exist_ok=True)
gate = SpendingGate(EVIDENCE / "spending-reservations.json")
log_lock = threading.Lock()
request_scope = ContextVar("validation7_request", default=None)
armed = None


def record(kind, **fields):
    row = {"time": datetime.now(UTC).isoformat(), "pid": os.getpid(),
           "kind": kind, "scope": request_scope.get(), **fields}
    with log_lock, (EVIDENCE / "live-events.jsonl").open("a", encoding="utf-8") as output:
        output.write(json.dumps(row, ensure_ascii=False, default=str) + "\n")
        output.flush()
        os.fsync(output.fileno())


def safe_error(text):
    return re.sub(r"(?i)(bearer\s+\S+|sk-[\w-]+)", "[redacted]", str(text))


def provider_for(host):
    if host == "api.deepseek.com":
        return "deepseek"
    if host and host.endswith("typesafe.ai"):
        return "typesafe"
    return None


def inspect_body(provider, body, dispatch):
    # Only usage and public response identifiers survive this boundary.
    for line in body.splitlines():
        raw = line[6:] if line.startswith(b"data: ") else line
        try:
            item = json.loads(raw)
        except (ValueError, UnicodeDecodeError):
            continue
        if isinstance(item, dict) and item.get("usage") is not None:
            record("provider_usage", provider=provider, dispatch=dispatch,
                   response_id=item.get("id"), model=item.get("model"), usage=item["usage"])


class ObservedStream(httpx.AsyncByteStream):
    def __init__(self, source, provider, dispatch):
        self.source, self.provider, self.dispatch = source, provider, dispatch

    async def __aiter__(self):
        pending = b""
        async for chunk in self.source:
            pending += chunk
            while b"\n" in pending:
                line, pending = pending.split(b"\n", 1)
                inspect_body(self.provider, line, self.dispatch)
            yield chunk
        if pending:
            inspect_body(self.provider, pending, self.dispatch)

    async def aclose(self):
        await self.source.aclose()


original_send = httpx.AsyncClient.send


async def observed_send(client, request, **kwargs):
    provider = provider_for(request.url.host)
    reservation = None
    if provider:
        payload = json.loads(request.content)
        reservation = gate.reserve(provider, payload, len(request.content))
        record("provider_dispatch", provider=provider, method=request.method,
               path=request.url.path, model=payload.get("model"), **reservation)
    try:
        response = await original_send(client, request, **kwargs)
    except Exception as error:
        record("transport_error", host=request.url.host, path=request.url.path,
               error_type=type(error).__name__, error=safe_error(error))
        raise
    if provider:
        record("provider_http", provider=provider, dispatch=reservation["dispatch"], status=response.status_code)
        if response.is_stream_consumed:
            inspect_body(provider, response.content, reservation["dispatch"])
        else:
            response.stream = ObservedStream(response.stream, provider, reservation["dispatch"])
    elif request.url.port == 8080 and request.url.path.startswith("/api/"):
        body = None
        if response.is_stream_consumed:
            try:
                body = response.json()
            except ValueError:
                body = {"non_json_response": safe_error(response.text[:600])}
        # Profile/avatar and historical lists stay out of the audit export.
        if request.url.path == "/api/user/profile":
            data = body.get("data", {}) if isinstance(body, dict) else {}
            body = {"code": body.get("code"), "data": {"userId": data.get("userId"), "nickname": data.get("nickname")},
                    "non_json_response": body.get("non_json_response")}
        elif request.url.path in {"/api/user/playlists", "/api/user/liked-songs"}:
            body = {"code": body.get("code"), "count": len(body.get("data") or [])}
        record("java_http", method=request.method, path=request.url.path,
               parameters=dict(request.url.params), status=response.status_code, response=body)
    return response


httpx.AsyncClient.send = observed_send

original_sync_send = httpx.Client.send


def observed_sync_send(client, request, **kwargs):
    response = original_sync_send(client, request, **kwargs)
    if request.url.port == 6333:
        record("qdrant_http", method=request.method, path=request.url.path, status=response.status_code)
    return response


httpx.Client.send = observed_sync_send

from app.main import app  # noqa: E402
from app.api.chat import _spring_session_cookie  # noqa: E402
from app.context.identity import UserIdentityProvider  # noqa: E402
from app.agent.foundation.long_horizon.accounting import DurableAccounting  # noqa: E402
from app.agent.foundation.long_horizon.execution import ExecutionLocks, ContinuityConflict  # noqa: E402
from app.agent.foundation.long_horizon.history_writer import HistoryWriter  # noqa: E402
from app.agent.foundation.long_horizon.models import ContinuityIdentity, HistoryEventType  # noqa: E402
from app.agent.foundation.long_horizon.operations import OperationJournal  # noqa: E402
from app.agent.foundation.long_horizon.runtime import get_long_horizon_service  # noqa: E402
from app.tools.music import MusicServiceClient, MusicServiceError  # noqa: E402
from functools import lru_cache  # noqa: E402
from app.agent.foundation import streaming as foundation_streaming  # noqa: E402


@lru_cache
def acceptance_handoff_graph():
    from app.agent.checkpointing import create_runtime_checkpointer
    from app.agent.foundation.long_horizon.coordinator import ContinuityCheckpointer, get_continuity_coordinator
    from app.agent.handoff.graph import create_handoff_graph
    from app.agent.handoff.state import RunLimits
    coordinator = get_continuity_coordinator()
    return create_handoff_graph(checkpointer=ContinuityCheckpointer(create_runtime_checkpointer(), coordinator),
        transfer_journal=coordinator.journal, limits=RunLimits(max_retries_per_agent=0))


foundation_streaming.get_handoff_graph = acceptance_handoff_graph

# Transparent error observation: re-raise the original failure without retrying.
import app.agent.handoff.graph as handoff_graph_module  # noqa: E402
from app.agent.decision.jev.client import JevClient  # noqa: E402
original_owned_task = handoff_graph_module.execute_owned_task
original_jev_decide = JevClient.decide


async def observed_owned_task(*args, **kwargs):
    try:
        return await original_owned_task(*args, **kwargs)
    except Exception as error:
        record("owned_task_error", error_type=type(error).__name__, error=safe_error(error))
        raise


async def observed_jev_decide(client, *args, **kwargs):
    try:
        # This SDK does not pass through AsyncClient.send. Gate at its real boundary.
        encoded = json.dumps([args, kwargs], ensure_ascii=False,
                             default=lambda value: value.model_dump(mode="json")).encode()
        reservation = gate.reserve("typesafe", {}, len(encoded) + 16_384)
        record("provider_dispatch", provider="typesafe", model=client.settings.jev_model,
               boundary="JevClient.decide", **reservation)
        result = await original_jev_decide(client, *args, **kwargs)
        record("jev_result", model=result.response_model, usage=result.usage,
               dispatch=reservation["dispatch"])
        return result
    except Exception as error:
        record("jev_error", error_type=type(error).__name__, error=safe_error(error))
        raise


handoff_graph_module.execute_owned_task = observed_owned_task
JevClient.decide = observed_jev_decide


def crash(point, **fields):
    global armed
    if armed == point:
        record("process_exit", point=point, exit_code=73, **fields)
        os._exit(73)


original_append = HistoryWriter.append_public_event


def observed_append(writer, identity, key, kind, body, **kwargs):
    values = body.model_dump(mode="json") if hasattr(body, "model_dump") else body
    if kind == HistoryEventType.AGENT_TRANSFER and values.get("phase") == "committed":
        crash("before_journal_committed", event_key=key, payload_digest=values["summary"].get("payload_digest"))
    event = original_append(writer, identity, key, kind, body, **kwargs)
    record("history", event_id=event.event_id, event_seq=event.event_seq,
           task_id=identity.continuity_task_id, session_id=identity.session_id,
           run_id=identity.run_id, event_type=str(kind), payload=event.payload)
    return event


HistoryWriter.append_public_event = observed_append


@app.middleware("http")
async def account_boundary(request: Request, call_next):
    if request.url.path.startswith("/api/ai/"):
        user = await UserIdentityProvider().resolve(_spring_session_cookie(request))
        try:
            require_test_user(user)
        except ValueError:
            from fastapi.responses import JSONResponse
            return JSONResponse(status_code=403, content={"detail": "test account identity mismatch"})
        token = request_scope.set(request.url.path)
        try:
            if request.url.path == "/api/ai/actions/ack":
                crash("before_action_ack")
            return await call_next(request)
        finally:
            request_scope.reset(token)
    return await call_next(request)


class Operation(BaseModel):
    model_config = ConfigDict(extra="forbid")
    kind: str
    run_id: str
    call_id: str = "first"
    name: str | None = None
    playlist_id: int | None = None
    song_id: int | None = None
    liked: bool = True


def owned_playlists():
    path = EVIDENCE / "owned-playlists.json"
    return {int(key): value for key, value in json.loads(path.read_text()).items()} if path.exists() else {}


@app.post("/api/ai/validation7/operation")
async def operation(body: Operation, request: Request):
    if not re.fullmatch(r"validation7-[a-z0-9-]+", body.run_id):
        raise HTTPException(422, "invalid acceptance run")
    parameters = {}
    if body.kind == "create":
        require_test_name(body.name)
        name, parameters = "create_playlist_tool", {"name": body.name, "privacy": 10}
    elif body.kind == "add":
        require_owned_playlist(body.playlist_id, owned_playlists())
        name, parameters = "add_song_to_playlist", {"playlist_id": body.playlist_id, "song_id": body.song_id}
    elif body.kind == "like":
        name, parameters = "set_song_liked_tool", {"song_id": body.song_id, "liked": body.liked}
    else:
        raise HTTPException(422, "unsupported acceptance write")
    client = MusicServiceClient(_spring_session_cookie(request))
    # Song IDs must resolve through the real Java/catalogue boundary before writing.
    if body.kind in {"add", "like"}:
        await client._request("GET", f"/api/songs/{body.song_id}")
    service = get_long_horizon_service()
    session = service.open_conversation(body.run_id)
    identity = ContinuityIdentity(conversation_id=body.run_id, continuity_task_id=session.task_id,
        session_id=session.session_id, request_id=body.call_id, run_id=body.run_id, graph_mode="handoff")
    lease = ExecutionLocks(service.store).acquire(body.run_id, "validation7:17994646607")
    writer = HistoryWriter(service.store)
    journal = OperationJournal(writer, DurableAccounting(writer, identity, lease))

    async def execute(_):
        if body.kind == "create":
            result = await client.create_playlist(**parameters)
            owned = owned_playlists()
            owned[result.playlist_id] = result.name
            (EVIDENCE / "owned-playlists.json").write_text(json.dumps(owned), encoding="utf-8")
        elif body.kind == "add":
            result = await client.add_song_to_playlist(**parameters)
        else:
            result = await client.set_song_liked(**parameters)
        record("external_write_observed", run_id=body.run_id, tool=name, parameters=parameters,
               result=result.model_dump(mode="json"))
        crash("after_write_before_result", run_id=body.run_id, tool=name)
        return ToolMessage(content=result.model_dump_json(), name=name, tool_call_id=body.call_id)

    try:
        result = await journal.call(SimpleNamespace(tool_call={"name": name, "args": parameters, "id": body.call_id}), execute)
        return {"run_id": body.run_id, "result": json.loads(result.content), "unknown_writes": sorted(journal.unknown_writes(body.run_id))}
    except (ContinuityConflict, MusicServiceError) as error:
        record("operation_failure", run_id=body.run_id, error_type=type(error).__name__, error=safe_error(error))
        raise HTTPException(409, str(error)) from error
    finally:
        lease.release()


@app.post("/api/ai/validation7/arm/{point}")
async def arm(point: str):
    global armed
    if point not in {"after_write_before_result", "before_journal_committed", "before_action_ack"}:
        raise HTTPException(422, "unsupported fault point")
    armed = point
    record("fault_armed", point=point)
    return {"armed": point}


@app.get("/api/ai/validation7/read/{kind}")
async def read(kind: str, request: Request, playlist_id: int | None = None, keyword: str = "晴天"):
    client = MusicServiceClient(_spring_session_cookie(request))
    if kind == "identity":
        result = await client._request("GET", "/api/user/profile")
    elif kind == "playlists":
        raw = await client._request("GET", "/api/user/playlists")
        owned = owned_playlists()
        result = {"owned": owned, "present": current_run_playlists(raw["data"], owned)}
    elif kind == "detail":
        require_owned_playlist(playlist_id, owned_playlists())
        result = (await client.get_playlist_detail(playlist_id)).model_dump(mode="json")
    elif kind == "liked":
        result = await client._request("GET", "/api/user/liked-songs")
    elif kind == "search":
        result = (await client.search_music(keyword, limit=3)).model_dump(mode="json")
    else:
        raise HTTPException(422, "unsupported read")
    return result


@app.post("/api/ai/validation7/jev")
async def live_jev_probe():
    from typesafe_sdk import Choice
    result = await JevClient().decide("Play the selected music track.", {
        "category": Choice(instructions="Which category matches the request?",
                           criteria={"music": None, "other": None})})
    return {"model": result.response_model, "usage": result.usage,
            "answers": {key: value.__dict__ for key, value in result.answers.items()}}


if __name__ == "__main__":
    import uvicorn
    record("process_start", canary_enabled=False, default_graph="foundation", database="isolated-loopback-15457")
    uvicorn.run(app, host="127.0.0.1", port=8001, access_log=False, log_level="warning")
