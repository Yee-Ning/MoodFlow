import asyncio
import unittest
from types import SimpleNamespace
from unittest.mock import patch

import httpx

from app.tools.music import MusicSearchError, search_music


def fake_settings() -> SimpleNamespace:
    return SimpleNamespace(
        spring_boot_base_url="http://127.0.0.1:8080",
        spring_boot_timeout_seconds=10,
    )


def run_search(
    handler: httpx.MockTransport,
    keyword: str = "周杰伦",
    limit: int = 10,
):
    client = httpx.AsyncClient(transport=handler)
    with (
        patch("app.tools.music.httpx.AsyncClient", return_value=client),
        patch("app.tools.music.get_settings", fake_settings),
    ):
        return asyncio.run(search_music(keyword, limit))


class SearchMusicTests(unittest.TestCase):
    def test_returns_validated_music_data(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            self.assertEqual(request.url.path, "/api/music/search")
            self.assertEqual(request.url.params["keyword"], "周杰伦")
            self.assertEqual(request.url.params["limit"], "2")
            return httpx.Response(
                200,
                json={
                    "code": 200,
                    "message": "success",
                    "data": [
                        {
                            "id": 185809,
                            "name": "晴天",
                            "artists": ["周杰伦"],
                            "album": "叶惠美",
                            "coverUrl": "https://img.test/qingtian.jpg",
                            "duration": 269000,
                        }
                    ],
                },
            )

        result = run_search(httpx.MockTransport(handler), limit=2)

        self.assertEqual(result.keyword, "周杰伦")
        self.assertEqual(result.limit, 2)
        self.assertEqual(len(result.songs), 1)
        self.assertEqual(result.songs[0].name, "晴天")
        self.assertEqual(result.songs[0].artists, ["周杰伦"])

    def test_handles_timeout(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            raise httpx.ReadTimeout("timed out", request=request)

        with self.assertRaisesRegex(MusicSearchError, "timed out"):
            run_search(httpx.MockTransport(handler))

    def test_handles_non_2xx_response(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(503, json={"detail": "unavailable"})

        with self.assertRaisesRegex(MusicSearchError, "HTTP 503"):
            run_search(httpx.MockTransport(handler))

    def test_rejects_invalid_response_data(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(
                200,
                json={
                    "code": 200,
                    "message": "success",
                    "data": [{"id": 1, "name": "Broken"}],
                },
            )

        with self.assertRaisesRegex(MusicSearchError, "invalid data"):
            run_search(httpx.MockTransport(handler))


if __name__ == "__main__":
    unittest.main()
