import unittest
from unittest.mock import AsyncMock, patch

from fastapi.testclient import TestClient

from app.main import app
from app.schemas.chat import ChatOutput


class ApiTests(unittest.TestCase):
    def setUp(self) -> None:
        self.client = TestClient(app)

    def test_health(self) -> None:
        response = self.client.get("/health")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {"status": "ok"})

    def test_chat_delegates_to_agent_and_returns_structured_output(self) -> None:
        run_graph = AsyncMock(
            return_value=ChatOutput(
                type="chat",
                message="你好，我是 MoodFlow AI。",
                search_query=None,
            )
        )

        with patch("app.api.chat.run_current_song_assistant", run_graph):
            response = self.client.post(
                "/api/ai/chat",
                json={
                    "message": "你好，你是谁？",
                    "thread_id": "thread-api-1",
                    "current_song_id": None,
                },
            )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["type"], "chat")
        run_graph.assert_awaited_once_with(
            message="你好，你是谁？",
            thread_id="thread-api-1",
            current_song_id=None,
        )

    def test_chat_resolves_frontend_context_before_running_agent(self) -> None:
        run_graph = AsyncMock(
            return_value=ChatOutput(
                type="chat",
                message="我知道你指的是《晴天》。",
                search_query=None,
            )
        )

        with patch("app.api.chat.run_current_song_assistant", run_graph):
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
        run_graph.assert_awaited_once_with(
            message="这首歌是谁唱的？",
            thread_id="thread-legacy-context",
            current_song_id=185809,
        )

    def test_chat_without_current_song_returns_friendly_message(self) -> None:
        response = self.client.post(
            "/api/ai/chat",
            json={
                "message": "分析一下这首歌",
                "thread_id": "thread-no-song-api",
                "current_song_id": None,
            },
        )

        self.assertEqual(response.status_code, 200)
        self.assertIn("先播放一首歌曲", response.json()["message"])


if __name__ == "__main__":
    unittest.main()
