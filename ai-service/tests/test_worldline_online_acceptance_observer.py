"""Unit tests for passive evidence collection; these are not E2E fixtures."""

import asyncio
from dataclasses import dataclass
import json
import logging
import sys

from scripts.worldline_online_acceptance_server import AcceptanceObserver, EvidenceLogHandler, TARGETS, observation
from scripts.worldline_online_checkpoint_evidence import canonical_hash


def test_observation_omits_credentials_and_unrelated_parser_tree():
    assert observation({"api_key": "hidden", "Authorization": "hidden", "headers": {"cookie": "hidden"},
                        "metadata": {"parser_metadata": {"nodes": []}, "claim_id": "native-id"}}) == {
        "metadata": {"claim_id": "native-id"},
    }


def test_dataclass_expansion_result_retains_actual_relation_arrays():
    @dataclass
    class IndependentObservation:
        relations: list
        trace: dict

    assert observation(IndependentObservation([], {"requested": True, "candidate_count": 0})) == {
        "relations": [], "trace": {"requested": True, "candidate_count": 0},
    }


def test_checkpoint_fingerprint_ignores_dictionary_order_but_detects_state_changes():
    assert canonical_hash({"revision": 2, "state": ["original"]}) == canonical_hash({"state": ["original"], "revision": 2})
    assert canonical_hash({"revision": 2}) != canonical_hash({"revision": 3})


def test_profile_records_completed_return_without_changing_result(tmp_path):
    namespace = {"__name__": "observer_unit"}
    exec("def actual_function(value):\n return {'value': value}\n", namespace)
    TARGETS["observer_unit"] = {"actual_function"}
    observer = AcceptanceObserver(tmp_path / "trace.jsonl")
    try:
        sys.setprofile(observer.profile)
        returned = namespace["actual_function"]("actual-value")
    finally:
        sys.setprofile(None)
        observer.close()
        TARGETS.pop("observer_unit")
    assert returned == {"value": "actual-value"}
    events = [json.loads(row) for row in (tmp_path / "trace.jsonl").read_text(encoding="utf-8").splitlines()]
    assert events[1]["payload"]["return"] == returned
    assert events[-1]["payload"]["observation_errors"] == 0


def test_non_target_calls_do_not_log_return_values(tmp_path):
    observer = AcceptanceObserver(tmp_path / "trace.jsonl")
    try:
        sys.setprofile(observer.profile)
        observation({"claim_id": "test-only"})
    finally:
        sys.setprofile(None)
        observer.close()
    assert len((tmp_path / "trace.jsonl").read_text(encoding="utf-8").splitlines()) == 2


def test_async_suspension_is_not_mistaken_for_completed_return(tmp_path):
    namespace = {"__name__": "observer_async_unit", "asyncio": asyncio}
    exec("async def actual_async():\n await asyncio.sleep(0.01)\n return {'finished': True}\n", namespace)
    TARGETS["observer_async_unit"] = {"actual_async"}
    observer = AcceptanceObserver(tmp_path / "trace.jsonl")
    try:
        sys.setprofile(observer.profile)
        assert asyncio.run(namespace["actual_async"]()) == {"finished": True}
    finally:
        sys.setprofile(None)
        observer.close()
        TARGETS.pop("observer_async_unit")
    events = [json.loads(row) for row in (tmp_path / "trace.jsonl").read_text(encoding="utf-8").splitlines()]
    assert len(events) == 3
    assert events[1]["payload"]["return"] == {"finished": True}


def test_log_collection_failure_cannot_fail_application_request():
    class BrokenObserver:
        error_count = 0

        def write(self, *args):
            raise OSError("test-only evidence destination unavailable")

    observer = BrokenObserver()
    EvidenceLogHandler(observer).emit(logging.LogRecord("app.test", logging.INFO, "", 1, "event", (), None))
    assert observer.error_count == 1
