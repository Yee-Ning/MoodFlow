import asyncio
from datetime import UTC, datetime, timedelta
import logging
from unittest.mock import AsyncMock, patch

import httpx
import pytest

from app.tools.music import (
    BASE_RETRY_DELAY,
    MAX_500_RETRIES,
    MAX_RETRY_AFTER_CAP,
    MAX_RETRY_DELAY,
    MAX_TOOL_RETRIES,
    MusicServiceClient,
    MusicServiceError,
    _calculate_retry_delay,
    _parse_retry_after,
    _resolve_tool_name,
)


class FakeClock:
    def __init__(self):
        self.slept_intervals: list[float] = []

    async def sleep(self, seconds: float) -> None:
        self.slept_intervals.append(seconds)


def make_client(*, sleeper=None, jitter=None, cookie="test-cookie"):
    return MusicServiceClient(
        session_cookie=cookie,
        sleeper=sleeper or (lambda _: asyncio.sleep(0)),
        jitter=jitter or (lambda: 0.0),
    )


class TestRetryDelayCalculation:
    def test_backoff_and_jitter_formula(self):
        # jitter = 0.0 -> exact base powers
        assert _calculate_retry_delay(1, 0.0) == 1.0
        assert _calculate_retry_delay(2, 0.0) == 2.0
        assert _calculate_retry_delay(3, 0.0) == 4.0
        assert _calculate_retry_delay(4, 0.0) == 8.0
        assert _calculate_retry_delay(5, 0.0) == 8.0  # capped at 8.0

        # jitter = 1.0 -> 1 + 0.25*1 = 1.25x
        assert _calculate_retry_delay(1, 1.0) == 1.25
        assert _calculate_retry_delay(2, 1.0) == 2.5
        assert _calculate_retry_delay(3, 1.0) == 5.0
        assert _calculate_retry_delay(4, 1.0) == 8.0  # capped at 8.0

    def test_parse_retry_after_integer(self):
        now = datetime(2026, 10, 19, 12, 0, 0, tzinfo=UTC)
        assert _parse_retry_after("5", now) == 5.0
        assert _parse_retry_after(" 12 ", now) == 12.0
        assert _parse_retry_after(None, now) is None
        assert _parse_retry_after("invalid", now) is None

    def test_parse_retry_after_http_date(self):
        now = datetime(2026, 10, 19, 12, 0, 0, tzinfo=UTC)
        date_str = (now + timedelta(seconds=7)).strftime("%a, %d %b %Y %H:%M:%S GMT")
        parsed = _parse_retry_after(date_str, now)
        assert parsed is not None
        assert round(parsed) == 7

    def test_resolve_tool_name(self):
        assert _resolve_tool_name("/api/music/search") == "search_music"
        assert _resolve_tool_name("/api/music/entities/search") == "search_entities"
        assert _resolve_tool_name("/api/music/entities/song/123") == "get_entity_detail"
        assert _resolve_tool_name("/api/songs/101/comment-summary") == "get_song_comment_count"
        assert _resolve_tool_name("/api/songs/101/lyrics") == "get_song_lyrics"
        assert _resolve_tool_name("/api/user/playlists") == "get_user_playlists"
        assert _resolve_tool_name("/api/playlists/1/songs/2", method="POST") == "add_song_to_playlist"
        assert _resolve_tool_name("/api/playlists/1/songs/2", method="DELETE") == "remove_song_from_playlist"
        assert _resolve_tool_name("/api/playlists/1", method="GET") == "get_playlist_detail"
        assert _resolve_tool_name("/api/playlists/1", method="DELETE") == "delete_playlist"
        assert _resolve_tool_name("/api/playlists", method="POST") == "create_playlist"
        assert _resolve_tool_name("/api/albums/10") == "get_album_detail"
        assert _resolve_tool_name("/api/songs/1/like", method="POST") == "set_song_liked"
        assert _resolve_tool_name("/api/user/liked-songs") == "get_liked_songs"
        assert _resolve_tool_name("/api/user/profile") == "get_account_id"
        # Explicit override
        assert _resolve_tool_name("/custom", tool_name="custom_tool") == "custom_tool"


@pytest.mark.asyncio
class TestMusicServiceClientRetry:
    async def test_timeout_retries_3_times_then_raises_friendly_error(self, caplog):
        caplog.set_level(logging.INFO)
        clock = FakeClock()
        call_count = 0

        def handler(request: httpx.Request) -> httpx.Response:
            nonlocal call_count
            call_count += 1
            raise httpx.ReadTimeout("read timed out", request=request)

        mock_transport = httpx.MockTransport(handler)
        mock_client = httpx.AsyncClient(transport=mock_transport)

        client = make_client(sleeper=clock.sleep, jitter=lambda: 0.2)
        with patch("app.tools.music.httpx.AsyncClient", return_value=mock_client):
            with pytest.raises(MusicServiceError) as exc_info:
                await client._request("GET", "/api/music/search", params={"keyword": "test"})

        # Initial call + 3 retries = 4 total calls
        assert call_count == 4
        # Sleep intervals recorded for 3 retries
        assert len(clock.slept_intervals) == 3
        # attempt=1: 1.0 * (1 + 0.25*0.2) = 1.05
        assert round(clock.slept_intervals[0], 2) == 1.05
        # attempt=2: 2.0 * (1 + 0.25*0.2) = 2.10
        assert round(clock.slept_intervals[1], 2) == 2.10
        # attempt=3: 4.0 * (1 + 0.25*0.2) = 4.20
        assert round(clock.slept_intervals[2], 2) == 4.20

        # Error code preserved for upper layer
        assert exc_info.value.code == "timeout"
        # User-friendly message instead of raw stacktrace
        assert exc_info.value.public_message == "音乐服务暂时不可用，请稍后重试。"
        assert "read timed out" not in str(exc_info.value)

        # Check structured log points
        logs = caplog.text
        assert 'tool_retry{tool="search_music", error="timeout", attempt=1, delay_ms=1050, outcome="retrying"}' in logs
        assert 'tool_retry{tool="search_music", error="timeout", attempt=2, delay_ms=2100, outcome="retrying"}' in logs
        assert 'tool_retry{tool="search_music", error="timeout", attempt=3, delay_ms=4200, outcome="retrying"}' in logs
        assert 'tool_retry{tool="search_music", error="timeout", attempt=3, outcome="failed"}' in logs
        assert "Tool execution failed after 3 retries: tool=search_music error=timeout" in logs

    async def test_retry_success_on_attempt_2(self, caplog):
        caplog.set_level(logging.INFO)
        clock = FakeClock()
        call_count = 0

        def handler(request: httpx.Request) -> httpx.Response:
            nonlocal call_count
            call_count += 1
            if call_count <= 2:
                return httpx.Response(502, text="Bad Gateway")
            return httpx.Response(200, json={"code": 200, "data": "ok"})

        mock_transport = httpx.MockTransport(handler)
        mock_client = httpx.AsyncClient(transport=mock_transport)

        client = make_client(sleeper=clock.sleep)
        with patch("app.tools.music.httpx.AsyncClient", return_value=mock_client):
            res = await client._request("GET", "/api/music/search", params={"keyword": "test"})

        assert res == {"code": 200, "data": "ok"}
        assert call_count == 3  # initial + retry1(fail) + retry2(success)
        assert len(clock.slept_intervals) == 2

        logs = caplog.text
        assert 'tool_retry{tool="search_music", error="502", attempt=1, delay_ms=1000, outcome="retrying"}' in logs
        assert 'tool_retry{tool="search_music", error="502", attempt=2, delay_ms=2000, outcome="retrying"}' in logs
        assert 'tool_retry{tool="search_music", error="502", attempt=2, outcome="success"}' in logs

    async def test_non_retryable_400_raises_immediately(self, caplog):
        caplog.set_level(logging.INFO)
        clock = FakeClock()
        call_count = 0

        def handler(request: httpx.Request) -> httpx.Response:
            nonlocal call_count
            call_count += 1
            return httpx.Response(400, json={"message": "invalid parameter"})

        mock_transport = httpx.MockTransport(handler)
        mock_client = httpx.AsyncClient(transport=mock_transport)

        client = make_client(sleeper=clock.sleep)
        with patch("app.tools.music.httpx.AsyncClient", return_value=mock_client):
            with pytest.raises(MusicServiceError) as exc_info:
                await client._request("GET", "/api/music/search", params={"keyword": "bad"})

        assert call_count == 1
        assert len(clock.slept_intervals) == 0
        assert exc_info.value.code == "http_400"
        assert exc_info.value.public_message == "invalid parameter"
        assert "tool_retry" not in caplog.text

    async def test_non_retryable_401_custom_friendly_message(self):
        call_count = 0

        def handler(request: httpx.Request) -> httpx.Response:
            nonlocal call_count
            call_count += 1
            return httpx.Response(401, text="")

        mock_transport = httpx.MockTransport(handler)
        mock_client = httpx.AsyncClient(transport=mock_transport)

        client = make_client()
        with patch("app.tools.music.httpx.AsyncClient", return_value=mock_client):
            with pytest.raises(MusicServiceError) as exc_info:
                await client._request("GET", "/api/user/playlists")

        assert call_count == 1
        assert exc_info.value.code == "http_401"
        assert exc_info.value.public_message == "请先登录网易云音乐。"

    async def test_http_500_allows_only_1_retry(self, caplog):
        caplog.set_level(logging.INFO)
        clock = FakeClock()
        call_count = 0

        def handler(request: httpx.Request) -> httpx.Response:
            nonlocal call_count
            call_count += 1
            return httpx.Response(500, json={"message": "Internal Server Error"})

        mock_transport = httpx.MockTransport(handler)
        mock_client = httpx.AsyncClient(transport=mock_transport)

        client = make_client(sleeper=clock.sleep)
        with patch("app.tools.music.httpx.AsyncClient", return_value=mock_client):
            with pytest.raises(MusicServiceError) as exc_info:
                await client._request("GET", "/api/music/search", params={"keyword": "error500"})

        # Initial + exactly 1 retry = 2 calls
        assert call_count == 2
        assert len(clock.slept_intervals) == 1
        assert exc_info.value.code == "http_500"
        assert exc_info.value.public_message == "音乐服务暂时不可用，请稍后重试。"

        logs = caplog.text
        assert 'tool_retry{tool="search_music", error="500", attempt=1, delay_ms=1000, outcome="retrying"}' in logs
        assert 'tool_retry{tool="search_music", error="500", attempt=1, outcome="failed"}' in logs

    async def test_http_429_respects_retry_after_header(self, caplog):
        caplog.set_level(logging.INFO)
        clock = FakeClock()
        call_count = 0

        def handler(request: httpx.Request) -> httpx.Response:
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return httpx.Response(429, headers={"Retry-After": "4"}, text="Rate limited")
            return httpx.Response(200, json={"code": 200, "data": "ok"})

        mock_transport = httpx.MockTransport(handler)
        mock_client = httpx.AsyncClient(transport=mock_transport)

        client = make_client(sleeper=clock.sleep)
        with patch("app.tools.music.httpx.AsyncClient", return_value=mock_client):
            res = await client._request("GET", "/api/music/search", params={"keyword": "rate"})

        assert res == {"code": 200, "data": "ok"}
        assert call_count == 2
        # Slept exactly 4.0s from Retry-After header
        assert len(clock.slept_intervals) == 1
        assert clock.slept_intervals[0] == 4.0
        assert 'tool_retry{tool="search_music", error="429", attempt=1, delay_ms=4000, outcome="retrying"}' in caplog.text

    async def test_http_429_retry_after_capped_at_10s(self):
        clock = FakeClock()
        call_count = 0

        def handler(request: httpx.Request) -> httpx.Response:
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return httpx.Response(429, headers={"Retry-After": "120"}, text="Rate limited")
            return httpx.Response(200, json={"code": 200, "data": "ok"})

        mock_transport = httpx.MockTransport(handler)
        mock_client = httpx.AsyncClient(transport=mock_transport)

        client = make_client(sleeper=clock.sleep)
        with patch("app.tools.music.httpx.AsyncClient", return_value=mock_client):
            await client._request("GET", "/api/music/search", params={"keyword": "capped"})

        # Capped at MAX_RETRY_AFTER_CAP (10.0s)
        assert clock.slept_intervals[0] == 10.0

    async def test_connection_error_marked_unavailable(self, caplog):
        caplog.set_level(logging.INFO)
        clock = FakeClock()
        call_count = 0

        def handler(request: httpx.Request) -> httpx.Response:
            nonlocal call_count
            call_count += 1
            raise httpx.ConnectError("Connection refused", request=request)

        mock_transport = httpx.MockTransport(handler)
        mock_client = httpx.AsyncClient(transport=mock_transport)

        client = make_client(sleeper=clock.sleep)
        with patch("app.tools.music.httpx.AsyncClient", return_value=mock_client):
            with pytest.raises(MusicServiceError) as exc_info:
                await client._request("GET", "/api/music/search")

        assert call_count == 4
        assert exc_info.value.code == "unavailable"
        logs = caplog.text
        assert 'tool_retry{tool="search_music", error="unavailable", attempt=1' in logs
        assert 'tool_retry{tool="search_music", error="unavailable", attempt=3, outcome="failed"}' in logs

    async def test_invalid_json_response_not_retried(self):
        call_count = 0

        def handler(request: httpx.Request) -> httpx.Response:
            nonlocal call_count
            call_count += 1
            return httpx.Response(200, text="Not a JSON text")

        mock_transport = httpx.MockTransport(handler)
        mock_client = httpx.AsyncClient(transport=mock_transport)

        client = make_client()
        with patch("app.tools.music.httpx.AsyncClient", return_value=mock_client):
            with pytest.raises(MusicServiceError) as exc_info:
                await client._request("GET", "/api/music/search")

        assert call_count == 1
        assert exc_info.value.code == "invalid_response"
