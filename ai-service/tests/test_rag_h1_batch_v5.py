"""Batch failure collection and the three-identical-case-code safety valve."""

import asyncio
from copy import deepcopy
import json

import httpx
import pytest

from evals import rag_h1_batch_v5 as run
from evals import rag_h1_bundle_v5 as old
from evals import rag_source_baseline as baseline


@pytest.fixture
def prepared(tmp_path, monkeypatch):
    monkeypatch.setattr(run, "OUTPUT", tmp_path / "batch")
    run.prepare()
    rows, reused, calls = run.load_prepared()
    _, _, _, _, binding = baseline.preflight()
    _, resolver = old.registry_components(binding)
    return rows, reused, calls, resolver


def valid(row, stage):
    if stage == "hyde":
        return "流行音乐的风格通常体现在旋律与节奏的不同组织方式。"
    return json.dumps({"queries": [row["completed_query"] + suffix for suffix in ("相关资料", "背景信息", "主题介绍")]}, ensure_ascii=False)


def test_prepared_scope_reuses_5_passes_008_failure_and_starts_009(prepared):
    rows, reused, calls, _ = prepared
    assert len(rows) == 29 and len(reused) == 6 and len(calls) == 13
    assert sum(r["batch_status"].startswith("passed_") for r in reused) == 5
    assert reused[-1]["case_id"] == "rag-v0-008"
    assert reused[-1]["diagnosis"]["category"] == "model_constraint_violation_suspected"
    assert run.scopes(rows)[0] == ("rag-v0-009", "expansion") and len(run.scopes(rows)) == 46
    prep = old.read_json(run.OUTPUT / "pre_call_manifest.json")
    assert prep["initial_failure_streak"]["case_ids"] == ["rag-v0-008"]
    assert not prep["retrieval_allowed"] and not prep["freeze_allowed"]


def test_single_expansion_and_hyde_failures_continue_without_retry_or_case_drop(prepared):
    rows, reused, _, resolver = prepared
    calls = []

    async def call(row, stage):
        calls.append((row["case_id"], stage))
        raw = valid(row, stage)
        if (row["case_id"], stage) == ("rag-v0-009", "expansion"):
            raw = '{"queries":["same","same","same"]}'
        if (row["case_id"], stage) == ("rag-v0-024", "hyde"):
            raw = ""
        return raw, 13 + len(calls)
    results, summary = asyncio.run(run.process_cases(rows, reused, resolver, call))
    assert summary["status"] == "completed_batch_pending_West_dispositions"
    assert len(results) == 29 and summary["passed_cases"] == 26 and summary["failed_cases"] == 3
    assert summary["failed_case_ids"] == ["rag-v0-008", "rag-v0-009", "rag-v0-024"]
    assert summary["not_attempted_case_ids"] == [] and not summary["freeze_allowed"]
    assert ("rag-v0-009", "hyde") not in calls and ("rag-v0-026", "expansion") in calls
    assert len(calls) == len(set(calls)) == 45
    failed = old.read_json(run.OUTPUT / "failed_cases.json")
    assert failed[1]["expansion_raw"] == '{"queries":["same","same","same"]}'
    assert failed[2]["hyde_raw"] == "" and failed[2]["diagnosis"]["preliminary"]
    assert tuple(r["case_id"] for r in results) == run.CASE_IDS


def test_three_same_codes_including_previous_008_stop_before_fourth(prepared):
    rows, reused, _, resolver = prepared
    calls = []

    async def call(row, stage):
        calls.append((row["case_id"], stage))
        raise old.H1Error("h1_added_registry_entity")
    results, summary = asyncio.run(run.process_cases(rows, reused, resolver, call))
    assert calls == [("rag-v0-009", "expansion"), ("rag-v0-023", "expansion")]
    assert summary["status"] == "safety_valve_paused"
    assert summary["failure_streak"] == {"code": "h1_added_registry_entity",
                                         "case_ids": ["rag-v0-008", "rag-v0-009", "rag-v0-023"]}
    assert len(results) == 8 and summary["not_attempted_case_ids"][0] == "rag-v0-024"
    assert not (run.OUTPUT / "query_understanding/rag-v0-024.json").exists()


def test_different_failure_codes_and_success_reset_the_streak(prepared):
    rows, reused, _, resolver = prepared
    calls = []
    errors = {"rag-v0-009": "h1_year_constraint_drift", "rag-v0-023": "h1_added_registry_entity",
              "rag-v0-024": "h1_added_registry_entity", "rag-v0-031": "h1_added_registry_entity"}

    async def call(row, stage):
        calls.append((row["case_id"], stage))
        if stage == "expansion" and row["case_id"] in errors:
            raise old.H1Error(errors[row["case_id"]])
        return valid(row, stage), 13 + len(calls)
    _, summary = asyncio.run(run.process_cases(rows, reused, resolver, call))
    assert summary["status"] == "completed_batch_pending_West_dispositions"
    assert summary["processed_cases"] == 29 and not summary["failure_streak"]["case_ids"]
    assert ("rag-v0-026", "hyde") in calls


def test_three_provider_http_failures_have_stable_code_and_pause(prepared):
    rows, reused, _, resolver = prepared
    seen = []

    class ProviderError(Exception):
        status_code = 503

    async def call(row, stage):
        seen.append((row["case_id"], stage))
        raise ProviderError("must not be exported")
    _, summary = asyncio.run(run.process_cases(rows, reused, resolver, call))
    assert len(seen) == 3 and summary["status"] == "safety_valve_paused"
    assert summary["failure_streak"]["code"] == "h1_provider_http_503"
    failed = old.read_json(run.OUTPUT / "failed_cases.json")
    assert all(r["diagnosis"]["category"] == "provider_failure" for r in failed[1:])
    assert "must not be exported" not in (run.OUTPUT / "failed_cases.json").read_text(encoding="utf-8")


def test_all_remaining_valid_still_report_008_and_do_not_freeze(prepared):
    rows, reused, _, resolver = prepared
    original_reused = deepcopy(reused)
    calls = []

    async def call(row, stage):
        calls.append((row["case_id"], stage))
        return valid(row, stage), 13 + len(calls)
    results, summary = asyncio.run(run.process_cases(rows, reused, resolver, call))
    assert len(calls) == 46 and summary["passed_cases"] == 28 and summary["failed_cases"] == 1
    assert results[:6] == reused == original_reused
    assert summary["failed_case_ids"] == ["rag-v0-008"]
    assert not (run.OUTPUT / "freeze_manifest.json").exists()
    run.verify_history()


def test_http_guard_prevents_reused_cases_wrong_temperature_and_duplicates(prepared):
    rows, _, _, _ = prepared
    recorder = run.Recorder(run.OUTPUT / "calls", rows)
    for row in rows[:6]:
        recorder.scope = (row, "expansion")
        with pytest.raises(old.H1Error, match="reused_or_excluded"):
            asyncio.run(recorder.request(httpx.Request("POST", old.BASE_URL + "/chat/completions",
                                                       json=old.request_body(row, "expansion"))))
    row = rows[6]
    recorder.scope = (row, "expansion")
    body = old.request_body(row, "expansion")
    bad = {**body, "temperature": 0.01}
    with pytest.raises(old.H1Error, match="outbound_body_or_temperature"):
        asyncio.run(recorder.request(httpx.Request("POST", old.BASE_URL + "/chat/completions", json=bad)))
    request = httpx.Request("POST", old.BASE_URL + "/chat/completions", json=body)
    asyncio.run(recorder.request(request))
    assert recorder.entries[0]["global_call_number"] == 14
    with pytest.raises(old.H1Error, match="second_http_attempt"):
        asyncio.run(recorder.request(request))


def test_prepared_hash_drift_and_repeat_execution_block_before_secret(prepared, monkeypatch):
    old.write_json(run.OUTPUT / "generation_state.json", {})

    def forbidden(*_args):
        raise AssertionError("must not load credentials")
    monkeypatch.setattr(old, "approved_settings", forbidden)
    with pytest.raises(old.H1Error, match="already_attempted"):
        asyncio.run(run.generate(False))
    old.write_json(run.OUTPUT / "inputs.json", [])
    with pytest.raises(old.H1Error, match="pre_call_drift"):
        run.load_prepared()
