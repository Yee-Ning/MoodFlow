import json
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient

from app.main import app
from app.schemas.chat import (
    ChatOutput,
    FinalStreamEvent,
    MessageStreamEvent,
    UpdateStreamEvent,
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

    def test_health(self) -> None:
        response = self.client.get("/health")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {"status": "ok"})

    def test_chat_streams_messages_updates_final_and_done(self) -> None:
        captured: dict[str, object] = {}

        async def stream_graph(**kwargs):
            captured.update(kwargs)
            yield UpdateStreamEvent(node="load_song_context")
            yield MessageStreamEvent(delta="你好")
            yield MessageStreamEvent(delta="，我是 MoodFlow AI。")
            yield UpdateStreamEvent(node="song_assistant")
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
        self.assertEqual(parse_sse(response.text)[-1][0], "done")

    def test_chat_without_current_song_returns_friendly_final_event(self) -> None:
        response = self.client.post(
            "/api/ai/chat",
            json={
                "message": "分析一下这首歌",
                "thread_id": "thread-no-song-api-stream",
                "current_song_id": None,
            },
        )

        self.assertEqual(response.status_code, 200)
        events = parse_sse(response.text)
        final = next(data for event, data in events if event == "final")
        self.assertIn("先播放一首歌曲", final["message"])
        self.assertEqual(events[-1], ("done", {}))

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
