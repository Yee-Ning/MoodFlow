import asyncio
from datetime import UTC, datetime
import logging
import random
from typing import Any
from unittest.mock import patch

import httpx
import pytest

from app.tools.music import (
    MusicServiceClient,
    MusicServiceError,
)
from tests.test_music_retry import FakeClock, make_client


class OldMusicServiceClient:
    """Simulates the pre-Gate 1 MusicServiceClient behavior without retry."""

    def __init__(self, session_cookie: str | None = None) -> None:
        self._session_cookie = session_cookie

    async def _request(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        tool_name: str | None = None,
    ) -> Any:
        url = f"http://127.0.0.1:8080{path}"
        headers = (
            {"Cookie": self._session_cookie}
            if self._session_cookie is not None
            else None
        )
        async with httpx.AsyncClient(timeout=10.0, trust_env=False) as client:
            try:
                response = await client.request(
                    method,
                    url,
                    params=params,
                    headers=headers,
                )
            except httpx.TimeoutException as exc:
                raise MusicServiceError(
                    "timeout",
                    "音乐服务请求超时，请稍后再试。",
                ) from exc
            except httpx.RequestError as exc:
                raise MusicServiceError(
                    "unavailable",
                    "暂时无法连接音乐服务，请稍后再试。",
                ) from exc

            if not response.is_success:
                raise MusicServiceError(
                    f"http_{response.status_code}",
                    f"HTTP error {response.status_code}",
                )
            return response.json()

    async def get_liked_song_ids(self) -> list[int]:
        res = await self._request("GET", "/api/user/liked-songs")
        return res.get("data", [])


@pytest.mark.asyncio
class TestBaselineReproduction:
    async def test_case_1_liked_songs_502(self, caplog):
        """Case 1 (2026-10-12): get_liked_songs encounters 502/unavailable."""
        caplog.set_level(logging.INFO)

        call_count_old = 0
        def old_handler(request: httpx.Request) -> httpx.Response:
            nonlocal call_count_old
            call_count_old += 1
            if call_count_old == 1:
                return httpx.Response(502, text="Bad Gateway")
            return httpx.Response(200, json={"code": 200, "message": "success", "data": [101, 102]})

        old_client = OldMusicServiceClient()
        with patch("httpx.AsyncClient", return_value=httpx.AsyncClient(transport=httpx.MockTransport(old_handler))):
            with pytest.raises(MusicServiceError) as exc_info_old:
                await old_client.get_liked_song_ids()

        assert exc_info_old.value.code == "http_502"
        assert call_count_old == 1
        print(f"\n[Case 1 Old Logic Output]")
        print(f"Status: FAILED (call_count={call_count_old})")
        print(f"Exception: MusicServiceError(code='{exc_info_old.value.code}', message='{exc_info_old.value.public_message}')")

        # Now test with new logic (with retry)
        call_count_new = 0
        clock = FakeClock()
        def new_handler(request: httpx.Request) -> httpx.Response:
            nonlocal call_count_new
            call_count_new += 1
            if call_count_new == 1:
                return httpx.Response(502, text="Bad Gateway")
            return httpx.Response(200, json={"code": 200, "message": "success", "data": [101, 102]})

        new_client = make_client(sleeper=clock.sleep, jitter=lambda: 0.1)
        mock_transport = httpx.MockTransport(new_handler)
        with patch("app.tools.music.httpx.AsyncClient", return_value=httpx.AsyncClient(transport=mock_transport)):
            result = await new_client.get_liked_song_ids()

        assert result == [101, 102]
        assert call_count_new == 2
        assert len(clock.slept_intervals) == 1
        # attempt=1: 1.0 * (1 + 0.25*0.1) = 1.025s -> ~1025ms
        assert round(clock.slept_intervals[0], 3) == 1.025
        assert sum(clock.slept_intervals) < 5.0
        assert 'tool_retry{tool="get_liked_songs", error="502", attempt=1, delay_ms=1025, outcome="retrying"}' in caplog.text
        assert 'tool_retry{tool="get_liked_songs", error="502", attempt=1, outcome="success"}' in caplog.text
        print(f"[Case 1 New Logic Output]")
        print(f"Status: RECOVERED (call_count={call_count_new}, retries=1)")
        print(f"Delays: {[int(s * 1000) for s in clock.slept_intervals]}ms, Total Wait: {int(sum(clock.slept_intervals) * 1000)}ms")
        print(f"Log: tool_retry{{tool=\"get_liked_songs\", error=\"502\", attempt=1, delay_ms=1025, outcome=\"retrying\"}}")
        print(f"Log: tool_retry{{tool=\"get_liked_songs\", error=\"502\", attempt=1, outcome=\"success\"}}")

    async def test_case_2_popularity_timeout(self, caplog):
        """Case 2 (2026-10-14): query encounters timeout twice, succeeds on 3rd."""
        caplog.set_level(logging.INFO)

        mock_payload = {
            "code": 200,
            "message": "success",
            "data": {"songId": 101, "commentCount": 12500},
        }

        call_count_old = 0
        def old_handler(request: httpx.Request) -> httpx.Response:
            nonlocal call_count_old
            call_count_old += 1
            if call_count_old <= 2:
                raise httpx.ReadTimeout("read timed out", request=request)
            return httpx.Response(200, json=mock_payload)

        old_client = OldMusicServiceClient()
        with patch("httpx.AsyncClient", return_value=httpx.AsyncClient(transport=httpx.MockTransport(old_handler))):
            with pytest.raises(MusicServiceError) as exc_info_old:
                await old_client._request("GET", "/api/songs/101/comment-summary", tool_name="get_song_comment_count")

        assert exc_info_old.value.code == "timeout"
        assert call_count_old == 1
        print(f"\n[Case 2 Old Logic Output]")
        print(f"Status: FAILED (call_count={call_count_old})")
        print(f"Exception: MusicServiceError(code='{exc_info_old.value.code}', message='{exc_info_old.value.public_message}')")

        # Now test with new logic (with retry)
        call_count_new = 0
        clock = FakeClock()
        def new_handler(request: httpx.Request) -> httpx.Response:
            nonlocal call_count_new
            call_count_new += 1
            if call_count_new <= 2:
                raise httpx.ReadTimeout("read timed out", request=request)
            return httpx.Response(200, json=mock_payload)

        new_client = make_client(sleeper=clock.sleep, jitter=lambda: 0.2)
        mock_transport = httpx.MockTransport(new_handler)
        with patch("app.tools.music.httpx.AsyncClient", return_value=httpx.AsyncClient(transport=mock_transport)):
            result = await new_client.get_song_comment_count(101)

        assert result == 12500
        assert call_count_new == 3
        assert len(clock.slept_intervals) == 2
        # attempt=1: 1.0 * (1 + 0.25*0.2) = 1.05s
        # attempt=2: 2.0 * (1 + 0.25*0.2) = 2.10s
        assert round(clock.slept_intervals[0], 2) == 1.05
        assert round(clock.slept_intervals[1], 2) == 2.10
        total_delay = sum(clock.slept_intervals)
        assert total_delay < 10.0
        assert round(total_delay, 2) == 3.15
        assert 'tool_retry{tool="get_song_comment_count", error="timeout", attempt=1, delay_ms=1050, outcome="retrying"}' in caplog.text
        assert 'tool_retry{tool="get_song_comment_count", error="timeout", attempt=2, delay_ms=2100, outcome="retrying"}' in caplog.text
        assert 'tool_retry{tool="get_song_comment_count", error="timeout", attempt=2, outcome="success"}' in caplog.text
        print(f"[Case 2 New Logic Output]")
        print(f"Status: RECOVERED (call_count={call_count_new}, retries=2)")
        print(f"Delays: {[int(s * 1000) for s in clock.slept_intervals]}ms, Total Wait: {int(total_delay * 1000)}ms")
        print(f"Log: tool_retry{{tool=\"get_song_comment_count\", error=\"timeout\", attempt=1, delay_ms=1050, outcome=\"retrying\"}}")
        print(f"Log: tool_retry{{tool=\"get_song_comment_count\", error=\"timeout\", attempt=2, delay_ms=2100, outcome=\"retrying\"}}")
        print(f"Log: tool_retry{{tool=\"get_song_comment_count\", error=\"timeout\", attempt=2, outcome=\"success\"}}")


@pytest.mark.asyncio
class TestRandomFaultInjection20:
    async def test_run_20_random_fault_injections(self, caplog):
        """Runs 20 randomized fault injection cases (502, 503, 504, timeout, 429 x 4 each)."""
        caplog.set_level(logging.INFO)
        rng = random.Random(20261019)

        # 5 types, 4 runs each = 20 runs
        error_types = ["502", "503", "504", "timeout", "429"] * 4
        rng.shuffle(error_types)

        tools = [
            ("search_music", "/api/music/search", {"keyword": "test"}),
            ("get_playlist_detail", "/api/playlists/1", None),
            ("get_liked_songs", "/api/user/liked-songs", None),
            ("get_song_comment_count", "/api/songs/101/comment-summary", None),
            ("get_song_lyrics", "/api/songs/101/lyrics", None),
        ]

        results = []

        for idx, err in enumerate(error_types, start=1):
            tool_name, path, params = tools[(idx - 1) % len(tools)]
            # Mock fail N times (N=1~3), succeed on N+1
            n_fails = rng.randint(1, 3)
            current_call = 0
            clock = FakeClock()

            # Randomize jitter for realistic variance [0.0, 1.0]
            jitter_val = rng.uniform(0.05, 0.95)

            def make_handler(target_err=err, fails=n_fails):
                def handler(request: httpx.Request) -> httpx.Response:
                    nonlocal current_call
                    current_call += 1
                    if current_call <= fails:
                        if target_err == "timeout":
                            raise httpx.ReadTimeout("Mocked read timeout", request=request)
                        elif target_err == "429":
                            # 50% chance to include Retry-After header
                            headers = {"Retry-After": "2"} if (current_call % 2 == 1) else {}
                            return httpx.Response(429, headers=headers, text="Too Many Requests")
                        else:
                            status_code = int(target_err)
                            return httpx.Response(status_code, text=f"Mocked HTTP {status_code}")
                    return httpx.Response(200, json={"code": 200, "data": "success"})
                return handler

            client = make_client(sleeper=clock.sleep, jitter=lambda: jitter_val)
            mock_transport = httpx.MockTransport(make_handler())

            with patch("app.tools.music.httpx.AsyncClient", return_value=httpx.AsyncClient(transport=mock_transport)):
                res = await client._request("GET", path, params=params, tool_name=tool_name)

            assert res == {"code": 200, "data": "success"}
            assert current_call == n_fails + 1
            assert len(clock.slept_intervals) == n_fails

            delays_ms = [int(s * 1000) for s in clock.slept_intervals]
            total_wait_ms = sum(delays_ms)

            results.append({
                "group": idx,
                "tool": tool_name,
                "error": err,
                "n_fails": n_fails,
                "retries": n_fails,
                "delays_ms": delays_ms,
                "total_wait_ms": total_wait_ms,
                "outcome": "recovered",
            })

        # Summary statistics
        recovered_count = sum(1 for r in results if r["outcome"] == "recovered")
        avg_retries = sum(r["retries"] for r in results) / len(results)
        avg_wait_ms = sum(r["total_wait_ms"] for r in results) / len(results)

        assert recovered_count == 20
        assert len(results) == 20

        # Print detailed markdown-ready table for reporting
        print("\n=== FAULT INJECTION 20 RUNS RESULT ===")
        print(f"Total: {len(results)}, Recovered: {recovered_count}/20 (100%), Avg Retries: {avg_retries:.2f}, Avg Wait: {avg_wait_ms:.1f}ms")
        for r in results:
            print(f"| {r['group']:02d} | `{r['tool']}` | {r['error']} | {r['n_fails']} | {r['delays_ms']} | {r['total_wait_ms']}ms | {r['outcome']} |")

    async def test_hard_failure_triggers_degradation(self, caplog):
        """Verify that when failure persists beyond 3 retries (N=4), it gracefully degrades."""
        caplog.set_level(logging.INFO)
        clock = FakeClock()
        call_count = 0

        def handler(request: httpx.Request) -> httpx.Response:
            nonlocal call_count
            call_count += 1
            return httpx.Response(503, text="Service Unavailable")

        client = make_client(sleeper=clock.sleep, jitter=lambda: 0.1)
        mock_transport = httpx.MockTransport(handler)

        with patch("app.tools.music.httpx.AsyncClient", return_value=httpx.AsyncClient(transport=mock_transport)):
            with pytest.raises(MusicServiceError) as exc_info:
                await client._request("GET", "/api/music/search", params={"keyword": "down"})

        assert exc_info.value.code == "http_503"
        assert exc_info.value.public_message == "音乐服务暂时不可用，请稍后重试。"
        # Initial call + 3 retries = 4 calls total
        assert call_count == 4
        assert len(clock.slept_intervals) == 3
        assert 'tool_retry{tool="search_music", error="503", attempt=3, outcome="failed"}' in caplog.text
        assert "Tool execution failed after 3 retries: tool=search_music error=503" in caplog.text
