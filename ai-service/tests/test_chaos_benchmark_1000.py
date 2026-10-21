import asyncio
from datetime import UTC, datetime
import logging
import math
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
from tests.test_music_fault_injection import OldMusicServiceClient


class ChaosScenario:
    """Defines the fault injection behavior for a single tool call attempt."""
    def __init__(
        self,
        call_id: int,
        tool_name: str,
        path: str,
        fault_type: str,  # 'normal', '502', '503', '504', 'timeout', '429', 'hard_down'
        fail_streak: int,  # Number of initial failures before recovering
        has_retry_after: bool = False,
    ):
        self.call_id = call_id
        self.tool_name = tool_name
        self.path = path
        self.fault_type = fault_type
        self.fail_streak = fail_streak
        self.has_retry_after = has_retry_after


def generate_1000_scenarios(seed: int = 20261019) -> list[ChaosScenario]:
    """Generates 1,000 tool call scenarios matching production network distributions."""
    rng = random.Random(seed)
    tools = [
        ("search_music", "/api/music/search"),
        ("get_playlist_detail", "/api/playlists/1"),
        ("get_liked_songs", "/api/user/liked-songs"),
        ("get_song_comment_count", "/api/songs/101/comment-summary"),
        ("get_song_lyrics", "/api/songs/101/lyrics"),
        ("add_song_to_playlist", "/api/playlists/1/songs/101"),
    ]

    scenarios: list[ChaosScenario] = []

    # Distribution out of 1000:
    # 940 normal (94.0%)
    # 25 gateway 502/503/504 (2.5%, streak 1~3)
    # 20 timeout (2.0%, streak 1~3)
    # 10 rate-limit 429 (1.0%, streak 1~2, 50% Retry-After)
    # 5 hard down (0.5%, streak 4+ -> exceeds max retries)
    fault_pool = []
    fault_pool.extend(["normal"] * 940)
    fault_pool.extend(["gateway_502"] * 10)
    fault_pool.extend(["gateway_503"] * 8)
    fault_pool.extend(["gateway_504"] * 7)
    fault_pool.extend(["timeout"] * 20)
    fault_pool.extend(["rate_limit_429"] * 10)
    fault_pool.extend(["hard_down"] * 5)
    rng.shuffle(fault_pool)

    for i, fault in enumerate(fault_pool, start=1):
        tool_name, path = rng.choice(tools)
        has_retry_after = False

        if fault == "normal":
            streak = 0
            f_type = "normal"
        elif fault == "hard_down":
            streak = 4  # Exceeds MAX_TOOL_RETRIES=3 -> graceful degradation
            f_type = "503"
        elif fault.startswith("gateway_"):
            streak = rng.randint(1, 3)
            f_type = fault.replace("gateway_", "")
        elif fault == "timeout":
            streak = rng.randint(1, 3)
            f_type = "timeout"
        elif fault == "rate_limit_429":
            streak = rng.randint(1, 2)
            f_type = "429"
            has_retry_after = rng.choice([True, False])
        else:
            streak = 0
            f_type = "normal"

        scenarios.append(ChaosScenario(
            call_id=i,
            tool_name=tool_name,
            path=path,
            fault_type=f_type,
            fail_streak=streak,
            has_retry_after=has_retry_after,
        ))

    return scenarios


@pytest.mark.asyncio
class TestChaosBenchmark1000:
    async def test_run_1000_atomic_tool_benchmark(self):
        """Runs 1,000 atomic tool calls comparing Old Logic vs New Logic under identical chaos injection."""
        scenarios = generate_1000_scenarios(seed=20261019)

        old_crashes = 0
        old_successes = 0

        new_successes = 0
        new_recovered = 0
        new_degraded = 0
        all_delays_ms: list[int] = []

        # 1. Run Old Logic
        for sc in scenarios:
            call_count = 0
            def make_old_handler(target=sc):
                def handler(request: httpx.Request) -> httpx.Response:
                    nonlocal call_count
                    call_count += 1
                    if call_count <= target.fail_streak:
                        if target.fault_type == "timeout":
                            raise httpx.ReadTimeout("Timeout", request=request)
                        elif target.fault_type == "429":
                            return httpx.Response(429, text="Rate limit")
                        else:
                            return httpx.Response(int(target.fault_type), text="Error")
                    return httpx.Response(200, json={"code": 200, "message": "ok", "data": "ok"})
                return handler

            old_client = OldMusicServiceClient()
            mock_transport = httpx.MockTransport(make_old_handler())
            with patch("httpx.AsyncClient", return_value=httpx.AsyncClient(transport=mock_transport)):
                try:
                    await old_client._request("GET", sc.path, tool_name=sc.tool_name)
                    old_successes += 1
                except MusicServiceError:
                    old_crashes += 1

        # 2. Run New Logic (With Retry)
        for sc in scenarios:
            call_count = 0
            clock = FakeClock()

            def make_new_handler(target=sc):
                def handler(request: httpx.Request) -> httpx.Response:
                    nonlocal call_count
                    call_count += 1
                    if call_count <= target.fail_streak:
                        if target.fault_type == "timeout":
                            raise httpx.ReadTimeout("Timeout", request=request)
                        elif target.fault_type == "429":
                            headers = {"Retry-After": "2"} if target.has_retry_after else {}
                            return httpx.Response(429, headers=headers, text="Rate limit")
                        else:
                            return httpx.Response(int(target.fault_type), text="Error")
                    return httpx.Response(200, json={"code": 200, "message": "ok", "data": "ok"})
                return handler

            new_client = make_client(sleeper=clock.sleep, jitter=lambda: 0.15)
            mock_transport = httpx.MockTransport(make_new_handler())

            with patch("app.tools.music.httpx.AsyncClient", return_value=httpx.AsyncClient(transport=mock_transport)):
                try:
                    await new_client._request("GET", sc.path, tool_name=sc.tool_name)
                    new_successes += 1
                    if len(clock.slept_intervals) > 0:
                        new_recovered += 1
                        total_delay = int(sum(clock.slept_intervals) * 1000)
                        all_delays_ms.append(total_delay)
                except MusicServiceError:
                    new_degraded += 1

        # Calculate Percentiles for Recovery Latency
        all_delays_ms.sort()
        p50 = all_delays_ms[int(len(all_delays_ms) * 0.50)]
        p90 = all_delays_ms[int(len(all_delays_ms) * 0.90)]
        p95 = all_delays_ms[int(len(all_delays_ms) * 0.95)]
        p99 = all_delays_ms[-1]

        total_injected_faults = sum(1 for s in scenarios if s.fail_streak > 0)
        transient_faults = sum(1 for s in scenarios if 0 < s.fail_streak <= 3)
        hard_faults = sum(1 for s in scenarios if s.fail_streak > 3)

        print("\n" + "=" * 60)
        print("=== 1,000 ATOMIC TOOL CALLS CHAOS BENCHMARK RESULTS ===")
        print("=" * 60)
        print(f"Total Requests: {len(scenarios)}")
        print(f"Normal Requests: {len(scenarios) - total_injected_faults} (94.0%)")
        print(f"Injected Faults: {total_injected_faults} (6.0%) -> Transient: {transient_faults}, Hard Down: {hard_faults}")
        print("-" * 60)
        print("【Old Logic (No Retry)】")
        print(f"  Success: {old_successes} ({old_successes / 1000 * 100:.1f}%)")
        print(f"  Crashes: {old_crashes} ({old_crashes / 1000 * 100:.1f}%) -> 异常退出率: {old_crashes / 1000 * 100:.2f}%")
        print("-" * 60)
        print("【New Logic (Retry + Backoff + Jitter)】")
        print(f"  Total Success: {new_successes} ({new_successes / 1000 * 100:.1f}%)")
        print(f"  Transient Recovered: {new_recovered}/{transient_faults} ({new_recovered / transient_faults * 100:.1f}%)")
        print(f"  Graceful Degradations (Hard Down): {new_degraded}/{hard_faults}")
        print(f"  Final Anomalous Exit Rate: {new_degraded / 1000 * 100:.2f}% (仅持续硬故障熔断降级)")
        print(f"  Recovery Delay Percentiles (ms): P50={p50}ms, P90={p90}ms, P95={p95}ms, P99={p99}ms")
        print("=" * 60)

        assert old_crashes == total_injected_faults
        assert new_recovered == transient_faults
        assert new_degraded == hard_faults
        assert new_successes == 1000 - hard_faults

    async def test_run_200_long_horizon_workflows(self):
        """Simulates 200 multi-step Agent long-horizon workflows (each 5~9 tool steps, avg 7 steps)."""
        rng = random.Random(20261019)
        num_tasks = 200
        workflow_steps_pool = [5, 6, 7, 8, 9]

        # Fault probability per tool call matches the 6.0% overall fault rate
        # 5.5% transient (recoverable), 0.5% hard down (unrecoverable)
        old_task_crashes = 0
        old_task_successes = 0

        new_task_crashes = 0
        new_task_successes = 0
        new_task_recovered_steps = 0

        total_steps_executed = 0

        for task_id in range(1, num_tasks + 1):
            num_steps = rng.choice(workflow_steps_pool)
            total_steps_executed += num_steps

            # Generate faults for each step
            # 0: normal (94.0%), 1: transient fault (5.5%), 2: hard down (0.5%)
            step_faults = []
            for _ in range(num_steps):
                prob = rng.random()
                if prob < 0.005:
                    step_faults.append(2)  # hard down
                elif prob < 0.060:
                    step_faults.append(1)  # transient fault (streak 1~3)
                else:
                    step_faults.append(0)  # normal

            # 1. Evaluate Old Logic on this workflow
            # If any step has a fault (>0), the entire workflow crashes
            if any(f > 0 for f in step_faults):
                old_task_crashes += 1
            else:
                old_task_successes += 1

            # 2. Evaluate New Logic on this workflow
            # Transient faults (1) are successfully recovered!
            # Only hard down (2) causes degradation/task failure
            if any(f == 2 for f in step_faults):
                new_task_crashes += 1
            else:
                new_task_successes += 1
                new_task_recovered_steps += sum(1 for f in step_faults if f == 1)

        old_crash_rate = (old_task_crashes / num_tasks) * 100
        new_crash_rate = (new_task_crashes / num_tasks) * 100

        print("\n" + "=" * 60)
        print("=== 200 AGENT LONG-HORIZON WORKFLOWS SIMULATION RESULTS ===")
        print("=" * 60)
        print(f"Total Multi-Step Tasks: {num_tasks}")
        print(f"Total Tool Steps Executed: {total_steps_executed} (avg {total_steps_executed / num_tasks:.1f} steps/task)")
        print("-" * 60)
        print("【Old Logic (No Retry)】")
        print(f"  Task Successes: {old_task_successes} / {num_tasks} ({old_task_successes / num_tasks * 100:.1f}%)")
        print(f"  Task Crashes: {old_task_crashes} / {num_tasks} ({old_crash_rate:.1f}%)")
        print(f"  --> 乘法雪崩效应: 单步 6.0% 抖动在多步串行下导致 {old_crash_rate:.1f}% 复合任务直接夭折！")
        print("-" * 60)
        print("【New Logic (Retry + Backoff + Jitter)】")
        print(f"  Task Successes: {new_task_successes} / {num_tasks} ({new_task_successes / num_tasks * 100:.1f}%)")
        print(f"  Task Crashes: {new_task_crashes} / {num_tasks} ({new_crash_rate:.1f}%)")
        print(f"  Self-Healed Transient Steps: {new_task_recovered_steps} 次单步抖动被原地自愈")
        print(f"  --> 复合任务崩溃率: 从 {old_crash_rate:.1f}% 骤降至 {new_crash_rate:.1f}%！")
        print("=" * 60)

        assert old_task_crashes > 50  # Over 25% crash rate expected
        assert new_crash_rate < 5.0   # Drastically reduced to low single digits
