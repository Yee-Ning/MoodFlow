import asyncio
import unittest
from types import SimpleNamespace
from unittest.mock import patch

import httpx

from app.tools.music import MusicServiceClient, MusicServiceError, search_music


def fake_settings() -> SimpleNamespace:
    return SimpleNamespace(
        spring_boot_base_url="http://127.0.0.1:8080",
        spring_boot_timeout_seconds=10,
    )


def run_with_transport(handler: httpx.MockTransport, awaitable_factory):
    client = httpx.AsyncClient(transport=handler)
    with (
        patch("app.tools.music.httpx.AsyncClient", return_value=client),
        patch("app.tools.music.get_settings", fake_settings),
    ):
        return asyncio.run(awaitable_factory())


class MusicServiceClientTests(unittest.TestCase):
    def test_search_music_returns_normalized_ids_and_fields(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            self.assertEqual(request.method, "GET")
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

        result = run_with_transport(
            httpx.MockTransport(handler),
            lambda: search_music("周杰伦", 2),
        )

        self.assertEqual(result.keyword, "周杰伦")
        self.assertEqual(result.limit, 2)
        self.assertEqual(result.songs[0].song_id, 185809)
        self.assertEqual(result.songs[0].artists, ["周杰伦"])
        serialized = result.model_dump(mode="json")
        self.assertIn("song_id", serialized["songs"][0])
        self.assertNotIn("id", serialized["songs"][0])

    def test_get_song_lyrics_uses_backend_and_session(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            self.assertEqual(request.url.path, "/api/songs/185809/lyrics")
            self.assertEqual(request.headers["cookie"], "JSESSIONID=session-1")
            return httpx.Response(
                200,
                json={
                    "code": 200,
                    "message": "success",
                    "data": {
                        "lyric": "故事的小黄花",
                        "translation": "",
                        "status": "available",
                    },
                },
            )

        result = run_with_transport(
            httpx.MockTransport(handler),
            lambda: MusicServiceClient("JSESSIONID=session-1").get_song_lyrics(185809),
        )

        self.assertEqual(result.song_id, 185809)
        self.assertEqual(result.original_lyrics, "故事的小黄花")
        self.assertEqual(result.status, "available")

    def test_get_user_playlists_returns_normalized_playlist_ids(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            self.assertEqual(request.url.path, "/api/user/playlists")
            self.assertEqual(request.headers["cookie"], "JSESSIONID=session-2")
            return httpx.Response(
                200,
                json={
                    "code": 200,
                    "message": "success",
                    "data": [
                        {
                            "id": 77,
                            "name": "深夜学习",
                            "coverImgUrl": "https://img.test/playlist.jpg",
                            "trackCount": 12,
                            "creator": "测试用户",
                            "subscribed": False,
                        },
                        {
                            "id": 88,
                            "name": "收藏歌单",
                            "coverImgUrl": "",
                            "trackCount": 5,
                            "creator": "别人",
                            "subscribed": True,
                        },
                    ],
                },
            )

        result = run_with_transport(
            httpx.MockTransport(handler),
            lambda: MusicServiceClient("JSESSIONID=session-2").get_user_playlists(),
        )

        self.assertEqual(result.playlists[0].playlist_id, 77)
        self.assertEqual(result.playlists[0].name, "深夜学习")
        self.assertTrue(result.playlists[0].editable)
        self.assertFalse(result.playlists[1].editable)

    def test_add_song_to_playlist_posts_ids_and_uses_session(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            self.assertEqual(request.method, "POST")
            self.assertEqual(request.url.path, "/api/playlists/77/songs/33")
            self.assertEqual(request.headers["cookie"], "JSESSIONID=session-3")
            return httpx.Response(
                200,
                json={
                    "code": 200,
                    "message": "success",
                    "data": {"status": "added", "message": "已添加到歌单"},
                },
            )

        result = run_with_transport(
            httpx.MockTransport(handler),
            lambda: MusicServiceClient("JSESSIONID=session-3").add_song_to_playlist(
                77,
                33,
            ),
        )

        self.assertEqual(result.playlist_id, 77)
        self.assertEqual(result.song_id, 33)
        self.assertEqual(result.status, "added")

    def test_preserves_backend_business_error_as_safe_failure(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(
                401,
                json={"code": 401, "message": "请先登录网易云音乐"},
            )

        with self.assertRaises(MusicServiceError) as raised:
            run_with_transport(
                httpx.MockTransport(handler),
                lambda: MusicServiceClient().get_user_playlists(),
            )

        self.assertEqual(raised.exception.code, "http_401")
        self.assertIn("请先登录", raised.exception.public_message)

    def test_handles_timeout_without_leaking_transport_details(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            raise httpx.ReadTimeout("private transport detail", request=request)

        with self.assertRaises(MusicServiceError) as raised:
            run_with_transport(
                httpx.MockTransport(handler),
                lambda: search_music("周杰伦"),
            )

        self.assertEqual(raised.exception.code, "timeout")
        self.assertNotIn("private transport detail", str(raised.exception))

    def test_rejects_invalid_backend_data(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(
                200,
                json={
                    "code": 200,
                    "message": "success",
                    "data": [{"id": 1, "name": "Broken"}],
                },
            )

        with self.assertRaises(MusicServiceError) as raised:
            run_with_transport(
                httpx.MockTransport(handler),
                lambda: search_music("周杰伦"),
            )

        self.assertEqual(raised.exception.code, "invalid_response")


if __name__ == "__main__":
    unittest.main()
