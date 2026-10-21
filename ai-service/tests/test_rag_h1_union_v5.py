"""Global coverage consistency and exactly one remaining, first HyDE call."""

import asyncio
import json
from unittest.mock import patch

import httpx
import pytest

from evals import rag_h1_bundle_v5 as old
from evals import rag_h1_union_v5 as run


@pytest.fixture(scope="module")
def saved():
    outcomes, calls = run.history()
    return outcomes, calls, run.prior.resolver()


@pytest.fixture
def prepared(tmp_path, monkeypatch, saved):
    monkeypatch.setattr(run, "OUTPUT", tmp_path / "global_coverage")
    run.prepare()
    return *run.load_prepared(), saved[2]


def test_saved_049_retry_now_passes_union_without_rewriting_any_query(saved):
    outcomes, _, resolver = saved
    row = next(o["record"] for o in outcomes if o["case_id"] == "rag-v0-049")
    queries, checks = run.validate_expansion(row["expansion_raw"], row, resolver)
    assert queries == json.loads(row["expansion_raw"])["queries"]
    assert all(c["coverage"] == "set" for c in checks)
    original = next(r for r in old.read_json(run.prior.batch.OUTPUT / "batch_results.json") if r["case_id"] == row["case_id"])
    with pytest.raises(old.H1Error, match="title_constraint_drift"):
        run.validate_expansion(original["expansion_raw"], row, resolver)  # Album still omitted.


def test_same_set_algorithm_applies_to_every_input_no_case_specific_exceptions(saved):
    outcomes, _, resolver = saved
    row = next(o["record"] for o in outcomes if o["case_id"] == "rag-v0-031")
    for case_id in old.CASE_IDS:
        assert run.validate_expansion(row["expansion_raw"], {**row, "case_id": case_id}, resolver)[0] == row["queries"]


def test_global_union_does_not_relax_missing_album_added_people_years_or_document_cues(saved):
    outcomes, _, resolver = saved
    rows = {o["case_id"]: o["record"] for o in outcomes}
    row = rows["rag-v0-049"]
    queries = json.loads(row["expansion_raw"])["queries"]
    for replacement, code in (([q.replace("《范特西》", "") for q in queries], "title_constraint_drift"),
                              ([queries[0] + " 周杰伦", *queries[1:]], "added_registry_entity"),
                              ([queries[0] + " 2004年", *queries[1:]], "year_constraint_drift")):
        with pytest.raises(old.H1Error, match=code):
            run.validate_expansion(json.dumps({"queries": replacement}, ensure_ascii=False), row, resolver)
    with pytest.raises(old.H1Error, match="document_constraint_drift"):
        run.validate_expansion(rows["rag-v0-045"]["expansion_raw"], rows["rag-v0-045"], resolver)


def test_prepare_replays_24_and_049_freezes_new_input_without_any_calls(prepared):
    outcomes, calls, _ = prepared
    assert len(calls) == 59 and len(outcomes) == 29
    replay = old.read_json(run.OUTPUT / "offline_revalidation.json")
    assert replay["api_calls"] == 0 and len(replay["records"]) == 25
    assert sum(r["hyde_needed"] for r in replay["records"]) == 1
    prep = old.read_json(run.OUTPUT / "pre_call_manifest.json")
    assert prep["shared_batch_http_spent"] == 6 and prep["max_new_http"] == 1
    assert prep["allowed_scope"] == ["rag-v0-049", "hyde"] and not prep["retrieval_allowed"]


def test_first_hyde_pass_recovers_049_keeps_054_excluded_and_freezes_25(prepared):
    outcomes, calls, resolver = prepared
    raw = "流行歌曲的创作奖项通常分别关注旋律写作、歌词表达和编曲处理等方面。"
    updated = run.apply_response(outcomes, raw, resolver)
    row = next(o["record"] for o in updated if o["case_id"] == "rag-v0-049")
    assert row["attempts"] == {"expansion": 2, "hyde": 1} and row["hyde_raw"] == raw
    assert next(o for o in updated if o["case_id"] == "rag-v0-054")["disposition"] == "excluded"
    old.write_json(run.OUTPUT / "dispositions.json", updated)
    with patch.object(run.prior, "OUTPUT", run.OUTPUT):
        snapshot = run.prior.snapshot(updated, calls, [])
    assert snapshot["candidate_count"] == 25 and snapshot["population_finalized"]
    assert not snapshot["pending_case_ids"] and not snapshot["retrieval_allowed"]
    assert {e["case_id"] for e in snapshot["excluded"]} == {"rag-v0-005", "rag-v0-008", "rag-v0-009", "rag-v0-045", "rag-v0-054"}


def test_first_hyde_failure_stays_pending_no_automatic_retry_or_exclusion(saved):
    outcomes, _, resolver = saved
    result = run.apply_response(outcomes, "2004年的音乐奖项", resolver)
    item = next(o for o in result if o["case_id"] == "rag-v0-049")
    assert item["disposition"] == "pending_West" and item["error_code"] == "h1_year_constraint_drift"
    assert item["record"]["hyde_raw"] == "2004年的音乐奖项"


def test_http_guard_forbids_reexpansion_wrong_temperature_and_second_request(prepared):
    outcomes, _, _ = prepared
    row = next(o["record"] for o in outcomes if o["case_id"] == "rag-v0-049")
    recorder = run.Recorder(run.OUTPUT / "calls")
    recorder.scope = (row, "expansion")
    with pytest.raises(old.H1Error, match="only_049_first_hyde_once"):
        asyncio.run(recorder.request(httpx.Request("POST", old.BASE_URL + "/chat/completions", json=old.request_body(row, "expansion"))))
    recorder.scope = (row, "hyde")
    body = old.request_body(row, "hyde")
    with pytest.raises(old.H1Error, match="outbound_body_or_temperature"):
        asyncio.run(recorder.request(httpx.Request("POST", old.BASE_URL + "/chat/completions", json={**body, "temperature": 0.01})))
    request = httpx.Request("POST", old.BASE_URL + "/chat/completions", json=body)
    asyncio.run(recorder.request(request))
    assert recorder.entries[0]["global_call_number"] == 60 and recorder.entries[0]["attempt"] == 1
    with pytest.raises(old.H1Error, match="only_049_first_hyde_once"):
        asyncio.run(recorder.request(request))


def test_existing_attempt_blocks_before_secret_read(prepared, monkeypatch):
    old.write_json(run.OUTPUT / "generation_state.json", {})

    def forbidden(*_a):
        raise AssertionError("credentials must not be read")
    monkeypatch.setattr(old, "approved_settings", forbidden)
    with pytest.raises(old.H1Error, match="already_attempted_no_resume"):
        asyncio.run(run.generate(False))
