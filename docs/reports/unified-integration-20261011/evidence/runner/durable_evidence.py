"""Persist each test report on its worker before xdist forwards it."""

from collections import Counter
import json
import os
from pathlib import Path
import time

clock_now = time.monotonic

phase = os.environ["COLDCHAIN_TEST_PHASE"]
directory = Path(os.environ["COLDCHAIN_TEST_EVIDENCE_DIR"])
worker = "controller"
counts = Counter()
failures = []
started = clock_now()


def append_record(path, record):
    with path.open("a", encoding="utf-8") as stream:
        stream.write(json.dumps(record, ensure_ascii=False) + "\n")
        stream.flush()
        os.fsync(stream.fileno())


def snapshot(complete=False, exit_code=None):
    append_record(directory / f"{phase}.{worker}.progress.jsonl", {
        "worker": worker, "complete": complete, "exit_code": exit_code,
        "elapsed_seconds": round(clock_now() - started, 2),
        "counts": dict(counts), "failed_nodeids": failures,
    })


def pytest_configure(config):
    global worker
    worker = getattr(config, "workerinput", {}).get("workerid", "controller")
    snapshot()


def persist(report):
    when = getattr(report, "when", "collect")
    if when == "call" or report.outcome != "passed":
        counts[report.outcome] += 1
    if report.failed:
        failures.append(report.nodeid)
        record = {"worker": worker, "nodeid": report.nodeid,
                  "when": when, "outcome": report.outcome,
                  "stack": report.longreprtext, "sections": report.sections}
        with (directory / f"{phase}.{worker}.failures.jsonl").open("a", encoding="utf-8") as stream:
            stream.write(json.dumps(record, ensure_ascii=False) + "\n")
            stream.flush()
            os.fsync(stream.fileno())
    snapshot()


def pytest_runtest_logreport(report):
    persist(report)


def pytest_collectreport(report):
    if report.failed:
        persist(report)


def pytest_sessionfinish(session, exitstatus):
    snapshot(complete=True, exit_code=int(exitstatus))


def pytest_internalerror(excrepr, excinfo):
    append_record(directory / f"{phase}.{worker}.internal_errors.jsonl", {
        "worker": worker, "stack": str(excrepr), "complete": False,
    })
