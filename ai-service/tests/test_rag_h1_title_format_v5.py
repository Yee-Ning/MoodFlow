"""Title punctuation alone may vary; all other approved checks remain intact."""

import ast
import asyncio
import inspect
import json
from unittest.mock import patch

import httpx
import pytest

from evals import rag_h1_bundle_v5 as old
from evals import rag_h1_title_format_v5 as fixed
from evals import rag_h1_title_resume_v5 as run
from evals import rag_source_baseline as baseline


@pytest.fixture
def prepared(tmp_path, monkeypatch):
    monkeypatch.setattr(run, "OUTPUT", tmp_path / "title_fix")
    run.prepare()
    rows, records, calls, saved = run.load_prepared()
    _, _, _, _, binding = baseline.preflight()
    _, resolver = old.registry_components(binding)
    return rows, records, calls, saved, resolver


def test_only_title_condition_changed_in_validator_ast():
    historical = ast.parse(inspect.getsource(old.validate_text_constraints)).body[0]
    current = ast.parse(inspect.getsource(fixed.validate_text_constraints)).body[0]
    assert historical.name == current.name
    old_title_if = next(node for node in ast.walk(historical) if isinstance(node, ast.If)
                        and 'h1_title_constraint_drift' in ast.unparse(node) and isinstance(node.test, ast.BoolOp))
    new_title_if = next(node for node in ast.walk(current) if isinstance(node, ast.If)
                        and isinstance(node.test, ast.Call) and isinstance(node.test.func, ast.Name)
                        and node.test.func.id == "title_format_drift")
    new_title_if.test = old_title_if.test
    assert ast.dump(current, include_attributes=False) == ast.dump(historical, include_attributes=False)
    old.verify_prompt()


def test_saved_007_replays_without_any_rewrite_and_old_four_unchanged(prepared):
    rows, records, _, saved, resolver = prepared
    original_raw = saved["expansion_raw"]
    with pytest.raises(old.H1Error, match="title_constraint_drift"):
        old.validate_expansion(original_raw, rows[4], resolver)
    queries, _ = run.replay(saved, rows[4], resolver)
    assert queries == json.loads(original_raw)["queries"]
    assert queries[2] == "青花瓷 歌曲 曲风 分析"
    assert saved["expansion_raw"] == original_raw
    check = old.read_json(run.OUTPUT / "007_offline_revalidation.json")
    assert check["real_api_calls"] == 0 and check["old_four_successes_revalidated_unchanged"] == list(run.prior.SUCCESS_IDS)
    for case_id in run.prior.SUCCESS_IDS:
        assert records[case_id]["status"].startswith("validated_")


@pytest.mark.parametrize("query", ["歌曲《青花瓷》曲风分析", "青花瓷 歌曲 曲风 分析"])
def test_same_work_with_or_without_brackets_passes(prepared, query):
    rows, _, _, _, resolver = prepared
    assert fixed.validate_text_constraints(query, rows[4], entity_resolver=resolver)["checks"] == "passed"


def test_same_work_may_gain_brackets_when_original_has_none(prepared):
    rows, _, _, _, resolver = prepared
    row = {**rows[4], "completed_query": "歌曲青花瓷体现了什么风格"}
    assert fixed.validate_text_constraints("《青花瓷》的曲风", row, entity_resolver=resolver)["checks"] == "passed"


@pytest.mark.parametrize("query", [
    "歌曲的曲风分析", "晴天 歌曲 曲风 分析", "歌曲《晴天》体现的风格",
    "青花瓷与《不存在的作品》曲风分析", "青花瓷与晴天的曲风分析",
])
def test_omitted_replaced_and_added_works_still_fail(prepared, query):
    rows, _, _, _, resolver = prepared
    with pytest.raises(old.H1Error, match="title_constraint_drift|added_registry_entity"):
        fixed.validate_text_constraints(query, rows[4], entity_resolver=resolver)


def test_missing_registry_identity_does_not_pass_on_substring_alone(prepared):
    rows, _, _, _, _ = prepared

    class NoEntity:
        def resolve(self, *_a, **_k):
            return []
    with pytest.raises(old.H1Error, match="title_constraint_drift"):
        fixed.validate_text_constraints("青花瓷器音乐", rows[4], entity_resolver=NoEntity())


def test_other_year_person_exclusion_constraints_not_relaxed(prepared):
    rows, _, _, _, resolver = prepared
    with pytest.raises(old.H1Error, match="year_constraint_drift"):
        fixed.validate_text_constraints("青花瓷2004年的曲风", rows[4], entity_resolver=resolver)
    with pytest.raises(old.H1Error, match="added_registry_entity"):
        fixed.validate_text_constraints("周杰伦 青花瓷 曲风", rows[4], entity_resolver=resolver)
    row = {**rows[4], "constraints": {**rows[4]["constraints"], "negative_terms": ["演唱会"]}}
    with pytest.raises(old.H1Error, match="negative_constraint_drift"):
        fixed.validate_text_constraints("青花瓷曲风", row, entity_resolver=resolver)
    # Both 005 failures remain forbidden; no entity_enriched bypass was added.
    original_rows = old.read_json(old.OUTPUT / "inputs.json")
    row005 = next(r for r in original_rows if r["case_id"] == "rag-v0-005")
    for directory in (old.OUTPUT, run.prior.retry.OUTPUT):
        raw = old.read_json(directory / "query_understanding/rag-v0-005.json")["expansion_raw"]
        with patch.object(old, "validate_text_constraints", fixed.validate_text_constraints):
            with pytest.raises(old.H1Error, match="added_registry_entity"):
                old.validate_expansion(raw, row005, resolver)


def test_scope_starts_007_hyde_no_second_007_expansion_or_old_case(prepared):
    rows, _, _, _, _ = prepared
    scopes = run.scopes(rows)
    assert len(scopes) == 49 and scopes[:3] == [
        ("rag-v0-007", "hyde"), ("rag-v0-008", "expansion"), ("rag-v0-008", "hyde")]
    recorder = run.Recorder(run.OUTPUT / "calls", rows)
    recorder.scope = (rows[4], "expansion")
    with pytest.raises(old.H1Error, match="out_of_order_repeat_or_excluded"):
        asyncio.run(recorder.request(httpx.Request("POST", old.BASE_URL + "/chat/completions",
                                                   json=old.request_body(rows[4], "expansion"))))
    recorder.scope = (rows[4], "hyde")
    request = httpx.Request("POST", old.BASE_URL + "/chat/completions", json=old.request_body(rows[4], "hyde"))
    asyncio.run(recorder.request(request))
    assert recorder.entries[0]["global_call_number"] == 12
    with pytest.raises(old.H1Error, match="out_of_order_repeat_or_excluded"):
        asyncio.run(recorder.request(request))


def test_007_hyde_failure_pauses_before_008(prepared):
    rows, records, _, saved, resolver = prepared
    seen = []

    async def call(row, stage):
        seen.append((row["case_id"], stage))
        return "关于2004年的风格", 12
    with pytest.raises(old.H1Error, match="year_constraint_drift"):
        asyncio.run(run.process_cases(rows, records, saved, resolver, call))
    assert seen == [("rag-v0-007", "hyde")]
    assert not (run.OUTPUT / "query_understanding/rag-v0-008.json").exists()
    current = old.read_json(run.OUTPUT / "query_understanding/rag-v0-007.json")
    assert current["expansion_raw"] == saved["expansion_raw"] and "queries" in current


def test_all_mock_passes_reuses_007_expansion_and_freezes_29_pending_review(prepared):
    rows, records, old_calls, saved, resolver = prepared
    calls = []

    async def call(row, stage):
        calls.append({"case_id": row["case_id"], "stage": stage,
                      "usage": {"prompt_tokens": 1, "completion_tokens": 2, "total_tokens": 3}})
        raw = "流行音乐的风格通常体现在旋律与节奏的不同组织方式。" if stage == "hyde" else json.dumps({
            "queries": [row["completed_query"] + suffix for suffix in ("相关资料", "背景信息", "主题介绍")]}, ensure_ascii=False)
        return raw, 11 + len(calls)
    results = asyncio.run(run.process_cases(rows, records, saved, resolver, call))
    assert len(results) == 29 and len(calls) == 49
    assert results[:4] == [records[case_id] for case_id in run.prior.SUCCESS_IDS]
    assert results[4]["expansion_raw"] == saved["expansion_raw"]
    assert results[4]["expansion_call_number"] == 11
    run.freeze(results, old_calls, calls)
    manifest = old.read_json(run.OUTPUT / "freeze_manifest.json")
    assert manifest["case_count"] == 29 and not manifest["retrieval_allowed"]
    assert manifest["all_calls_usage_including_two_excluded_005_failures"]["all"]["http_calls"] == 60
    run.verify_history()


def test_existing_attempt_blocks_before_key_read(prepared, monkeypatch):
    old.write_json(run.OUTPUT / "generation_state.json", {})

    def forbidden(*_args):
        raise AssertionError("credentials must not be read")
    monkeypatch.setattr(old, "approved_settings", forbidden)
    with pytest.raises(old.H1Error, match="already_attempted"):
        asyncio.run(run.generate(False))
