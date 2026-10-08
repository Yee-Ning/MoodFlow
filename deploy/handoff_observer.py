"""Private, allowlisted Docker log receiver and read-only canary/ACK summaries."""

from __future__ import annotations

import argparse
import json
import math
import os
import re
import subprocess
import sys
import time
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

PREFIX = "app.agent.handoff.observability:"
MARKERS = (
    "jev.handoff_entry",
    "jev.handoff_entry_local",
    "jev.handoff_guard",
    "handoff.trace",
    "handoff.request",
    "handoff.action",
    "handoff.ack",
    "handoff.fallback",
    "handoff.alert",
    "foundation.routing",
)
FIELDS = {
    "graph_mode",
    "cohort",
    "sample_kind",
    "request_key",
    "thread_key",
    "event_id",
    "at_utc",
    "logging_error_count",
    "phase",
    "entrypoint",
    "status",
    "latency_ms",
    "entry_wall_latency_ms",
    "decision_class",
    "attempt_class",
    "v5_attempted",
    "router_source",
    "fallback_reason",
    "error_type",
    "shadow",
    "api_calls",
    "candidate_task_count",
    "candidate_complete",
    "candidate_needs_input",
    "plan_agreement",
    "source",
    "tasks",
    "needs_clarification",
    "scores",
    "score",
    "score_bucket",
    "threshold",
    "injection_threshold",
    "would_allow",
    "prompt_version",
    "duration_ms",
    "tokens",
    "source_agent",
    "target_agent",
    "code",
    "reason_code",
    "guard_score",
    "run_id",
    "action_key",
    "name",
    "alert_code",
    "fallback_event_id",
}
TEXT = re.compile(r"^[a-zA-Z0-9_:./,\[\]()\-]+$")
HEX = re.compile(r"^[0-9a-f]{32}$")
BUCKETS = ("[0,.7)", "[.7,.8)", "[.8,.9)", "[.9,1]")


def utc_now():
    return datetime.now(timezone.utc)


def parse(line):
    if not re.match(
        r"^(?:INFO|WARNING|ERROR):app\.agent\.handoff\.observability:", line
    ):
        return None
    value = line.split(PREFIX, 1)[1]
    marker, separator, body = value.partition(" ")
    if marker not in MARKERS or not separator:
        return None
    try:
        raw = json.loads(body)
        assert isinstance(raw, dict) and HEX.fullmatch(raw["event_id"])
        datetime.fromisoformat(raw["at_utc"])
        assert raw["cohort"] in {"canary", "non_canary", "explicit", "unknown"}
        assert raw["sample_kind"] in {
            "production",
            "acceptance",
            "synthetic",
            "unknown",
        }
        record = {"marker": marker}
        for key in FIELDS.intersection(raw):
            item = raw[key]
            if item is None or isinstance(item, bool):
                record[key] = item
            elif isinstance(item, (int, float)):
                assert math.isfinite(item)
                record[key] = item
            elif key == "at_utc":
                record[key] = item
            elif key == "scores" and isinstance(item, dict):
                record[key] = {
                    name: number
                    for name, number in item.items()
                    if name in {"task_fidelity", "authority_override"}
                    and type(number) in {int, float}
                    and math.isfinite(number)
                    and 0 <= number <= 1
                }
            elif isinstance(item, str) and len(item) <= 160 and TEXT.fullmatch(item):
                record[key] = item
        return record
    except (ValueError, TypeError, KeyError, AssertionError):
        return {"marker": "observer.invalid_event"}


def read_records(path):
    if not path.exists():
        return []
    return [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line
    ]


def append(path, record):
    data = (json.dumps(record, separators=(",", ":")) + "\n").encode()
    descriptor = os.open(path, os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o600)
    try:
        while data:
            data = data[os.write(descriptor, data) :]
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


class Receiver:
    def __init__(self, directory):
        self.directory = Path(directory)
        self.directory.mkdir(mode=0o700, parents=True, exist_ok=True)
        self.directory.chmod(0o700)
        self.events_path = self.directory / "events.jsonl"
        self.alerts_path = self.directory / "alerts.jsonl"
        self.ids = {row.get("event_id") for row in read_records(self.events_path)}
        self.delivered = {
            row["alert_event_id"] for row in read_records(self.alerts_path)
        }

    def ingest(self, line):
        record = parse(line)
        if record is None:
            return None
        if (
            record.get("event_id") not in self.ids
            or record["marker"] == "observer.invalid_event"
        ):
            append(self.events_path, record)
            self.ids.add(record.get("event_id"))
        if (
            record["marker"] == "handoff.alert"
            and record["event_id"] not in self.delivered
        ):
            delivery = {
                "marker": "handoff.alert_delivered",
                "alert_event_id": record["event_id"],
                "fallback_event_id": record.get("fallback_event_id"),
                "alert_code": record.get("alert_code"),
                "sample_kind": record.get("sample_kind"),
                "cohort": record.get("cohort"),
                "delivered_at_utc": utc_now().isoformat(),
                "receiver": "private_local_fsync_file",
            }
            append(self.alerts_path, delivery)
            self.delivered.add(record["event_id"])
            return delivery
        return record


def percentile(values, fraction):
    values = sorted(values)
    if not values:
        return None
    index = (len(values) - 1) * fraction
    low = int(index)
    high = min(low + 1, len(values) - 1)
    return round(values[low] + (values[high] - values[low]) * (index - low), 2)


def bucket(score):
    return BUCKETS[0 if score < 0.7 else 1 if score < 0.8 else 2 if score < 0.9 else 3]


def ratio(numerator, denominator):
    return numerator / denominator if denominator else None


def cohort_summary(records, *, now=None):
    now = now or utc_now()
    start = {
        r["request_key"]: r
        for r in records
        if r["marker"] == "handoff.request" and r.get("phase") == "started"
    }
    ends = {
        r["request_key"]: r
        for r in records
        if r["marker"] == "handoff.request" and r.get("phase") == "finished"
    }
    statuses = Counter(r.get("status") for r in ends.values())
    attempts = [
        r
        for r in records
        if r["marker"] == "jev.handoff_entry" and r.get("v5_attempted")
    ]
    classes = Counter(r.get("decision_class") for r in attempts)
    latencies = [r["latency_ms"] for r in attempts if "latency_ms" in r]
    guards = [r for r in records if r["marker"] == "jev.handoff_guard"]
    histograms = {}
    for score_name in ("display", "task_fidelity", "authority_override"):
        values = [
            r.get("score")
            if score_name == "display"
            else r.get("scores", {}).get(score_name)
            for r in guards
        ]
        valid = [v for v in values if type(v) in {int, float} and 0 <= v <= 1]
        histogram = Counter(bucket(v) for v in valid)
        histograms[score_name] = {
            "count": len(valid),
            "missing": len(values) - len(valid),
            "bins": {key: histogram[key] for key in BUCKETS},
        }
    actions = {}
    for row in records:
        if row["marker"] == "handoff.action":
            actions.setdefault(row["action_key"], row)
    receipts = {}
    for row in records:
        if row["marker"] == "handoff.ack":
            receipts.setdefault(row["action_key"], row)
    ack_counts = Counter()
    for key, action in actions.items():
        created = datetime.fromisoformat(action["at_utc"])
        ack = receipts.get(key)
        if (
            ack
            and (datetime.fromisoformat(ack["at_utc"]) - created).total_seconds() <= 60
        ):
            ack_counts[ack["status"]] += 1
        elif (now - created).total_seconds() >= 60:
            ack_counts["timed_out"] += 1
            ack_counts["late_receipts"] += int(ack is not None)
        else:
            ack_counts["pending"] += 1
        ack_counts["matured_actions"] += int((now - created).total_seconds() >= 60)
        if (now - created).total_seconds() >= 60:
            timely_applied = (
                ack
                and ack["status"] == "applied"
                and (datetime.fromisoformat(ack["at_utc"]) - created).total_seconds()
                <= 60
            )
            ack_counts["matured_failures"] += int(not timely_applied)
    eligible = sum(ack_counts[key] for key in ("applied", "rejected", "timed_out"))
    baseline = [r for r in records if r["marker"] == "foundation.routing"]
    baseline_latencies = [r["latency_ms"] for r in baseline if "latency_ms" in r]
    return {
        "request_starts": len(start),
        "request_finishes": len(ends),
        "missing_terminals": len(start.keys() - ends.keys()),
        "statuses": dict(statuses),
        "request_error_rate": ratio(
            statuses["failed"] + statuses["cancelled"], len(ends)
        ),
        "clarification_rate": ratio(statuses["needs_input"], len(ends)),
        "v5_attempts": len(attempts),
        "v5_classes": dict(classes),
        "v5_success_rate": ratio(classes["Single"] + classes["Multi"], len(attempts)),
        "v5_error_rate": ratio(classes["error"], len(attempts)),
        "fallback_rate": ratio(classes["fallback"], len(attempts)),
        "v5_p50_ms": percentile(latencies, 0.5),
        "v5_p95_ms": percentile(latencies, 0.95),
        "v5_missing_latency": len(attempts) - len(latencies),
        "v5_latency_by_class": {
            name: {
                "count": len(
                    group := [
                        r["latency_ms"]
                        for r in attempts
                        if r.get("attempt_class") == name and "latency_ms" in r
                    ]
                ),
                "p50_ms": percentile(group, 0.5),
                "p95_ms": percentile(group, 0.95),
            }
            for name in ("Single", "Multi")
        },
        "local_rules": sum(
            r["marker"] == "jev.handoff_entry_local"
            and r.get("decision_class") == "rule"
            for r in records
        ),
        "rule_hit_rate": ratio(
            sum(
                r["marker"] == "jev.handoff_entry_local"
                and r.get("decision_class") == "rule"
                for r in records
            ),
            len(start),
        ),
        "guard_histograms": histograms,
        "trace_events": sum(r["marker"] == "handoff.trace" for r in records),
        "transfer_events": sum(
            r["marker"] == "handoff.trace"
            and r.get("code") == "accepted"
            and r.get("source_agent") != r.get("target_agent")
            for r in records
        ),
        "ack": {
            "unique_actions": len(actions),
            **{
                key: ack_counts[key]
                for key in (
                    "applied",
                    "rejected",
                    "timed_out",
                    "pending",
                    "late_receipts",
                    "matured_actions",
                    "matured_failures",
                )
            },
            "eligible_actions": eligible,
            "success_rate": ratio(ack_counts["applied"], eligible),
        },
        "foundation_routing_count": len(baseline),
        "foundation_routing_p50_ms": percentile(baseline_latencies, 0.5),
        "foundation_routing_p95_ms": percentile(baseline_latencies, 0.95),
    }


def window_breaches(summary):
    """Preparation report section 5; only real traffic and sufficient denominators."""
    canary = summary["groups"]["production"]["canary"]
    baseline = summary["groups"]["production"]["non_canary"]
    reasons = set()
    if canary["v5_attempts"] >= 20:
        error = canary["v5_error_rate"]
        if error is not None and (
            error > 0.05
            or (
                baseline["request_finishes"] >= 20
                and baseline["request_error_rate"] is not None
                and error > baseline["request_error_rate"] + 0.03
            )
        ):
            reasons.add("error_rate")
        fallback = canary["fallback_rate"]
        if fallback is not None and (
            fallback > 0.10
            or (
                baseline["v5_attempts"] >= 20
                and baseline["fallback_rate"] is not None
                and fallback > baseline["fallback_rate"] + 0.05
            )
        ):
            reasons.add("fallback_rate")
        baseline_p95 = (
            baseline["foundation_routing_p95_ms"]
            if baseline["foundation_routing_count"] >= 20
            else None
        )
        latency_groups = {
            "all": {"count": canary["v5_attempts"], "p95_ms": canary["v5_p95_ms"]},
            **canary["v5_latency_by_class"],
        }
        for name, group in latency_groups.items():
            value = group["p95_ms"]
            if (
                group["count"] >= 20
                and value is not None
                and (
                    value > 5000
                    or (baseline_p95 is not None and value > 2 * baseline_p95)
                )
            ):
                reasons.add("latency_" + name)
    ack = canary["ack"]
    if (
        ack["matured_actions"] >= 20
        and ack["matured_failures"] / ack["matured_actions"] > 0.05
    ):
        reasons.add("ack_failure_rate")
    return reasons


def consecutive_breaches(previous, current):
    """Call for adjacent, completed, nonoverlapping five-minute windows."""
    return sorted(window_breaches(previous).intersection(window_breaches(current)))


def summarize(records, alerts=(), *, since=None, now=None):
    now = now or utc_now()
    selected = [
        r
        for r in records
        if "at_utc" in r
        and (since is None or datetime.fromisoformat(r["at_utc"]) >= since)
    ]
    # ACK endpoints have no generation context; join on the validated original action.
    all_receipts = [r for r in selected if r["marker"] == "handoff.ack"]
    groups = {}
    for source in ("production", "acceptance", "synthetic"):
        groups[source] = {}
        for cohort in ("canary", "non_canary", "explicit"):
            subset = [
                r
                for r in selected
                if r.get("sample_kind") == source and r.get("cohort") == cohort
            ]
            action_ids = {
                r["action_key"] for r in subset if r["marker"] == "handoff.action"
            }
            subset += [
                r
                for r in all_receipts
                if r["action_key"] in action_ids and r not in subset
            ]
            groups[source][cohort] = cohort_summary(subset, now=now)
    return {
        "at_utc": now.isoformat(),
        "since_utc": since.isoformat() if since else None,
        "event_counts": dict(Counter(r["marker"] for r in selected)),
        "groups": groups,
        "invalid_events": sum(r["marker"] == "observer.invalid_event" for r in records),
        "logging_errors": max(
            (r.get("logging_error_count", 0) for r in selected), default=0
        ),
        "alerts_generated": sum(r["marker"] == "handoff.alert" for r in selected),
        "alerts_delivered": sum(
            any(r.get("event_id") == a["alert_event_id"] for r in selected)
            for a in alerts
        ),
        "unscoped_events": sum(
            r.get("cohort") == "unknown" or r.get("sample_kind") == "unknown"
            for r in selected
            if r["marker"] != "handoff.ack"
        ),
    }


def follow(receiver):
    compose = [
        "docker",
        "compose",
        "--env-file",
        ".env.production",
        "-f",
        "docker-compose.production.yml",
    ]
    while True:
        cid = subprocess.check_output(
            compose + ["ps", "-q", "ai-service"], cwd="/opt/moodflow", text=True
        ).strip()
        if not cid:
            time.sleep(2)
            continue
        process = subprocess.Popen(
            ["docker", "logs", "--follow", "--since", "5m", cid],
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
        )
        try:
            for line in process.stdout:
                receiver.ingest(line)
        finally:
            process.terminate()
            process.wait(timeout=10)
        time.sleep(1)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--directory", type=Path, required=True)
    parser.add_argument("--follow", action="store_true")
    parser.add_argument("--ingest", action="store_true")
    parser.add_argument("--since")
    args = parser.parse_args()
    os.umask(0o077)
    receiver = Receiver(args.directory)
    if args.follow:
        follow(receiver)
    elif args.ingest:
        for line in sys.stdin:
            delivered = receiver.ingest(line)
            if delivered and delivered["marker"] == "handoff.alert_delivered":
                print(json.dumps(delivered))
    else:
        result = summarize(
            read_records(receiver.events_path),
            read_records(receiver.alerts_path),
            since=datetime.fromisoformat(args.since) if args.since else None,
        )
        print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
