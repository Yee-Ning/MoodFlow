"""Receiver delivery, privacy, denominators and ACK deadline/deduplication."""

import json
from datetime import datetime, timedelta, timezone
from uuid import uuid4

import pytest

from deploy.handoff_observer import (
    PREFIX,
    Receiver,
    cohort_summary,
    consecutive_breaches,
    parse,
    read_records,
    summarize,
    window_breaches,
)

NOW = datetime(2026, 10, 4, 7, tzinfo=timezone.utc)


def event(marker, *, age=0, **values):
    return {
        "marker": marker,
        "at_utc": (NOW - timedelta(seconds=age)).isoformat(),
        "event_id": uuid4().hex,
        "sample_kind": "acceptance",
        "cohort": "canary",
        "graph_mode": "handoff",
        "request_key": uuid4().hex,
        **values,
    }


def wire(record):
    return (
        "INFO:"
        + PREFIX
        + record["marker"]
        + " "
        + json.dumps({key: value for key, value in record.items() if key != "marker"})
    )


def test_receiver_delivers_and_fsyncs_real_trigger_once_across_restart(tmp_path):
    alert = event(
        "handoff.alert",
        alert_code="v5_hybrid_fallback",
        fallback_event_id=uuid4().hex,
        sample_kind="synthetic",
    )
    receiver = Receiver(tmp_path)
    receipt = receiver.ingest(wire(alert))
    assert (
        receipt["marker"] == "handoff.alert_delivered"
        and receipt["receiver"] == "private_local_fsync_file"
    )
    receiver.ingest(wire(alert))
    Receiver(tmp_path).ingest(wire(alert))
    rows = read_records(receiver.events_path)
    deliveries = read_records(receiver.alerts_path)
    assert (
        len(rows) == len(deliveries) == 1
        and deliveries[0]["alert_event_id"] == alert["event_id"]
    )
    result = summarize(rows, deliveries, now=NOW)
    assert result["alerts_generated"] == result["alerts_delivered"] == 1
    assert result["groups"]["production"]["canary"]["v5_attempts"] == 0


def test_receiver_retries_delivery_after_event_was_saved(tmp_path):
    alert = event("handoff.alert", alert_code="v5_hybrid_fallback")
    receiver = Receiver(tmp_path)
    receiver.events_path.write_text(json.dumps(alert) + "\n")
    receipt = Receiver(tmp_path).ingest(wire(alert))
    assert receipt["marker"] == "handoff.alert_delivered"
    assert len(read_records(receiver.events_path)) == 1


def test_privacy_filter_and_explicit_bad_event(tmp_path):
    raw = event(
        "jev.handoff_guard",
        score=0.85,
        scores={"task_fidelity": 0.9, "authority_override": 0.15, "private": "secret"},
        user_text="do not copy",
        token="secret",
        threshold=0.8,
    )
    row = parse(wire(raw))
    assert (
        "user_text" not in row and "token" not in row and "private" not in row["scores"]
    )
    assert parse("WARNING:other:" + wire(raw)) is None
    receiver = Receiver(tmp_path)
    receiver.ingest(wire({**raw, "score": float("nan")}))
    assert summarize(read_records(receiver.events_path))["invalid_events"] == 1


def test_candidate_denominators_exclude_rules_and_report_missing_latency():
    rows = [
        event(
            "jev.handoff_entry",
            decision_class="Single",
            attempt_class="Single",
            v5_attempted=True,
            latency_ms=100,
        ),
        event(
            "jev.handoff_entry",
            decision_class="Multi",
            attempt_class="Multi",
            v5_attempted=True,
            latency_ms=300,
        ),
        event(
            "jev.handoff_entry",
            decision_class="fallback",
            attempt_class="Single",
            v5_attempted=True,
        ),
        event("jev.handoff_entry_local", decision_class="rule", v5_attempted=False),
    ]
    result = cohort_summary(rows, now=NOW)
    assert result["v5_attempts"] == 3 and result["v5_success_rate"] == pytest.approx(
        2 / 3
    )
    assert (
        result["fallback_rate"] == pytest.approx(1 / 3) and result["local_rules"] == 1
    )
    assert (
        result["v5_p50_ms"] == 200
        and result["v5_p95_ms"] == 290
        and result["v5_missing_latency"] == 1
    )
    assert result["v5_latency_by_class"]["Multi"]["p95_ms"] == 300


def test_guard_raw_and_derived_bins_and_missing_scores():
    rows = [
        event(
            "jev.handoff_guard",
            score=value,
            scores={"task_fidelity": value, "authority_override": 0},
        )
        for value in (0.0, 0.7, 0.8, 0.9, 1)
    ] + [event("jev.handoff_guard", score=None)]
    result = cohort_summary(rows, now=NOW)["guard_histograms"]
    assert result["display"]["bins"] == {
        "[0,.7)": 1,
        "[.7,.8)": 1,
        "[.8,.9)": 1,
        "[.9,1]": 2,
    }
    assert (
        result["display"]["missing"] == 1
        and result["authority_override"]["bins"]["[0,.7)"] == 5
    )


def test_ack_unique_actions_pending_rejected_timeout_and_late_receipts():
    actions = [
        event("handoff.action", action_key=key, age=age)
        for key, age in [
            ("ok", 70),
            ("rejected", 70),
            ("timeout", 70),
            ("pending", 20),
            ("late", 70),
        ]
    ]
    receipts = [
        event("handoff.ack", action_key="ok", status="applied", age=65),
        event("handoff.ack", action_key="ok", status="applied", age=60),
        event("handoff.ack", action_key="rejected", status="rejected", age=65),
        event("handoff.ack", action_key="late", status="applied", age=1),
    ]
    result = cohort_summary(actions + actions + receipts, now=NOW)["ack"]
    assert (
        result["unique_actions"] == 5
        and result["applied"] == 1
        and result["rejected"] == 1
    )
    assert (
        result["timed_out"] == 2
        and result["pending"] == 1
        and result["late_receipts"] == 1
    )
    assert result["eligible_actions"] == 4 and result["success_rate"] == 0.25


def test_unscoped_receipt_joins_original_action_and_source():
    action = event("handoff.action", action_key="one", age=30)
    ack = event(
        "handoff.ack",
        action_key="one",
        age=20,
        status="applied",
        sample_kind="unknown",
        cohort="unknown",
    )
    summary = summarize([action, ack], now=NOW)
    assert summary["groups"]["acceptance"]["canary"]["ack"]["success_rate"] == 1
    assert summary["groups"]["production"]["canary"]["ack"]["unique_actions"] == 0
    assert summary["unscoped_events"] == 0


def test_empty_denominators_are_null_and_inflight_is_explicit():
    result = cohort_summary([event("handoff.request", phase="started")], now=NOW)
    assert result["missing_terminals"] == 1 and result["v5_success_rate"] is None
    assert (
        result["ack"]["success_rate"] is None and result["clarification_rate"] is None
    )


def test_window_rules_require_real_samples_and_two_adjacent_windows():
    rows = [
        event(
            "jev.handoff_entry",
            sample_kind="production",
            decision_class="error",
            attempt_class="Multi",
            v5_attempted=True,
            latency_ms=6000,
        )
        for _ in range(20)
    ]
    breached = summarize(rows, now=NOW)
    assert window_breaches(breached) == {"error_rate", "latency_all", "latency_Multi"}
    assert consecutive_breaches(breached, breached) == [
        "error_rate",
        "latency_Multi",
        "latency_all",
    ]
    assert consecutive_breaches(breached, summarize(rows[:19], now=NOW)) == []
    assert (
        window_breaches(
            summarize([{**r, "sample_kind": "synthetic"} for r in rows], now=NOW)
        )
        == set()
    )


def test_window_fallback_ack_thresholds_and_pending_do_not_trigger():
    rows = [
        event(
            "jev.handoff_entry",
            sample_kind="production",
            decision_class="fallback",
            attempt_class="Single",
            v5_attempted=True,
            latency_ms=100,
        )
        for _ in range(20)
    ]
    rows += [
        event("handoff.action", action_key=str(i), age=70, sample_kind="production")
        for i in range(20)
    ]
    summary = summarize(rows, now=NOW)
    assert window_breaches(summary) == {"fallback_rate", "ack_failure_rate"}
    assert summary["groups"]["production"]["canary"]["ack"]["matured_failures"] == 20
    pending = summarize(
        [
            {**r, "at_utc": NOW.isoformat()}
            for r in rows
            if r["marker"] == "handoff.action"
        ],
        now=NOW,
    )
    assert not window_breaches(pending)


def test_window_relative_baseline_and_exact_threshold_boundaries():
    rows = [
        event(
            "jev.handoff_entry",
            sample_kind="production",
            decision_class="Single",
            attempt_class="Single",
            v5_attempted=True,
            latency_ms=5000,
        )
        for _ in range(20)
    ]
    assert not window_breaches(summarize(rows, now=NOW))
    rows += [
        event(
            "foundation.routing",
            sample_kind="production",
            cohort="non_canary",
            latency_ms=2000,
        )
        for _ in range(20)
    ]
    assert window_breaches(summarize(rows, now=NOW)) == {
        "latency_all",
        "latency_Single",
    }
