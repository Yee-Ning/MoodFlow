"""One approved manual retry, immutable history, and stop-on-failure boundaries."""

import asyncio
import json

import httpx
import pytest

from evals import rag_h1_bundle_retry_v5 as retry
from evals import rag_h1_bundle_v5 as original
from evals import rag_source_baseline as baseline


@pytest.fixture
def prepared(tmp_path, monkeypatch):
    monkeypatch.setattr(retry, "OUTPUT", tmp_path / "retry")
    retry.prepare()
    rows, records, calls = retry.load_prepared()
    _, _, _, _, binding = baseline.preflight()
    _, resolver = original.registry_components(binding)
    return rows, records, calls, resolver


def valid_response(row, stage):
    if stage == "hyde":
        return "流行音乐中的节奏与旋律可以体现多样的风格，编曲通常结合不同音色与结构。"
    return json.dumps({"queries": [row["completed_query"] + suffix for suffix in ("相关资料", "背景信息", "主题介绍")]}, ensure_ascii=False)


def test_preparation_reuses_exact_inputs_prompts_and_old_completed_cases(prepared):
    rows, records, _, _ = prepared
    prep = original.read_json(retry.OUTPUT / "pre_call_manifest.json")
    assert len(rows) == 30 and list(records)[:4] == list(retry.SUCCESS_IDS)
    assert prep["original_inputs_sha256"] == original.sha256(original.OUTPUT / "inputs.json")
    assert prep["original_prompts_sha256"] == original.sha256(original.OUTPUT / "prompts.json")
    assert prep["max_retries"] == 0 and prep["temperature"] == 0 and not prep["retrieval_allowed"]
    scopes = retry.allowed_scopes(rows)
    assert len(scopes) == 52 and scopes[:3] == [
        ("rag-v0-005", "expansion"), ("rag-v0-005", "hyde"), ("rag-v0-007", "expansion")]
    assert not any(case_id in {*retry.SUCCESS_IDS, "rag-v0-006"} for case_id, _ in scopes)


def test_original_hash_drift_blocks_before_any_new_call(prepared, monkeypatch):
    real_read = original.read_json

    def read(path):
        value = real_read(path)
        if path == original.OUTPUT / "partial_manifest.json":
            value["artifact_hashes"]["calls/009.json"] = "0" * 64
        return value
    monkeypatch.setattr(original, "read_json", read)
    with pytest.raises(original.H1Error, match="original_artifact_drift"):
        retry.verify_originals()


def test_retry_request_equals_previous_payload_and_allows_one_new_attempt(prepared):
    rows, _, calls, _ = prepared
    row = rows[4]
    body = original.request_body(row, "expansion")
    assert body == calls[-1]["request"]
    recorder = retry.RetryRecorder(retry.OUTPUT / "calls", rows)
    recorder.scope = (row, "expansion")
    request = httpx.Request("POST", original.BASE_URL + "/chat/completions", json=body)
    asyncio.run(recorder.request(request))
    assert len(recorder.entries) == 1
    assert recorder.entries[0]["attempt"] == 2 and recorder.entries[0]["global_call_number"] == 10
    assert recorder.entries[0]["manual_retry"]
    with pytest.raises(original.H1Error, match="out_of_order_or_duplicate"):
        asyncio.run(recorder.request(request))
    recorder.scope = (rows[0], "expansion")
    with pytest.raises(original.H1Error, match="out_of_order_or_duplicate"):
        asyncio.run(recorder.request(httpx.Request("POST", original.BASE_URL + "/chat/completions",
                                                 json=original.request_body(rows[0], "expansion"))))


def test_same_005_violation_stops_after_one_call_and_preserves_both_raw_outputs(prepared):
    rows, records, _, resolver = prepared
    seen = []

    async def call(row, stage):
        seen.append((row["case_id"], stage))
        return records[retry.RETRY_ID]["expansion_raw"], 10
    with pytest.raises(original.H1Error, match="added_registry_entity"):
        asyncio.run(retry.process_cases(rows, records, resolver, call))
    assert seen == [(retry.RETRY_ID, "expansion")]
    retry.write_comparison(records)
    comparison = original.read_json(retry.OUTPUT / "005_attempt_comparison.json")
    assert comparison["same_failure"] and comparison["first"]["raw"] == comparison["second"]["raw"]
    second = original.read_json(retry.OUTPUT / "query_understanding/rag-v0-005.json")
    assert second["attempts"] == {"expansion": 2, "hyde": 0}
    assert not (retry.OUTPUT / "query_understanding/rag-v0-007.json").exists()
    retry.verify_originals()


def test_after_005_passes_next_failure_stops_without_skipping(prepared):
    rows, records, _, resolver = prepared
    seen = []

    async def call(row, stage):
        seen.append((row["case_id"], stage))
        raw = valid_response(row, stage)
        if row["case_id"] == "rag-v0-007":
            raw = '{"queries":["same","same","same"]}'
        return raw, 9 + len(seen)
    with pytest.raises(original.H1Error, match="blank_or_duplicate"):
        asyncio.run(retry.process_cases(rows, records, resolver, call))
    assert seen == [(retry.RETRY_ID, "expansion"), (retry.RETRY_ID, "hyde"), ("rag-v0-007", "expansion")]
    assert original.read_json(retry.OUTPUT / "query_understanding/rag-v0-005.json")["status"].startswith("validated_")
    assert not (retry.OUTPUT / "query_understanding/rag-v0-008.json").exists()
    retry.verify_originals()


def test_if_all_pass_only_52_new_calls_and_freeze_uses_four_original_successes(prepared):
    rows, records, old_calls, resolver = prepared
    new_calls = []

    async def call(row, stage):
        new_calls.append({"case_id": row["case_id"], "stage": stage,
                          "usage": {"prompt_tokens": 1, "completion_tokens": 2, "total_tokens": 3}})
        return valid_response(row, stage), 9 + len(new_calls)
    results = asyncio.run(retry.process_cases(rows, records, resolver, call))
    assert len(new_calls) == 52 and tuple(r["case_id"] for r in results) == original.CASE_IDS
    assert results[:4] == [records[case_id] for case_id in retry.SUCCESS_IDS]
    retry.freeze(results, old_calls, new_calls)
    freeze = original.read_json(retry.OUTPUT / "freeze_manifest.json")
    assert freeze["case_count"] == 30 and not freeze["retrieval_allowed"]
    assert freeze["all_calls_usage"]["all"]["http_calls"] == 61
    assert freeze["frozen_queries_sha256"] == original.sha256(retry.OUTPUT / "frozen_query_understanding.json")
    for case_id in retry.SUCCESS_IDS:
        assert freeze["per_case"][case_id]["path"] == (original.OUTPUT / "query_understanding" / (case_id + ".json")).as_posix()
    retry.verify_originals()


def test_existing_attempt_cannot_be_rerun_or_read_secret_again(prepared, monkeypatch):
    (retry.OUTPUT / "generation_state.json").write_text("{}", encoding="utf-8")

    def forbidden(*_args):
        raise AssertionError("Secret loader must not run")
    monkeypatch.setattr(original, "approved_settings", forbidden)
    with pytest.raises(original.H1Error, match="already_attempted"):
        asyncio.run(retry.generate(False))
