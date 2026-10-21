"""The dimension experiment varies only the system prompt and 002 queries."""

from copy import deepcopy
import json

import httpx
import pytest

from tools.v52_offline_runtime import dimension_002 as run


@pytest.fixture(scope="module")
def source():
    return run.load_source()


def test_prompt_is_exact_append_and_request_only_changes_system(source):
    old = source[2]
    assert run.EXPANSION_PROMPT == run.h1.EXPANSION_PROMPT + "\n\n" + run.STRATEGY
    previous = run.h1.request_body(old, "expansion")
    request = run.generation_body(old)
    restored = deepcopy(request)
    restored["messages"][0]["content"] = previous["messages"][0]["content"]
    assert restored == previous
    assert request["temperature"] == 0 and request["model"] == "deepseek-chat"
    assert json.loads(request["messages"][1]["content"]) == {"question": old["completed_query"]}
    assert run.h1.EXPANSION_PROMPT == previous["messages"][0]["content"]


def test_provider_recorder_allows_one_exact_002_call_only(source, tmp_path):
    import asyncio

    recorder = run.Recorder(tmp_path / "calls")
    recorder.scope = (source[2], "expansion")
    request = httpx.Request("POST", run.h1.BASE_URL + "/chat/completions",
                            json=run.generation_body(source[2]))
    asyncio.run(recorder.request(request))
    assert len(recorder.entries) == 1
    assert "authorization" not in str(recorder.entries).lower()
    with pytest.raises(run.h1.H1Error, match="only_one_002"):
        asyncio.run(recorder.request(request))


@pytest.mark.parametrize("change", ["case", "stage", "endpoint", "body"])
def test_provider_rejects_other_scopes(source, tmp_path, change):
    import asyncio

    recorder = run.Recorder(tmp_path / "calls")
    old = deepcopy(source[2])
    stage, endpoint = "expansion", run.h1.BASE_URL + "/chat/completions"
    body = run.generation_body(old)
    if change == "case":
        old["case_id"] = "rag-v0-001"
    elif change == "stage":
        stage = "hyde"
    elif change == "endpoint":
        endpoint = "https://example.com/v1/chat/completions"
    else:
        body["temperature"] = 1
    recorder.scope = (old, stage)
    with pytest.raises(run.h1.H1Error):
        asyncio.run(recorder.request(httpx.Request("POST", endpoint, json=body)))
    assert not recorder.entries


def test_unchanged_constraints_accept_facets_reject_added_answer_entities(source):
    old, binding = source[2], source[0][-1]
    _, resolver = run.base.registry_components(binding)
    queries = ["周杰伦音乐风格中乐器使用有什么特点",
               "周杰伦音乐风格如何融合不同曲风",
               "周杰伦哪些代表作品体现了他的音乐风格特点"]
    raw = json.dumps({"queries": queries}, ensure_ascii=False)
    values, checks = run.base.validate_stage(raw, old, "expansion", resolver)
    assert values == queries and all(c["checks"] == "passed" for c in checks)
    for extra in ("2004年", "《东风破》", "王力宏"):
        invalid = json.dumps({"queries": [queries[0] + extra, *queries[1:]]}, ensure_ascii=False)
        with pytest.raises(run.h1.H1Error):
            run.base.validate_stage(invalid, old, "expansion", resolver)


def test_review_gate_preserves_hyde_filter_and_all_execution_fields(source, tmp_path, monkeypatch):
    monkeypatch.setattr(run, "OUTPUT", tmp_path)
    _, _, old, original_plan, condition, _ = source
    new = deepcopy(old)
    new["queries"] = ["周杰伦音乐风格的乐器使用", "周杰伦音乐风格的曲风融合", "周杰伦音乐风格的代表作品"]
    run.base.write_json(tmp_path / "new-expansion.json", new)
    run.base.write_json(tmp_path / "expansion-review.json", {
        "reviewer": "Codex", "passed": True, "case_id": run.CASE_ID,
        "dimensions": ["乐器", "融合", "作品"],
        "new_expansion_sha256": run.base.sha256(tmp_path / "new-expansion.json"),
    })
    query, plan, actual_filter = run.reviewed_bundle(original_plan, old, condition)
    restored = deepcopy(plan)
    restored["queries"] = original_plan["queries"]
    restored["plan_id"] = original_plan["plan_id"]
    assert restored == original_plan
    assert actual_filter == condition and query["hyde"] == old["hyde"]
    new["hyde"] += "tampered"
    run.base.write_json(tmp_path / "new-expansion.json", new)
    with pytest.raises(run.base.BaselineError, match="missing_or_stale"):
        run.reviewed_bundle(original_plan, old, condition)


def test_generation_refuses_existing_output_before_any_call(source, tmp_path, monkeypatch):
    import asyncio

    monkeypatch.setattr(run, "load_source", lambda: source)
    monkeypatch.setattr(run, "OUTPUT", tmp_path)
    with pytest.raises(run.base.BaselineError, match="already_attempted"):
        asyncio.run(run.generate())
