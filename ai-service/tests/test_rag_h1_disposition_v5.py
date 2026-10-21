"""Scope, reuse, once-only retries and truthful pending/freeze behavior."""

import asyncio
import json

import httpx
import pytest

from evals import rag_h1_bundle_v5 as old
from evals import rag_h1_disposition_v5 as run


@pytest.fixture
def prepared(tmp_path, monkeypatch):
    monkeypatch.setattr(run, "OUTPUT", tmp_path / "disposition")
    run.prepare()
    rows, records, calls = run.load_prepared()
    return rows, records, calls, run.resolver()


def valid(row, stage):
    if stage == "hyde":
        return "流行音乐的风格可以从旋律、节奏与演唱方式等方面观察。"
    return json.dumps({"queries": [row["completed_query"] + s for s in ("相关资料", "背景介绍", "内容说明")]}, ensure_ascii=False)


def test_prepare_zero_api_scope_saved_response_replay_and_diagnosis(prepared):
    rows, records, calls, _ = prepared
    assert len(rows) == 29 and len(records) == 29 and len(calls) == 53
    assert len(run.scopes()) == 8
    assert run.scopes() == [("rag-v0-009", "hyde"), ("rag-v0-031", "hyde"),
                            ("rag-v0-034", "hyde"), ("rag-v0-045", "expansion"),
                            ("rag-v0-045", "hyde"), ("rag-v0-049", "expansion"),
                            ("rag-v0-049", "hyde"), ("rag-v0-058", "hyde")]
    replay = old.read_json(run.OUTPUT / "offline_revalidation.json")
    assert replay["real_api_calls"] == 0 and replay["old_21_unchanged"] and len(replay["records"]) == 24
    diag = old.read_json(run.OUTPUT / "054_diagnosis.json")
    assert not diag["fix_authorized"]
    assert [p["type_hint_for_rice_field"] for p in diag["actual_subquery_probes"]] == [None, "album", None]
    assert diag["actual_subquery_probes"][1]["registry_reason"] == "registry_candidate_type_mismatch"
    assert all(p["registry_entity_id"] == "song:rice_field" for p in diag["counterfactual_diagnostic_probes_only_never_sent"])
    assert not (run.OUTPUT / "calls").exists()


def test_all_pass_reuses_009_expansion_no_saved_expansion_reissued_054_still_pending(prepared):
    _, records, old_calls, resolver = prepared
    seen = []

    async def call(row, stage):
        seen.append((row["case_id"], stage))
        return valid(row, stage), 53 + len(seen)
    outcomes = asyncio.run(run.process_cases(records, resolver, call))
    assert seen == run.scopes()
    by_id = {o["case_id"]: o for o in outcomes}
    assert by_id["rag-v0-009"]["record"]["expansion_raw"] == records["rag-v0-009"]["expansion_raw"]
    for case_id in run.OFFLINE_IDS:
        assert by_id[case_id]["record"]["expansion_raw"] == records[case_id]["expansion_raw"]
    assert by_id["rag-v0-009"]["record"]["attempts"] == {"expansion": 1, "hyde": 2}
    summary = run.snapshot(outcomes, old_calls, [])
    assert summary["candidate_count"] == 27 and summary["pending_case_ids"] == ["rag-v0-054"]
    assert not summary["population_finalized"] and not summary["retrieval_allowed"]
    assert [e["case_id"] for e in summary["excluded"]] == ["rag-v0-005", "rag-v0-008"]
    assert not (run.OUTPUT / "freeze_manifest.json").exists()
    assert records["rag-v0-009"]["attempts"] == {"expansion": 1, "hyde": 1}


def test_same_code_retry_failures_exclude_only_approved_stages_and_no_followup_hyde(prepared):
    _, records, _, resolver = prepared
    seen = []

    async def call(row, stage):
        seen.append((row["case_id"], stage))
        if run.RETRY_STAGES.get(row["case_id"]) == stage:
            raise old.H1Error(row["error_code"])
        return valid(row, stage), 53 + len(seen)
    outcomes = asyncio.run(run.process_cases(records, resolver, call))
    assert len(seen) == len(set(seen)) == 6
    assert ("rag-v0-045", "hyde") not in seen and ("rag-v0-049", "hyde") not in seen
    by_id = {o["case_id"]: o for o in outcomes}
    for case_id in run.RETRY_STAGES:
        assert by_id[case_id]["disposition"] == "excluded"
    assert all(by_id[c]["disposition"] == "passed" for c in run.OFFLINE_IDS)


def test_different_retry_code_and_new_hyde_failure_are_pending_not_auto_excluded(prepared):
    _, records, _, resolver = prepared
    seen = []

    async def call(row, stage):
        seen.append((row["case_id"], stage))
        if row["case_id"] == "rag-v0-009":
            raise old.H1Error("h1_added_registry_entity")
        if row["case_id"] == "rag-v0-031":
            return "2004年的流行音乐", 53 + len(seen)
        return valid(row, stage), 53 + len(seen)
    outcomes = asyncio.run(run.process_cases(records, resolver, call))
    by_id = {o["case_id"]: o for o in outcomes}
    assert by_id["rag-v0-009"]["disposition"] == by_id["rag-v0-031"]["disposition"] == "pending_West"
    assert by_id["rag-v0-031"]["record"]["hyde_raw"] == "2004年的流行音乐"
    assert len(seen) == 8


def test_consecutive_three_same_code_safety_valve_retained(prepared):
    _, records, _, resolver = prepared
    seen = []

    async def call(row, stage):
        seen.append((row["case_id"], stage))
        raise old.H1Error("h1_provider_http_503")
    outcomes = asyncio.run(run.process_cases(records, resolver, call))
    assert seen == run.scopes()[:3]
    assert old.read_json(run.OUTPUT / "safety_valve.json")["case_ids"] == ["rag-v0-009", "rag-v0-031", "rag-v0-034"]
    assert next(o for o in outcomes if o["case_id"] == "rag-v0-045")["disposition"] == "pending_execution"


def test_http_guard_only_allows_fixed_stages_temperature_and_once_each(prepared):
    _, records, _, _ = prepared
    recorder = run.Recorder(run.OUTPUT / "calls")
    for case_id, stage in (("rag-v0-054", "expansion"), ("rag-v0-009", "expansion"),
                           ("rag-v0-031", "expansion"), ("rag-v0-008", "expansion")):
        row = records[case_id]
        recorder.scope = (row, stage)
        with pytest.raises(old.H1Error, match="unapproved_scope_or_budget"):
            asyncio.run(recorder.request(httpx.Request("POST", old.BASE_URL + "/chat/completions", json=old.request_body(row, stage))))
    row = records["rag-v0-009"]
    recorder.scope = (row, "hyde")
    body = old.request_body(row, "hyde")
    with pytest.raises(old.H1Error, match="outbound_body_or_temperature"):
        asyncio.run(recorder.request(httpx.Request("POST", old.BASE_URL + "/chat/completions", json={**body, "temperature": 0.01})))
    request = httpx.Request("POST", old.BASE_URL + "/chat/completions", json=body)
    asyncio.run(recorder.request(request))
    assert recorder.entries[0]["attempt"] == 2 and recorder.entries[0]["global_call_number"] == 54
    with pytest.raises(old.H1Error, match="second_http_attempt"):
        asyncio.run(recorder.request(request))


def test_existing_run_and_hash_drift_block_before_credentials(prepared, monkeypatch):
    old.write_json(run.OUTPUT / "generation_state.json", {})

    def forbidden(*_a):
        raise AssertionError("must not read credentials")
    monkeypatch.setattr(old, "approved_settings", forbidden)
    with pytest.raises(old.H1Error, match="already_attempted_no_resume"):
        asyncio.run(run.generate(False))
    old.write_json(run.OUTPUT / "inputs.json", [])
    with pytest.raises(old.H1Error, match="pre_call_drift"):
        run.load_prepared()


def test_explicit_post_diagnosis_054_exclusion_can_finalize_population(prepared):
    _, records, old_calls, resolver = prepared

    async def call(row, stage):
        return valid(row, stage), 0
    outcomes = asyncio.run(run.process_cases(records, resolver, call))
    item = next(o for o in outcomes if o["case_id"] == "rag-v0-054")
    assert item["disposition"] == "pending_West"
    item.update(disposition="excluded", reason="West explicitly excluded 054 after reviewing diagnosis")
    old.write_json(run.OUTPUT / "dispositions.json", outcomes)
    summary = run.snapshot(outcomes, old_calls, [])
    assert summary["population_finalized"] and summary["candidate_count"] == 27
    assert summary["pending_case_ids"] == [] and not summary["retrieval_allowed"]
    assert (run.OUTPUT / "freeze_manifest.json").is_file()
    assert {r["case_id"] for r in summary["excluded"]} == {"rag-v0-005", "rag-v0-008", "rag-v0-054"}
