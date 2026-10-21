"""Fetch policy tests use fake time, synthetic raw bytes, and HTTP transports only."""

from collections import deque
from datetime import UTC, datetime, timedelta
from email.utils import format_datetime
import json
import socket

import httpx
import pytest

from app.rag.source_ingestion.attempts import SourceAttemptLedger
from app.rag.source_ingestion.auto_service import AutoSourceIngestionService
from app.rag.source_ingestion.http_client import (
    PolicyAwareHttpClient,
    RawHttpResponse,
    RobotsDeniedError,
)
from app.rag.source_ingestion.quality_filter import AutoSourceQualityFilter
from app.rag.source_ingestion.report import SourceCorpusReporter
from app.rag.source_ingestion.retry import (
    HostRetryScheduler,
    SourceRetryConfig,
    classify_failure,
    retry_after_seconds,
)
from app.rag.source_ingestion.snapshot import RawSnapshotStore
from app.rag.source_ingestion.v2_models import SourceAttemptRecord, SourceAttemptStatus
from app.rag.source_ingestion.validation import SnapshotValidator
from scripts import source_ingestion_v2
from tests.test_source_ingestion_v2_service import _candidate, _valid_html


class Clock:
    def __init__(self):
        self.value = datetime(2026, 10, 16, tzinfo=UTC)
        self.sleeps = []

    def now(self):
        return self.value

    def sleep(self, seconds):
        self.sleeps.append(seconds)
        self.value += timedelta(seconds=seconds)


class SequenceClient:
    def __init__(self, outcomes, clock):
        self.outcomes = deque(outcomes)
        self.clock = clock
        self.calls = []

    def fetch(self, url):
        self.calls.append((url, self.clock.now()))
        value = self.outcomes[0]
        if len(self.outcomes) > 1:
            self.outcomes.popleft()
        if isinstance(value, Exception):
            raise value
        return value


def status_error(status, retry_after=None, url="https://example.test/source"):
    headers = {} if retry_after is None else {"Retry-After": retry_after}
    response = httpx.Response(
        status, headers=headers, request=httpx.Request("GET", url)
    )
    return httpx.HTTPStatusError(
        "synthetic HTTP error", request=response.request, response=response
    )


def make_service(root, outcomes, config=None, clock=None):
    config, clock = config or SourceRetryConfig(), clock or Clock()
    client = SequenceClient(outcomes, clock)
    scheduler = HostRetryScheduler(
        config, now=clock.now, sleeper=clock.sleep, jitter=lambda: 0
    )
    service = AutoSourceIngestionService(
        snapshot_store=RawSnapshotStore(root, now=clock.now),
        http_client=client,
        validator=SnapshotValidator(),
        quality_filter=AutoSourceQualityFilter(),
        attempt_ledger=SourceAttemptLedger(
            root / "data/raw/source_attempts.jsonl", now=clock.now
        ),
        existing_candidates=[],
        retry_config=config,
        retry_scheduler=scheduler,
    )
    return service, client, clock


def run(service, cap=3, reopen=False):
    return service.run(
        [_candidate("retry-source", group="retry-group", priority=1)],
        target_count=1,
        max_replacement_attempts=cap,
        reopen_failed=reopen,
    )


@pytest.mark.parametrize(
    ("error", "reason"),
    [
        (socket.gaierror(-2, "name or service not known"), "dns_failure"),
        (ConnectionResetError("reset"), "connection_reset"),
        (ConnectionRefusedError("refused"), "connection_refused"),
        (httpx.ConnectError("connection refused"), "connection_refused"),
        (httpx.ConnectError("getaddrinfo failed"), "dns_failure"),
        (httpx.ReadError("connection reset"), "connection_reset"),
        (httpx.ConnectError("connect"), "connection_error"),
        (httpx.ConnectTimeout("connect"), "connect_timeout"),
        (httpx.ReadTimeout("read"), "read_timeout"),
        (TimeoutError("timeout"), "network_timeout"),
    ],
)
def test_network_types_retry_and_record_reason(tmp_path, error, reason):
    service, client, clock = make_service(tmp_path, [error, _valid_html("recovered")])
    summary = run(service)
    assert (summary.fetch_attempted, summary.fetch_failed, summary.fetch_success) == (
        2,
        1,
        1,
    )
    assert service.attempt_ledger.load()[0].reason == reason
    assert clock.sleeps == [1.0]
    assert len(client.calls) == 2


def test_wrapped_os_error_is_classified():
    error = httpx.ConnectError("transport failed")
    error.__cause__ = socket.gaierror(-2, "dns")
    assert classify_failure(error) == "dns_failure"


@pytest.mark.parametrize("status", [408, 429, 503, 502, 504, 500])
def test_retryable_http_types_recover(tmp_path, status):
    service, client, _ = make_service(
        tmp_path, [status_error(status), _valid_html("recovered")]
    )
    summary = run(service)
    failure = next(
        row
        for row in service.attempt_ledger.load()
        if row.status is SourceAttemptStatus.FETCH_FAILED
    )
    assert failure.reason == f"http_{status}"
    assert summary.fetch_success == 1 and len(client.calls) == 2
    assert summary.rate_limited == int(status in {429, 503})
    assert summary.fetch_failed == int(status not in {429, 503})


@pytest.mark.parametrize("status", [400, 401, 403, 404, 410, 422, 501])
def test_terminal_http_types_do_not_retry_across_runs(tmp_path, status):
    service, client, clock = make_service(
        tmp_path, [status_error(status), _valid_html("unused")]
    )
    first = run(service, cap=10)
    second = run(service, cap=10)
    assert (first.fetch_failed, second.fetch_attempted, len(client.calls)) == (1, 0, 1)
    assert clock.sleeps == []
    assert service.attempt_ledger.load()[0].reason == f"http_{status}"


def test_bare_500_has_exactly_one_retry_even_with_large_budget(tmp_path):
    service, client, clock = make_service(tmp_path, [status_error(500)])
    assert run(service, cap=10).fetch_attempted == 2
    assert run(service, cap=10).fetch_attempted == 0
    assert len(client.calls) == 2 and clock.sleeps == [1.0]


@pytest.mark.parametrize("status", [429, 503])
@pytest.mark.parametrize("header", ["125", "date"])
def test_retry_after_is_a_lower_bound_and_survives_restart(tmp_path, status, header):
    clock = Clock()
    value = (
        format_datetime(clock.now() + timedelta(seconds=125), usegmt=True)
        if header == "date"
        else header
    )
    service, _, _ = make_service(tmp_path, [status_error(status, value)], clock=clock)
    assert run(service, cap=1).rate_limited == 1
    failure = service.attempt_ledger.load()[0]
    assert failure.retry_after_seconds == 125
    assert failure.retry_not_before == clock.now() + timedelta(seconds=125)
    # New scheduler and client; only the ledger carries the deadline/budget.
    second, client, _ = make_service(tmp_path, [_valid_html("later")], clock=clock)
    assert run(second, cap=1).fetch_success == 1
    assert clock.sleeps == [60.0, 60.0, 5.0]
    assert client.calls[0][1] == failure.retry_not_before


@pytest.mark.parametrize("value", [None, "bad", "-3", "1.5"])
def test_invalid_retry_after_falls_back_to_local_backoff(value):
    assert retry_after_seconds(value, Clock().now()) is None


def test_expired_retry_after_date_is_zero():
    clock = Clock()
    value = format_datetime(clock.now() - timedelta(seconds=30), usegmt=True)
    assert retry_after_seconds(value, clock.now()) == 0


def test_host_backoff_exponential_jitter_cap_and_isolation():
    clock = Clock()
    config = SourceRetryConfig(
        base_delay_seconds=2, max_delay_seconds=10, jitter_ratio=0.5
    )
    scheduler = HostRetryScheduler(
        config, now=clock.now, sleeper=clock.sleep, jitter=lambda: 1
    )
    delays = []
    for _ in range(6):
        before = clock.now()
        scheduler.failed("same.test", "http_429")
        scheduler.wait("other.test")
        assert clock.now() == before
        scheduler.wait("same.test")
        delays.append((clock.now() - before).total_seconds())
    assert delays == [3, 6, 10, 10, 10, 10]
    scheduler.succeeded("same.test")
    scheduler.failed("same.test", "http_503")
    scheduler.wait("same.test")
    assert clock.sleeps[-1] == 3


def test_jitter_still_varies_when_host_backoff_reaches_cap():
    delays = []
    for sample in (0.0, 1.0):
        clock = Clock()
        scheduler = HostRetryScheduler(
            SourceRetryConfig(),
            now=clock.now,
            sleeper=clock.sleep,
            jitter=lambda: sample,
        )
        for _ in range(10):
            scheduler.failed("busy.test", "http_429")
            scheduler.wait("busy.test")
        delays.append(clock.sleeps[-1])
    assert delays == [48.0, 60.0]


def test_host_limit_applies_to_different_source_in_same_group(tmp_path):
    first = _candidate("retry-first", group="retry-group", priority=1)
    second = _candidate("retry-second", group="retry-group", priority=2)
    service, client, clock = make_service(
        tmp_path, [status_error(429, "12"), _valid_html("replacement")]
    )
    summary = service.run([first, second], target_count=1, max_replacement_attempts=2)
    assert [call[0] for call in client.calls] == [str(first.url), str(second.url)]
    assert summary.fetch_success == 1 and clock.sleeps == [12]


def test_source_budget_persists_but_exhausted_group_reopens(tmp_path):
    service, client, _ = make_service(tmp_path, [httpx.ReadTimeout("read")])
    assert [run(service, cap=1).fetch_attempted for _ in range(4)] == [1, 1, 1, 0]
    failures = [
        row
        for row in service.attempt_ledger.load()
        if row.status is SourceAttemptStatus.FETCH_FAILED
    ]
    assert [row.source_attempt_number for row in failures] == [1, 2, 3]
    assert len(client.calls) == 3
    client.outcomes = deque([_valid_html("manual after exhaustion")])
    assert run(service, cap=1, reopen=True).fetch_success == 1
    assert service.attempt_ledger.load()[-1].source_attempt_number == 1


def test_manual_flag_reopens_permanent_and_exhausted_sources(tmp_path):
    service, client, _ = make_service(
        tmp_path, [status_error(404), _valid_html("manual")]
    )
    assert run(service).fetch_failed == 1
    assert run(service).fetch_attempted == 0
    assert run(service, cap=1, reopen=True).fetch_success == 1
    assert len(client.calls) == 2


def test_disabled_mode_needs_manual_flag_and_has_no_backoff(tmp_path):
    service, client, clock = make_service(
        tmp_path,
        [httpx.ReadTimeout("read"), _valid_html("manual")],
        SourceRetryConfig(enabled=False),
    )
    assert run(service).fetch_attempted == 1
    assert run(service).fetch_attempted == 0
    assert run(service, reopen=True).fetch_success == 1
    assert clock.sleeps == [] and len(client.calls) == 2


def test_manual_retry_with_switch_off_still_obeys_server_retry_after(tmp_path):
    service, _, clock = make_service(
        tmp_path,
        [status_error(429, "15"), _valid_html("manual")],
        SourceRetryConfig(enabled=False),
    )
    assert run(service).fetch_attempted == 1
    assert run(service).fetch_attempted == 0
    assert run(service, reopen=True).fetch_success == 1
    assert clock.sleeps == [15]


def test_robots_is_visible_terminal_and_manual_does_not_bypass_policy(tmp_path):
    service, client, clock = make_service(tmp_path, [RobotsDeniedError("disallowed")])
    summary = run(service)
    assert (summary.robots_denied, summary.fetch_failed, summary.rate_limited) == (
        1,
        0,
        0,
    )
    assert run(service).fetch_attempted == 0
    assert run(service, reopen=True).robots_denied == 1
    assert len(client.calls) == 2 and clock.sleeps == []


def test_consecutive_empty_200_threshold_and_no_snapshot(tmp_path):
    empty = RawHttpResponse(b" \n", "text/html", 200)
    service, client, _ = make_service(tmp_path, [empty])
    summary = run(service)
    failures = [
        row
        for row in service.attempt_ledger.load()
        if row.status is SourceAttemptStatus.FETCH_FAILED
    ]
    assert [row.reason for row in failures] == [
        "empty_200",
        "empty_200_rate_limited",
        "empty_200_rate_limited",
    ]
    assert [row.empty_200_streak for row in failures] == [1, 2, 3]
    assert (summary.fetch_failed, summary.rate_limited, len(client.calls)) == (1, 2, 3)
    assert not service.snapshot_store.manifest_path.exists()


def test_nonempty_response_breaks_empty_streak_and_legacy_ledger_is_readable(tmp_path):
    service, _, clock = make_service(tmp_path, [RawHttpResponse(b"", "text/html", 200)])
    run(service, cap=1)
    # Old schema/reason remain readable and identifiable; no ledger migration.
    path = service.attempt_ledger.path
    rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
    fields = set(SourceAttemptRecord.model_fields) - {
        "source_attempt_number",
        "retry_host",
        "retry_after_seconds",
        "retry_not_before",
        "host_failure_streak",
        "empty_200_streak",
        "failure_detail",
    }
    rows[0] = {key: value for key, value in rows[0].items() if key in fields}
    rows[0]["reason"] = "ReadTimeout:read timed out"
    path.write_text("\n".join(json.dumps(row) for row in rows) + "\n", encoding="utf-8")
    second, _, _ = make_service(tmp_path, [_valid_html("recovered")], clock=clock)
    assert run(second).fetch_success == 1
    assert second.retry_scheduler.empty_streaks["example.test"] == 0


def test_empty_streak_is_host_local_and_resets_on_nonempty_or_success():
    scheduler = HostRetryScheduler(SourceRetryConfig())
    assert scheduler.observe_empty("one.test", True) == "empty_200"
    assert scheduler.observe_empty("two.test", True) == "empty_200"
    assert scheduler.observe_empty("one.test", False) is None
    assert scheduler.observe_empty("one.test", True) == "empty_200"
    assert scheduler.observe_empty("one.test", True) == "empty_200_rate_limited"
    scheduler.succeeded("one.test")
    assert scheduler.observe_empty("one.test", True) == "empty_200"


def test_health_report_separates_rate_limits_and_robots(tmp_path):
    service, _, _ = make_service(
        tmp_path, [status_error(429), status_error(503), RobotsDeniedError("deny")]
    )
    summary = run(service)
    report = SourceCorpusReporter(tmp_path).write_outputs(
        existing_candidates=[],
        discovered_candidates=[],
        attempts=service.attempt_ledger.load(),
    )
    assert (summary.fetch_failed, report["fetch_failed"]) == (0, 0)
    assert (
        report["rate_limited"],
        report["robots_denied"],
        report["fetch_attempted"],
    ) == (2, 1, 3)
    assert report["rate_limit_distribution"] == {"http_429": 1, "http_503": 1}
    assert report["fetch_failure_distribution"] == {}
    assert "robots policy denials: 1" in (
        tmp_path / "data/raw/source_corpus_report.md"
    ).read_text(encoding="utf-8")


def test_cli_switch_and_source_cap_defaults():
    default = source_ingestion_v2.parse_args(["run"])
    disabled = source_ingestion_v2.parse_args(
        ["run", "--no-auto-retry", "--max-source-attempts", "2", "--reopen-failed"]
    )
    assert default.auto_retry and default.max_source_attempts == 3
    assert (
        not disabled.auto_retry
        and disabled.max_source_attempts == 2
        and disabled.reopen_failed
    )


def test_cli_rejects_zero_source_cap_before_reading_any_corpus():
    with pytest.raises(SystemExit, match="max-source-attempts must be positive"):
        source_ingestion_v2.main(["run", "--max-source-attempts", "0"])


def test_redirected_requests_obey_host_deadline_and_robots(tmp_path):
    clock = Clock()
    calls = []
    source_calls = [0]

    def handler(request):
        calls.append((str(request.url), clock.now()))
        if request.url.path == "/robots.txt":
            return httpx.Response(404)
        if request.url.host == "example.test":
            return httpx.Response(
                302, headers={"Location": "https://target.test/source"}
            )
        source_calls[0] += 1
        if source_calls[0] == 1:
            return httpx.Response(503, headers={"Retry-After": "9"})
        return httpx.Response(
            200,
            content=_valid_html("redirected").content,
            headers={"Content-Type": "text/html"},
        )

    service, _, _ = make_service(tmp_path, [], clock=clock)
    with httpx.Client(transport=httpx.MockTransport(handler)) as raw:
        service.http_client = PolicyAwareHttpClient(client=raw, min_interval_seconds=0)
        assert run(service).fetch_success == 1
    target = [at for url, at in calls if url == "https://target.test/source"]
    assert (target[1] - target[0]).total_seconds() >= 9
    assert clock.sleeps == [9]
    assert service.retry_scheduler.streaks["target.test"] == 0


def test_actual_robots_disallow_never_fetches_body_even_with_manual_flag(tmp_path):
    calls = []

    def handler(request):
        calls.append(str(request.url))
        assert request.url.path == "/robots.txt"
        return httpx.Response(
            200,
            text="User-agent: *\nDisallow: /",
            headers={"Content-Type": "text/plain"},
        )

    service, _, _ = make_service(tmp_path, [])
    with httpx.Client(transport=httpx.MockTransport(handler)) as raw:
        service.http_client = PolicyAwareHttpClient(client=raw, min_interval_seconds=0)
        assert run(service).robots_denied == 1
        assert run(service).fetch_attempted == 0
        assert run(service, reopen=True).robots_denied == 1
    assert calls == ["https://example.test/robots.txt"]


@pytest.mark.parametrize("failure", ["dns", 429, 503])
def test_robots_transport_outage_is_not_cached_as_policy_denial(tmp_path, failure):
    clock = Clock()
    robots_calls = [0]
    body_times = []

    def handler(request):
        if request.url.path == "/robots.txt":
            robots_calls[0] += 1
            if robots_calls[0] == 1:
                if failure == "dns":
                    raise httpx.ConnectError("getaddrinfo failed", request=request)
                return httpx.Response(failure, headers={"Retry-After": "7"})
            return httpx.Response(
                200,
                text="User-agent: *\nAllow: /",
                headers={"Content-Type": "text/plain"},
            )
        body_times.append(clock.now())
        return httpx.Response(
            200,
            content=_valid_html("robots recovered").content,
            headers={"Content-Type": "text/html"},
        )

    service, _, _ = make_service(tmp_path, [], clock=clock)
    with httpx.Client(transport=httpx.MockTransport(handler)) as raw:
        service.http_client = PolicyAwareHttpClient(client=raw, min_interval_seconds=0)
        summary = run(service)
    assert summary.fetch_success == 1 and summary.robots_denied == 0
    assert summary.rate_limited == int(failure in {429, 503})
    assert summary.fetch_failed == int(failure == "dns")
    assert robots_calls[0] == 2 and len(body_times) == 1
    assert clock.sleeps == [1 if failure == "dns" else 7]


@pytest.mark.parametrize(
    "updates",
    [
        {"max_source_attempts": 0},
        {"empty_200_threshold": 1},
        {"base_delay_seconds": 0},
        {"max_delay_seconds": 0.5},
        {"jitter_ratio": -1},
        {"jitter_ratio": float("nan")},
    ],
)
def test_retry_config_rejects_invalid_budgets(updates):
    with pytest.raises(ValueError):
        SourceRetryConfig(**updates)
