import asyncio
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import httpx

from app.context.builder import build_context
from app.context.formatter import format_runtime_context
from app.context.music import MusicContextProvider, MusicContextProviderError
from app.schemas.chat import ChatContext
from app.schemas.context import ResolvedContext, SongContext


def fake_settings() -> SimpleNamespace:
    return SimpleNamespace(
        spring_boot_base_url="http://127.0.0.1:8080",
        spring_boot_timeout_seconds=10,
    )


def run_get_song(handler: httpx.MockTransport, song_id: int = 185809):
    client = httpx.AsyncClient(transport=handler)
    with (
        patch("app.context.music.httpx.AsyncClient", return_value=client),
        patch("app.context.music.get_settings", fake_settings),
    ):
        return asyncio.run(MusicContextProvider().get_song(song_id))


class MusicContextProviderTests(unittest.TestCase):
    def test_resolves_song_through_java_endpoint(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            self.assertEqual(request.url.path, "/api/songs/185809/context")
            return httpx.Response(
                200,
                json={
                    "code": 200,
                    "message": "success",
                    "data": {
                        "songId": 185809,
                        "name": "晴天",
                        "artist": "周杰伦",
                        "album": "叶惠美",
                        "originalLyrics": "[00:01.00]故事的小黄花",
                        "translatedLyrics": "",
                        "lyricsStatus": "AVAILABLE",
                    },
                },
            )

        song = run_get_song(httpx.MockTransport(handler))

        self.assertEqual(song.name, "晴天")
        self.assertEqual(song.artist, "周杰伦")
        self.assertEqual(song.original_lyrics, "[00:01.00]故事的小黄花")
        self.assertEqual(song.lyrics_status, "AVAILABLE")

    def test_reports_java_not_found(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(404, json={"code": 404, "data": None})

        with self.assertRaisesRegex(MusicContextProviderError, "HTTP 404"):
            run_get_song(httpx.MockTransport(handler))

    def test_reports_spring_timeout(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            raise httpx.ReadTimeout("timed out", request=request)

        with self.assertRaisesRegex(MusicContextProviderError, "timed out"):
            run_get_song(httpx.MockTransport(handler))

    def test_reports_unavailable_spring_service(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            raise httpx.ConnectError("connection refused", request=request)

        with self.assertRaisesRegex(MusicContextProviderError, "Cannot connect"):
            run_get_song(httpx.MockTransport(handler))

    def test_rejects_invalid_song_context_response(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(
                200,
                json={
                    "code": 200,
                    "message": "success",
                    "data": {"songId": 185809, "name": "晴天"},
                },
            )

        with self.assertRaisesRegex(MusicContextProviderError, "invalid data"):
            run_get_song(httpx.MockTransport(handler))


class ContextBuilderTests(unittest.TestCase):
    def test_degrades_when_song_resolution_fails(self) -> None:
        provider = SimpleNamespace(
            get_song=AsyncMock(side_effect=MusicContextProviderError("timeout"))
        )

        resolved = asyncio.run(
            build_context(
                ChatContext(
                    current_song_id=185809,
                    current_playlist_id=1001,
                    page="/library",
                ),
                provider=provider,
            )
        )

        self.assertIsNone(resolved.current_song)
        self.assertEqual(resolved.current_playlist_id, 1001)
        self.assertEqual(resolved.page, "/library")

    def test_preserves_resolved_song(self) -> None:
        song = SongContext(
            song_id=185809,
            name="晴天",
            artist="周杰伦",
            album="叶惠美",
            original_lyrics="[00:01.00]故事的小黄花",
            translated_lyrics="",
            lyrics_status="AVAILABLE",
        )
        provider = SimpleNamespace(get_song=AsyncMock(return_value=song))

        resolved = asyncio.run(
            build_context(ChatContext(current_song_id=185809), provider=provider)
        )

        self.assertEqual(resolved.current_song, song)


class ContextFormatterTests(unittest.TestCase):
    def test_formats_trusted_runtime_context_without_lyrics(self) -> None:
        formatted = format_runtime_context(
            ResolvedContext(
                current_song=SongContext(
                    song_id=185809,
                    name="晴天",
                    artist="周杰伦",
                    album="叶惠美",
                    original_lyrics="[00:01.00]故事的小黄花",
                    translated_lyrics="",
                    lyrics_status="AVAILABLE",
                ),
                page="/library",
            )
        )

        self.assertIn("【当前应用状态】", formatted)
        self.assertIn("歌曲：晴天", formatted)
        self.assertIn("歌手：周杰伦", formatted)
        self.assertIn("专辑：叶惠美", formatted)
        self.assertNotIn("歌词", formatted)


if __name__ == "__main__":
    unittest.main()
