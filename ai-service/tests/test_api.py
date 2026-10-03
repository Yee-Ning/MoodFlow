import json
import os
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from fastapi.testclient import TestClient

from app.main import app
from app.config.settings import Settings
from app.agent.foundation.main_graph import build_main_graph
from app.agent.foundation.routing import (
    IntentItem, IntentSource, IntentType, RouteDecision, RouteKey, RouteTask,
)
from app.schemas.chat import (
    ChatOutput,
    ClientActionStreamEvent,
    FinalStreamEvent,
    MessageStreamEvent,
    UpdateStreamEvent,
)
from app.agent.foundation.subgraphs.tool_calling.models import (
    ClientAction,
    ClientActionName,
    EmptyActionArguments,
)


def parse_sse(body: str) -> list[tuple[str, dict]]:
    events: list[tuple[str, dict]] = []
    for block in body.strip().split("\n\n"):
        lines = block.splitlines()
        event_name = next(line[7:] for line in lines if line.startswith("event: "))
        data = next(line[6:] for line in lines if line.startswith("data: "))
        events.append((event_name, json.loads(data)))
    return events


class ApiTests(unittest.TestCase):
    def setUp(self) -> None:
        self.client = TestClient(app)
        settings = patch(
            "app.api.chat.get_settings",
            return_value=SimpleNamespace(agent_graph_mode="legacy"),
        )
        settings.start()
        self.addCleanup(settings.stop)

    def test_health(self) -> None:
        response = self.client.get("/health")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {"status": "ok"})

    def test_foundation_is_default_graph_mode(self) -> None:
        with patch.dict(os.environ, {
            "LLM_API_KEY": "test-only",
            "LLM_BASE_URL": "https://example.test",
            "LLM_MODEL": "test-model",
            "LLM_TIMEOUT_SECONDS": "5",
            "SPRING_BOOT_BASE_URL": "http://127.0.0.1:8080",
            "SPRING_BOOT_TIMEOUT_SECONDS": "5",
        }, clear=True):
            self.assertEqual(Settings(_env_file=None).agent_graph_mode, "foundation")

    def test_foundation_json_and_sse_share_task_contract(self) -> None:
        class PauseRouter:
            async def route(self, text, context):
                intent = IntentItem(
                    intent=IntentType.TOOL_CALLING, sub_intent="pause_music",
                    confidence=1.0, source=IntentSource.RULE,
                    evidence=text, start=0, end=len(text), order=0,
                )
                return RouteDecision(
                    intents=[intent], is_complete=True, coverage=1.0,
                    tasks=[RouteTask(
                        task_id="pause-task", route_key=RouteKey.TOOL_CALLING,
                        input_text=text, intent_orders=[0],
                    )],
                )

        graph = build_main_graph(intent_router=PauseRouter()).compile()
        payload = {
            "message": "暂停一下", "thread_id": "foundation-api-e2e",
            "current_song_id": 101,
            "player_context": {
                "has_current_song": True, "current_song_id": 101,
                "queue_size": 1, "current_index": 0,
            },
        }
        with (
            patch("app.api.chat.get_settings", return_value=SimpleNamespace(agent_graph_mode="foundation")),
            patch("app.agent.foundation.streaming.get_foundation_graph", return_value=graph),
        ):
            json_response = self.client.post("/api/ai/foundation", json=payload)
            stream_response = self.client.post("/api/ai/chat", json=payload)

        self.assertEqual(json_response.status_code, 200)
        self.assertEqual(stream_response.status_code, 200)
        from_stream = next(
            data for event, data in parse_sse(stream_response.text) if event == "final"
        )
        self.assertEqual(json_response.json(), from_stream)
        self.assertEqual(from_stream["results"][0]["subgraph"], "tool_calling")
        self.assertEqual(from_stream["results"][0]["status"], "success")
        self.assertEqual(from_stream["status"], "success")

    def test_foundation_json_validates_request_and_hides_execution_error(self) -> None:
        self.assertEqual(
            self.client.post("/api/ai/foundation", json={"message": " "}).status_code,
            422,
        )

        async def fail(**kwargs):
            raise RuntimeError("private upstream error")

        with patch("app.api.chat.invoke_foundation_assistant", fail):
            response = self.client.post(
                "/api/ai/foundation", json={"message": "测试失败"},
            )
        self.assertEqual(response.status_code, 502)
        self.assertNotIn("private upstream error", response.text)

    def test_chat_streams_messages_updates_final_and_done(self) -> None:
        captured: dict[str, object] = {}

        async def stream_graph(**kwargs):
            captured.update(kwargs)
            yield UpdateStreamEvent(node="load_song_context")
            yield MessageStreamEvent(delta="你好")
            yield MessageStreamEvent(delta="，我是 MoodFlow AI。")
            yield UpdateStreamEvent(node="agent")
            yield FinalStreamEvent(
                response=ChatOutput(
                    type="chat",
                    message="你好，我是 MoodFlow AI。",
                    search_query=None,
                )
            )

        with patch("app.api.chat.stream_current_song_assistant", stream_graph):
            response = self.client.post(
                "/api/ai/chat",
                json={
                    "message": "你好，你是谁？",
                    "thread_id": "thread-api-1",
                    "current_song_id": None,
                },
            )

        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.headers["content-type"].startswith("text/event-stream"))
        events = parse_sse(response.text)
        self.assertEqual(
            [event_name for event_name, _ in events],
            ["update", "message", "message", "update", "final", "done"],
        )
        self.assertEqual(events[1][1], {"delta": "你好"})
        self.assertEqual(events[4][1]["type"], "chat")
        self.assertEqual(
            captured,
            {
                "message": "你好，你是谁？",
                "thread_id": "thread-api-1",
                "current_song_id": None,
                "session_cookie": None,
            },
        )

    def test_chat_resolves_legacy_frontend_context_for_stream(self) -> None:
        captured: dict[str, object] = {}

        async def stream_graph(**kwargs):
            captured.update(kwargs)
            yield FinalStreamEvent(
                response=ChatOutput(
                    type="chat",
                    message="我知道你指的是《晴天》。",
                    search_query=None,
                )
            )

        with patch("app.api.chat.stream_current_song_assistant", stream_graph):
            response = self.client.post(
                "/api/ai/chat",
                json={
                    "message": "这首歌是谁唱的？",
                    "thread_id": "thread-legacy-context",
                    "context": {
                        "current_song_id": 185809,
                        "page": "/library",
                    },
                },
            )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(captured["current_song_id"], 185809)
        self.assertIsNone(captured["session_cookie"])
        self.assertEqual(parse_sse(response.text)[-1][0], "done")

    def test_chat_forwards_only_spring_session_to_graph_runtime(self) -> None:
        captured: dict[str, object] = {}

        async def stream_graph(**kwargs):
            captured.update(kwargs)
            yield FinalStreamEvent(
                response=ChatOutput(
                    type="chat",
                    message="可以继续处理音乐请求。",
                    search_query=None,
                )
            )

        with patch("app.api.chat.stream_current_song_assistant", stream_graph):
            self.client.cookies.set("JSESSIONID", "spring-session")
            self.client.cookies.set("tracking", "do-not-forward")
            try:
                response = self.client.post(
                    "/api/ai/chat",
                    json={
                        "message": "列出我的歌单",
                        "thread_id": "thread-auth",
                        "current_song_id": None,
                    },
                )
            finally:
                self.client.cookies.clear()

        self.assertEqual(response.status_code, 200)
        events = parse_sse(response.text)
        final = next(data for event, data in events if event == "final")
        self.assertIn("继续处理", final["message"])
        self.assertEqual(events[-1], ("done", {}))
        self.assertEqual(captured["session_cookie"], "JSESSIONID=spring-session")

    def test_foundation_mode_streams_client_action_and_player_context(self) -> None:
        captured: dict[str, object] = {}

        async def stream_graph(**kwargs):
            captured.update(kwargs)
            yield ClientActionStreamEvent(
                action=ClientAction(
                    action_id="task-1:direct:0",
                    task_id="task-1",
                    name=ClientActionName.PAUSE_PLAYER,
                    arguments=EmptyActionArguments(),
                )
            )
            yield FinalStreamEvent(
                response=ChatOutput(
                    type="chat",
                    message="已发送暂停指令。",
                    search_query=None,
                )
            )

        with (
            patch(
                "app.api.chat.get_settings",
                return_value=SimpleNamespace(agent_graph_mode="foundation"),
            ),
            patch("app.api.chat.stream_foundation_assistant", stream_graph),
        ):
            response = self.client.post(
                "/api/ai/chat",
                headers={"X-MoodFlow-Telemetry-Class": "acceptance"},
                json={
                    "message": "暂停一下",
                    "thread_id": "foundation-api",
                    "current_song_id": 185809,
                    "player_context": {
                        "has_current_song": True,
                        "current_song_id": 185809,
                        "player_state": "playing",
                        "volume": 0.72,
                        "is_muted": False,
                        "play_mode": "list-loop",
                        "current_time": 30,
                        "duration": 180,
                        "queue_size": 1,
                        "current_index": 0,
                        "current_song_liked": False,
                    },
                },
            )

        self.assertEqual(response.status_code, 200)
        events = parse_sse(response.text)
        self.assertEqual([name for name, _ in events], ["action", "final", "done"])
        self.assertEqual(events[0][1]["name"], "pause_player")
        self.assertEqual(events[0][1]["graph_mode"], "foundation")
        self.assertEqual(events[0][1]["thread_id"], "foundation-api")
        self.assertEqual(captured["thread_id"], "foundation-api")
        self.assertEqual(captured["telemetry_sample_kind"], "acceptance")
        context = captured["routing_context"]
        self.assertEqual(context.current_song_id, 185809)
        self.assertEqual(context.current_time, 30)
        self.assertNotIn("current_song_id", {
            key: value for key, value in captured.items() if key != "routing_context"
        })

    def test_streaming_failure_emits_error_and_done(self) -> None:
        async def failing_stream(**kwargs):
            if False:
                yield None
            raise RuntimeError("secret upstream failure")

        with patch("app.api.chat.stream_current_song_assistant", failing_stream):
            response = self.client.post(
                "/api/ai/chat",
                json={
                    "message": "分析一下",
                    "thread_id": "thread-error",
                    "current_song_id": 185809,
                },
            )

        self.assertEqual(response.status_code, 200)
        events = parse_sse(response.text)
        self.assertEqual([event for event, _ in events], ["error", "done"])
        self.assertNotIn("secret upstream failure", response.text)


if __name__ == "__main__":
    unittest.main()
