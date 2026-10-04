"""Loopback-only Phase 4 acceptance with a fresh QR session and real music tools.

No browser/old cookie import, model call, production config edit or retry of writes.
Start with --work-dir pointing to a new ignored directory; credentials stay in memory.
"""

import argparse
import json
import os
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

parser = argparse.ArgumentParser()
parser.add_argument("--work-dir", type=Path, required=True)
parser.add_argument("--port", type=int, default=18004)
args = parser.parse_args()
args.work_dir.mkdir(parents=True, exist_ok=True)
os.environ.update(SPRING_BOOT_BASE_URL="http://127.0.0.1:18003", CHECKPOINT_BACKEND="memory",
                  PERSISTENCE_BACKEND="sqlite", MEMORY_BACKEND="disabled", DATABASE_URL="",
                  AI_AGENT_GRAPH_MODE="foundation", HANDOFF_CANARY_ENABLED="false", JEV_ENABLED="false",
                  LLM_API_KEY="offline-validation-placeholder", LLM_BASE_URL="https://invalid.example/v1",
                  LLM_MODEL="offline-test", LLM_GENERATION_MODEL="offline-test", AI_RAG_RETRIEVAL_MODE="empty",
                  AI_RAG_RERANK_MODE="off", NO_PROXY="localhost,127.0.0.1,::1", PYTHONUTF8="1")

import httpx  # noqa: E402
import uvicorn  # noqa: E402
from fastapi import FastAPI, HTTPException  # noqa: E402
from fastapi.responses import HTMLResponse  # noqa: E402
from langchain_core.messages import ToolMessage  # noqa: E402
from pydantic import BaseModel, ConfigDict  # noqa: E402
from app.api.chat import router  # noqa: E402
from app.agent.foundation.long_horizon import coordinator as coordinator_module  # noqa: E402
from app.agent.foundation.long_horizon.accounting import DurableAccounting  # noqa: E402
from app.agent.foundation.long_horizon.coordinator import ContinuityCoordinator  # noqa: E402
from app.agent.foundation.long_horizon.history_writer import digest  # noqa: E402
from app.agent.foundation.long_horizon.models import ContinuityIdentity  # noqa: E402
from app.agent.foundation.long_horizon.operations import OperationJournal, active_operations  # noqa: E402
from app.agent.foundation.long_horizon.service import LongHorizonContextService  # noqa: E402
from app.agent.foundation.long_horizon.store import SQLiteContinuityStore  # noqa: E402
from app.schemas.context import MusicAgentContext  # noqa: E402
from app.tools.music import MUSIC_AGENT_TOOLS, MusicServiceClient  # noqa: E402

app = FastAPI()
app.router.routes.extend(route for route in router.routes if route.path == "/api/ai/continuity/operations/status")
client = httpx.AsyncClient(base_url="http://127.0.0.1:18003", timeout=25, trust_env=False)
key = None
EXPECTED_ID = 17994646607
EXPECTED_NAME = "我也无话可说_t"
store = SQLiteContinuityStore(args.work_dir / "history.db")
coordinator = ContinuityCoordinator(LongHorizonContextService(store))
# Inject only isolated storage. All production tool/client/journal/API behavior is used unchanged.
coordinator_module.get_continuity_coordinator = lambda: coordinator
tools = {tool.name: tool for tool in MUSIC_AGENT_TOOLS}
plan = {"thread_id": "tools-t1t2-" + uuid4().hex, "run_id": "phase4-" + uuid4().hex,
        "name": "MoodFlowTest-" + datetime.now().strftime("%Y%m%d-%H%M%S"), "attempted": False}
if (args.work_dir / "plan.json").exists():
    plan = json.loads((args.work_dir / "plan.json").read_text(encoding="utf-8"))
rows = []


def record(kind, **fields):
    row = {"time": datetime.now(UTC).isoformat(), "kind": kind, **fields}
    rows.append(row)
    with (args.work_dir / "events.jsonl").open("a", encoding="utf-8") as out:
        out.write(json.dumps(row, ensure_ascii=False) + "\n")
    (args.work_dir / "plan.json").write_text(json.dumps(plan, ensure_ascii=False), encoding="utf-8")


send = httpx.AsyncClient.send


async def observe_send(self, request, **kwargs):
    response = await send(self, request, **kwargs)
    if request.url.port == 18003 and request.url.path.startswith(("/api/songs", "/api/playlists", "/api/user/liked-songs")):
        await response.aread()
        record("music_http", method=request.method, path=request.url.path,
               parameters=dict(request.url.params), status=response.status_code, result=response.json())
    return response


httpx.AsyncClient.send = observe_send


def fresh_session_cookie():
    # The jar began empty and is populated only by this round's QR login.
    return "JSESSIONID=" + client.cookies["JSESSIONID"]


async def execute_tool(name, parameters, text):
    await identity()
    conversation = "handoff:v1:" + plan["thread_id"]
    session = coordinator.service.open_conversation(conversation)
    lease = coordinator.locks.acquire(conversation, digest(["handoff", str(EXPECTED_ID)]))
    identity_dto = ContinuityIdentity(conversation_id=conversation, continuity_task_id=session.task_id,
        session_id=session.session_id, request_id=uuid4().hex, graph_mode="handoff", run_id=plan["run_id"])
    journal = OperationJournal(coordinator.writer, DurableAccounting(coordinator.writer, identity_dto, lease),
        read_client=MusicServiceClient(fresh_session_cookie()))
    runtime = SimpleNamespace(state={"current_input": text}, context=MusicAgentContext(
        session_cookie=fresh_session_cookie(), user_id=str(EXPECTED_ID)))
    call_id = uuid4().hex
    request = SimpleNamespace(tool_call={"name": name, "args": parameters, "id": call_id})
    async def execute(request):
        result = await tools[name].coroutine(**parameters, runtime=runtime)
        return ToolMessage(content=result, name=name, tool_call_id=call_id)
    token = active_operations.set(journal)
    try:
        response = await journal.call(request, execute)
        public = json.loads(response.content)
        record("tool", name=name, parameters=parameters, result=public, status=response.status)
        matches = [event for event in journal.write_events() if event.payload["name"] == name
                   and event.payload["parameters"] == parameters]
        if matches:
            oid = matches[0].payload["operation_id"]
            record("write_state", **journal.state(oid))
            await query_state(oid)
        if response.status == "error":
            raise HTTPException(409, public)
        return public
    except Exception as exc:
        record("failure", name=name, error_type=type(exc).__name__, message=str(exc))
        matches = [event for event in journal.write_events() if event.payload["name"] == name
                   and event.payload["parameters"] == parameters]
        if matches:
            oid = matches[0].payload["operation_id"]
            record("write_state", **journal.state(oid))
            await query_state(oid)
        raise
    finally:
        active_operations.reset(token)
        lease.release()


async def query_state(operation_id):
    dto = {"thread_id": plan["thread_id"], "graph_mode": "handoff", "operation_id": operation_id}
    async with httpx.AsyncClient(trust_env=False) as api:
        response = await api.post(f"http://127.0.0.1:{args.port}/api/ai/continuity/operations/status", json=dto,
                                  headers={"Cookie": fresh_session_cookie()})
    record("state_api", request=dto, status=response.status_code, result=response.json())
    response.raise_for_status()
    return response.json()


@app.post("/live/basics")
async def basics():
    await identity()
    if plan["attempted"]:
        raise HTTPException(409, "本轮已开始，不重复任何业务写请求；请查询已保存的证据")
    plan["attempted"] = True
    music = MusicServiceClient(fresh_session_cookie())
    liked_before = await execute_tool("get_liked_songs", {}, "读取云端红心")
    search = await music.search_music("晴天", 10)
    selected = next((song for song in search.songs if song.song_id not in liked_before["song_ids"]), None)
    if selected is None:
        raise HTTPException(422, "没有可安全恢复原红心状态的候选歌曲")
    plan["song_id"] = selected.song_id
    record("selected_song", song=selected.model_dump(mode="json"), originally_liked=False)
    for liked in (True, False):
        await execute_tool("set_song_liked_tool", {"song_id": selected.song_id, "liked": liked},
                           "收藏这首歌" if liked else "取消这首歌红心")
        current = await execute_tool("get_liked_songs", {}, "只读核对红心")
        matched = (selected.song_id in current["song_ids"]) is liked
        record("like_readback", song_id=selected.song_id, expected=liked, consistent=matched)
        if not matched:
            raise HTTPException(409, "红心读回不一致；保留原始结果，不重试写入")
    created = await execute_tool("create_playlist_tool", {"name": plan["name"], "privacy": 10},
                                 "创建测试歌单 " + plan["name"])
    plan["playlist_id"] = created["playlist_id"]
    current = await execute_tool("get_user_playlists", {}, "只读核对本轮歌单")
    record("created_readback", playlist_id=plan["playlist_id"], present=any(
        row["playlist_id"] == plan["playlist_id"] for row in current["playlists"]))
    return {"name": plan["name"], "playlist_id": plan["playlist_id"],
            "next": f"请 West 回复：确认删除歌单 {plan['playlist_id']}"}


class DeleteConfirmation(BaseModel):
    model_config = ConfigDict(extra="forbid")
    confirmation_text: str


@app.post("/live/delete")
async def delete_created(body: DeleteConfirmation):
    await identity()
    if "playlist_id" not in plan:
        raise HTTPException(422, "本轮没有已创建的歌单，不删除历史歌单")
    result = await execute_tool("delete_playlist_tool", {"playlist_id": plan["playlist_id"], "confirmed": True},
                                body.confirmation_text)
    current = await execute_tool("get_user_playlists", {}, "删除后只读核对本轮歌单")
    absent = all(row["playlist_id"] != plan["playlist_id"] for row in current["playlists"])
    record("deleted_readback", playlist_id=plan["playlist_id"], absent=absent)
    return {"result": result, "absent": absent}


@app.get("/live/evidence")
async def evidence():
    await identity()
    return {"plan": plan, "events": rows}


@app.post("/live/negative-controls")
async def negative_controls():
    """Only an independently verified official foreign playlist is used for the 403 probe."""
    from app.tools.music import MusicServiceError
    from app.schemas.music import DeletedPlaylistResult
    await identity()
    if plan.get("negative_attempted"):
        raise HTTPException(409, "负向验收已运行，不重复请求")
    plan["negative_attempted"] = True
    music = MusicServiceClient(fresh_session_cookie())
    search = await music.search_entities("网易云音乐", entity_type="playlist", limit=20)
    candidates = [item for item in search if item.artist == "网易云音乐"]
    candidate = min(candidates, key=lambda item: item.metadata.get("track_count", 100000))
    detail = await music.get_playlist_detail(candidate.entity_id)
    if detail.creator != "网易云音乐" or not detail.creator_id or detail.creator_id == EXPECTED_ID:
        raise HTTPException(422, "无法证明该公开歌单由官方账号创建，不执行拒绝探针")
    before = digest(detail.model_dump(mode="json"))
    record("foreign_target", playlist_id=detail.playlist_id, name=detail.name,
           creator=detail.creator, creator_id=detail.creator_id, source_hash=before)
    response = await client.delete(f"/api/playlists/{detail.playlist_id}", params={"confirmed": "true"})
    record("foreign_java_rejection", playlist_id=detail.playlist_id, status=response.status_code,
           result=response.json())
    if response.status_code != 403:
        raise HTTPException(409, "非自建拒绝未取得预期 403，保留现场，不重试")
    after = await music.get_playlist_detail(detail.playlist_id)
    record("foreign_readback", unchanged=before == digest(after.model_dump(mode="json")))
    try:
        await execute_tool("delete_playlist_tool", {"playlist_id": detail.playlist_id, "confirmed": True},
                           f"确认删除歌单 {detail.playlist_id}")
    except MusicServiceError as exc:
        if exc.code != "http_422" or exc.write_state != "failed":
            raise
        record("foreign_agent_rejection", code=exc.code, state=exc.write_state)
    else:
        raise HTTPException(409, "非自建 Agent 工具未拒绝")
    response = await client.delete(f"/api/playlists/{plan['playlist_id']}", params={"confirmed": "false"})
    record("unconfirmed_java_rejection", status=response.status_code, result=response.json())
    if response.status_code != 422:
        raise HTTPException(409, "未确认删除没有被拒绝")
    runtime = SimpleNamespace(state={"current_input": "查看歌单"}, context=MusicAgentContext(
        session_cookie=fresh_session_cookie(), user_id=str(EXPECTED_ID)))
    try:
        await tools["delete_playlist_tool"].coroutine(plan["playlist_id"], runtime, True)
    except MusicServiceError as exc:
        if exc.code != "http_422":
            raise
        record("unconfirmed_agent_rejection", code=exc.code)
    else:
        raise HTTPException(409, "模型参数不能替用户确认")
    # Reuse the saved operation/run ID after a new process and fresh login. A
    # committed receipt returns the artifact without a second DELETE dispatch.
    replay = await execute_tool("delete_playlist_tool", {"playlist_id": plan["playlist_id"], "confirmed": True},
                                 f"确认删除歌单 {plan['playlist_id']}")
    DeletedPlaylistResult.model_validate(replay)
    record("delete_replay", replayed_from_receipt=True, playlist_id=plan["playlist_id"])
    current = await execute_tool("get_user_playlists", {}, "最终只读核对本轮歌单")
    absent = all(item["playlist_id"] != plan["playlist_id"] for item in current["playlists"])
    liked = await execute_tool("get_liked_songs", {}, "最终只读核对红心")
    record("final_cleanup", playlist_absent=absent, test_song_unliked=plan["song_id"] not in liked["song_ids"])
    return {"foreign_java": 403, "foreign_agent": "failed / http_422", "unconfirmed_java": 422,
            "unconfirmed_agent": "http_422", "delete_replay": "receipt only", "playlist_absent": absent}


async def identity():
    response = await client.get("/api/user/profile")
    if response.status_code != 200:
        raise HTTPException(401, "请先用本轮新二维码登录测试账号")
    profile = response.json()["data"]
    if profile["userId"] != EXPECTED_ID or profile["nickname"] != EXPECTED_NAME:
        raise HTTPException(403, "测试账号身份不符；所有业务写操作均禁止")
    return {"user_id": profile["userId"], "name": profile["nickname"]}


@app.get("/live/init")
async def login_init():
    global key
    response = await client.get("/api/auth/qr/key")
    response.raise_for_status()
    key = response.json()["data"]["key"]
    response = await client.get("/api/auth/qr/create", params={"key": key})
    response.raise_for_status()
    return {"image": response.json()["data"]["qrimg"]}


@app.get("/live/check")
async def login_check():
    if not key:
        raise HTTPException(422, "请先生成本轮二维码")
    response = await client.get("/api/auth/qr/check", params={"key": key})
    response.raise_for_status()
    data = response.json()["data"]
    if data.get("loggedIn"):
        return {"logged_in": True, **await identity()}
    return {"logged_in": False, "status": data.get("status"), "message": data.get("statusMessage")}


@app.get("/live/identity")
async def get_identity():
    return await identity()


@app.get("/", response_class=HTMLResponse)
async def ui():
    return """<!doctype html><meta charset="utf-8"><title>Phase 4 测试账号登录</title>
    <style>body{font:20px sans-serif;padding:32px;background:#eef5f8}img{width:280px}pre{white-space:pre-wrap}</style>
    <h1>Phase 4 · 红心读回 / 删除自建歌单</h1><p>仅授权测试账号：我也无话可说_t（17994646607）</p>
    <p>本轮全新扫码会话；不会导入旧 Cookie。扫码后自动核对身份。</p>
    <button onclick="init()">生成 / 更新二维码</button><p><img id="qr"></p><pre id="status">等待扫码</pre>
    <script>let timer; async function init(){clearInterval(timer); let r=await fetch('/live/init');
    let d=await r.json(); if(!r.ok){status.textContent=JSON.stringify(d);return;}qr.src=d.image;
    timer=setInterval(async()=>{let r=await fetch('/live/check');let d=await r.json();
    document.getElementById('status').textContent=JSON.stringify(d,null,2);
    if(d.logged_in||!r.ok){clearInterval(timer);if(d.logged_in)qr.remove();}},3000);}init();</script>"""


if __name__ == "__main__":
    uvicorn.run(app, host="127.0.0.1", port=args.port, access_log=False)
