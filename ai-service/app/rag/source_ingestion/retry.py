"""Typed fetch retry policy and persisted, host-wide retry deadlines."""

from __future__ import annotations

import errno
import math
import random
import re
import socket
import time
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from email.utils import parsedate_to_datetime
from urllib.parse import urlsplit

import httpx

from .http_client import ResponseTooLargeError, RobotsDeniedError


@dataclass(frozen=True)
class FailurePolicy:
    retryable: bool = False
    max_attempts: int | None = None
    category: str = "failure"


# max_attempts includes the first request: a bare 500 gets ONE retry.
FAILURE_POLICIES = {
    **{
        reason: FailurePolicy(True)
        for reason in (
            "dns_failure",
            "connection_reset",
            "connection_refused",
            "connection_error",
            "connect_timeout",
            "read_timeout",
            "network_timeout",
            "http_408",
            "http_502",
            "http_504",
            "empty_200",
        )
    },
    "http_500": FailurePolicy(True, max_attempts=2),
    **{
        reason: FailurePolicy(True, category="rate_limited")
        for reason in (
            "http_429",
            "http_503",
            "empty_200_rate_limited",
        )
    },
    "robots_denied": FailurePolicy(category="policy_denied"),
    **{f"http_{status}": FailurePolicy() for status in (400, 401, 403, 404, 410, 422)},
    "snapshot_invalid": FailurePolicy(),
    "response_too_large": FailurePolicy(),
    "unknown_failure": FailurePolicy(),
}


@dataclass(frozen=True)
class SourceRetryConfig:
    enabled: bool = True
    max_source_attempts: int = 3
    base_delay_seconds: float = 1.0
    max_delay_seconds: float = 60.0
    jitter_ratio: float = 0.25
    empty_200_threshold: int = 2

    def __post_init__(self) -> None:
        if self.max_source_attempts < 1 or self.empty_200_threshold < 2:
            raise ValueError(
                "retry attempt cap must be positive and empty threshold at least 2"
            )
        values = (self.base_delay_seconds, self.max_delay_seconds, self.jitter_ratio)
        if not all(math.isfinite(value) for value in values):
            raise ValueError("retry timing must be finite")
        if not 0 < self.base_delay_seconds <= self.max_delay_seconds:
            raise ValueError("retry delays require 0 < base <= max")
        if not 0 <= self.jitter_ratio <= 1:
            raise ValueError("retry jitter_ratio must be between 0 and 1")


def failure_policy(reason: str | None) -> FailurePolicy:
    return FAILURE_POLICIES.get(reason_code(reason), FailurePolicy())


def reason_code(reason: str | None) -> str:
    """Read new codes and recognizable old ledger reasons without rewriting history."""
    if reason in FAILURE_POLICIES or (
        reason is not None and re.fullmatch(r"http_\d{3}", reason)
    ):
        return str(reason)
    text = (reason or "").casefold()
    if "robots" in text:
        return "robots_denied"
    if text.startswith("httpstatuserror:"):
        match = re.search(r"\b([45]\d\d)\b", text)
        if match:
            return "http_" + match.group(1)
    if text.startswith("connecttimeout:"):
        return "connect_timeout"
    if text.startswith("readtimeout:"):
        return "read_timeout"
    if text.startswith(("timeouterror:", "timeoutexception:")):
        return "network_timeout"
    if text.startswith("connecterror:"):
        return "connection_error"
    if "snapshot_validation:" in text:
        return "snapshot_invalid"
    return "unknown_failure"


def classify_failure(error: Exception) -> str:
    if isinstance(error, RobotsDeniedError):
        return "robots_denied"
    if isinstance(error, httpx.HTTPStatusError):
        return f"http_{error.response.status_code}"
    if isinstance(error, httpx.ConnectTimeout):
        return "connect_timeout"
    if isinstance(error, httpx.ReadTimeout):
        return "read_timeout"
    if isinstance(error, (httpx.TimeoutException, TimeoutError)):
        return "network_timeout"
    current: BaseException | None = error
    seen: set[int] = set()
    while current is not None and id(current) not in seen:
        seen.add(id(current))
        if isinstance(current, socket.gaierror):
            return "dns_failure"
        if isinstance(current, ConnectionResetError) or getattr(
            current, "errno", None
        ) in {errno.ECONNRESET, 10054}:
            return "connection_reset"
        if isinstance(current, ConnectionRefusedError) or getattr(
            current, "errno", None
        ) in {errno.ECONNREFUSED, 10061}:
            return "connection_refused"
        current = current.__cause__ or current.__context__
    if isinstance(error, (httpx.ConnectError, httpx.ReadError, ConnectionError)):
        message = str(error).casefold()
        if any(
            value in message
            for value in ("name resolution", "getaddrinfo", "name or service not known")
        ):
            return "dns_failure"
        if "reset" in message:
            return "connection_reset"
        if "refused" in message:
            return "connection_refused"
        return "connection_error"
    if isinstance(error, ResponseTooLargeError):
        return "response_too_large"
    if isinstance(error, ValueError) and str(error).startswith("snapshot_validation:"):
        return "snapshot_invalid"
    return "unknown_failure"


def retry_after_seconds(value: str | None, now: datetime) -> float | None:
    if value is None:
        return None
    value = value.strip()
    try:
        if re.fullmatch(r"\d+", value):
            seconds = float(value)
        else:
            deadline = parsedate_to_datetime(value)
            if deadline.tzinfo is None:
                return None
            seconds = max(0.0, (deadline - now).total_seconds())
        return seconds if math.isfinite(seconds) else None
    except (ValueError, TypeError, OverflowError):
        return None


def retry_host(url: str) -> str:
    return (urlsplit(url).hostname or "").casefold()


class HostRetryScheduler:
    def __init__(
        self,
        config: SourceRetryConfig,
        *,
        now: Callable[[], datetime] = lambda: datetime.now(UTC),
        sleeper: Callable[[float], None] = time.sleep,
        jitter: Callable[[], float] = random.random,
    ) -> None:
        self.config, self.now, self.sleeper, self.jitter = config, now, sleeper, jitter
        self.deadlines: dict[str, datetime] = {}
        self.streaks: dict[str, int] = {}
        self.empty_streaks: dict[str, int] = {}

    def restore(self, records: Iterable) -> None:
        self.deadlines.clear()
        self.streaks.clear()
        self.empty_streaks.clear()
        for record in records:
            host = record.retry_host or retry_host(str(record.url))
            if record.status.value == "snapshot_valid":
                self.streaks[host] = self.empty_streaks[host] = 0
            elif record.status.value == "fetch_failed":
                self.empty_streaks[host] = record.empty_200_streak
                self.streaks[host] = record.host_failure_streak
                if record.retry_not_before is not None:
                    old = self.deadlines.get(host, record.retry_not_before)
                    self.deadlines[host] = max(old, record.retry_not_before)

    def wait(self, host: str) -> None:
        while host in self.deadlines:
            remaining = (self.deadlines[host] - self.now()).total_seconds()
            if remaining <= 0:
                return
            # A long server Retry-After is never shortened by the local cap.
            self.sleeper(min(60.0, remaining))

    def failed(
        self, host: str, reason: str, server_delay: float | None = None
    ) -> datetime:
        streak = self.streaks.get(host, 0) + 1
        self.streaks[host] = streak
        delay = min(
            # Reserve headroom before jitter so saturated hosts still spread
            # their requests instead of all sleeping exactly the cap.
            self.config.max_delay_seconds / (1 + self.config.jitter_ratio),
            self.config.base_delay_seconds * 2 ** min(streak - 1, 30),
        )
        delay = min(
            self.config.max_delay_seconds,
            delay * (1 + self.config.jitter_ratio * self.jitter()),
        )
        now = self.now()
        deadline = now + timedelta(seconds=max(delay, server_delay or 0))
        self.deadlines[host] = max(self.deadlines.get(host, deadline), deadline)
        return self.deadlines[host]

    def server_deadline(self, host: str, seconds: float) -> datetime:
        deadline = self.now() + timedelta(seconds=seconds)
        self.deadlines[host] = max(self.deadlines.get(host, deadline), deadline)
        return self.deadlines[host]

    def observe_empty(self, host: str, empty: bool) -> str | None:
        self.empty_streaks[host] = self.empty_streaks.get(host, 0) + 1 if empty else 0
        if not empty:
            return None
        return (
            "empty_200_rate_limited"
            if self.empty_streaks[host] >= self.config.empty_200_threshold
            else "empty_200"
        )

    def succeeded(self, host: str) -> None:
        self.streaks[host] = self.empty_streaks[host] = 0
