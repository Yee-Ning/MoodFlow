"""Matched 29-case control, immutable history, and fail-closed continuation."""

import asyncio
import json
import math

import httpx
import pytest

from evals import rag_h1_bundle_29_v5 as run
from evals import rag_h1_bundle_v5 as original
from evals import rag_source_baseline as baseline


@pytest.fixture
def prepared(tmp_path, monkeypatch):
    monkeypatch.setattr(run, "OUTPUT", tmp_path / "run29")
    run.prepare()
    rows, records, calls = run.load_prepared()
    _, _, _, _, binding = baseline.preflight()
    _, resolver = original.registry_components(binding)
    return rows, records, calls, resolver


def valid_response(row, stage):
    if stage == "hyde":
        return "流行音乐中的节奏与旋律可以体现多样的风格，编曲通常结合不同音色与结构。"
    return json.dumps({"queries": [row["completed_query"] + suffix for suffix in ("相关资料", "背景信息", "主题介绍")]}, ensure_ascii=False)


def test_fixed_29_only_excludes_005_and_recomputes_matching_historical_means(prepared):
    rows, _, _, _ = prepared
    assert len(rows) == 29 and tuple(r["case_id"] for r in rows) == run.CASE_IDS
    assert set(original.CASE_IDS) - set(run.CASE_IDS) == {"rag-v0-005"}
    control = original.read_json(run.OUTPUT / "baseline29.json")["metrics"]
    saved = [r for r in baseline.read_jsonl(baseline.OUTPUT / "cases.jsonl") if r["case_id"] in run.CASE_IDS]
    assert control["all_scored"]["case_count"] == 29
    assert control["by_answerability"]["full"]["case_count"] == 18
    assert control["by_answerability"]["partial"]["case_count"] == 11
    for metric in baseline.METRICS:
        assert control["all_scored"][metric] == math.fsum(r["metrics"][metric] for r in saved) / 29
    old = original.read_json(baseline.OUTPUT / "metrics.json")
    for group in ("timeline", "artist_overview"):
        assert control["by_question_group"][group] == old["by_question_group"][group]
    assert control["all_scored"]["recall_at_40"] != old["all_scored"]["recall_at_40"]
    exclusion = original.read_json(run.OUTPUT / "exclusion.json")
    assert len(exclusion["failed_records"]) == 2 and "not selected by retrieval scores" in exclusion["reason"]


def test_scoped_request_guard_excludes_005_old_successes_and_duplicates(prepared):
    rows, _, _, _ = prepared
    assert len(run.scopes(rows)) == 50
    assert run.scopes(rows)[0] == ("rag-v0-007", "expansion")
    recorder = run.Bundle29Recorder(run.OUTPUT / "calls", rows)
    original_rows = original.read_json(original.OUTPUT / "inputs.json")
    for case_id in ("rag-v0-001", "rag-v0-005"):
        row = next(r for r in original_rows if r["case_id"] == case_id)
        recorder.scope = (row, "expansion")
        with pytest.raises(original.H1Error, match="out_of_order_excluded_or_duplicate"):
            asyncio.run(recorder.request(httpx.Request("POST", original.BASE_URL + "/chat/completions",
                                                      json=original.request_body(row, "expansion"))))
    row = rows[4]
    recorder.scope = (row, "expansion")
    request = httpx.Request("POST", original.BASE_URL + "/chat/completions", json=original.request_body(row, "expansion"))
    asyncio.run(recorder.request(request))
    assert recorder.entries[0]["global_call_number"] == 11
    assert recorder.entries[0]["selected_population"] == 29 and recorder.entries[0]["attempt"] == 1
    with pytest.raises(original.H1Error, match="out_of_order_excluded_or_duplicate"):
        asyncio.run(recorder.request(request))


def test_next_failure_stops_before_hyde_without_reducing_fixed_29(prepared):
    rows, records, _, resolver = prepared
    seen = []

    async def call(row, stage):
        seen.append((row["case_id"], stage))
        return '{"queries":["same","same","same"]}', 11
    with pytest.raises(original.H1Error, match="blank_or_duplicate"):
        asyncio.run(run.process_cases(rows, records, resolver, call))
    assert seen == [("rag-v0-007", "expansion")]
    failed = original.read_json(run.OUTPUT / "query_understanding/rag-v0-007.json")
    assert failed["attempts"] == {"expansion": 1, "hyde": 0}
    assert not (run.OUTPUT / "query_understanding/rag-v0-008.json").exists()
    assert len(original.read_json(run.OUTPUT / "inputs.json")) == 29
    run.verify_history()


def test_all_valid_cases_reuse_four_old_records_and_freeze_29_before_review(prepared):
    rows, records, old_calls, resolver = prepared
    calls = []

    async def call(row, stage):
        calls.append({"case_id": row["case_id"], "stage": stage,
                      "usage": {"prompt_tokens": 1, "completion_tokens": 2, "total_tokens": 3}})
        return valid_response(row, stage), 10 + len(calls)
    results = asyncio.run(run.process_cases(rows, records, resolver, call))
    assert len(calls) == 50 and len(results) == 29
    assert results[:4] == [records[case_id] for case_id in run.SUCCESS_IDS]
    assert not any(c["case_id"] == "rag-v0-005" for c in calls)
    run.freeze(results, old_calls, calls)
    manifest = original.read_json(run.OUTPUT / "freeze_manifest.json")
    assert manifest["case_count"] == 29 and not manifest["retrieval_allowed"]
    assert manifest["all_calls_usage_including_two_excluded_005_failures"]["all"]["http_calls"] == 60
    assert "rag-v0-005" not in manifest["per_case"]
    assert manifest["frozen_queries_sha256"] == original.sha256(run.OUTPUT / "frozen_query_understanding.json")
    for case_id in run.SUCCESS_IDS:
        assert manifest["per_case"][case_id]["path"] == (original.OUTPUT / "query_understanding" / (case_id + ".json")).as_posix()
    run.verify_history()


def test_changed_fixed_inputs_fail_before_new_calls(prepared):
    rows, _, _, _ = prepared
    original.write_json(run.OUTPUT / "inputs.json", rows[:-1])
    with pytest.raises(original.H1Error, match="pre_call_drift"):
        run.load_prepared()


def test_existing_run_blocks_before_reading_key(prepared, monkeypatch):
    original.write_json(run.OUTPUT / "generation_state.json", {})

    def forbidden(*_args):
        raise AssertionError("must not load credentials")
    monkeypatch.setattr(original, "approved_settings", forbidden)
    with pytest.raises(original.H1Error, match="already_attempted"):
        asyncio.run(run.generate(False))
