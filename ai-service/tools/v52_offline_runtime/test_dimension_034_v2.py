"""v2 preserves every input except the approved system-prompt suffix."""

import asyncio
from copy import deepcopy
import json
from unittest.mock import patch

import httpx
import pytest

from tools.v52_offline_runtime import dimension_034_v2 as v2
from tools.v52_offline_runtime import verify_dimension_034_v2 as verify_v2


@pytest.fixture(scope="module")
def source():
    return v2.batch.load_source()


def test_exact_append_and_isolation_with_automatic_restore(source):
    old_output, old_prompt = v2.batch.OUTPUT, v2.batch.single.EXPANSION_PROMPT
    v2.assert_prompt()
    assert v2.EXPANSION_PROMPT == old_prompt + "\n\n" + v2.APPEND
    assert v2.OUTPUT != old_output and verify_v2.OUTPUT == v2.OUTPUT
    before = v2.v1_hashes()
    with v2.configured():
        assert v2.batch.OUTPUT == v2.OUTPUT
        assert v2.batch.AUTHORIZATION == v2.AUTHORIZATION
        assert v2.batch.single.EXPANSION_PROMPT == v2.EXPANSION_PROMPT
        assert v2.batch.protected_hashes().items() >= before.items()
        for cid, original in source[2].items():
            body = v2.batch.generation_body(original)
            assert body["messages"][0]["content"] == v2.EXPANSION_PROMPT
            restored = deepcopy(body)
            restored["messages"][0]["content"] = original["request"]["messages"][0]["content"]
            assert restored == original["request"], cid
    assert v2.batch.OUTPUT == old_output and v2.batch.single.EXPANSION_PROMPT == old_prompt
    assert v2.v1_hashes() == before


def test_v2_all34_calls_use_v2_prompt_and_reject_repeat(source, tmp_path):
    with v2.configured():
        recorder = v2.batch.Recorder(tmp_path / "calls", source[2])
        for cid, original in source[2].items():
            recorder.scope = cid
            req = httpx.Request("POST", v2.batch.h1.BASE_URL + "/chat/completions", json=v2.batch.generation_body(original))
            asyncio.run(recorder.request(req))
        assert len(recorder.entries) == 34
        with pytest.raises(v2.batch.h1.H1Error, match="duplicate"):
            asyncio.run(recorder.request(req))


@pytest.mark.parametrize("mutation", ["v1_prompt", "user", "temperature", "HyDE", "model"])
def test_v2_recorder_rejects_parameter_drift(source, tmp_path, mutation):
    with v2.configured():
        cid = "rag-v0-002"
        body = v2.batch.generation_body(source[2][cid])
        if mutation in {"v1_prompt", "HyDE"}:
            body["messages"][0]["content"] = v2.BASE_PROMPT if mutation == "v1_prompt" else v2.batch.h1.HYDE_PROMPT
        elif mutation == "user":
            body["messages"][1]["content"] = "changed question"
        else:
            body[mutation] = 1
        recorder = v2.batch.Recorder(tmp_path / "calls", source[2])
        recorder.scope = cid
        with pytest.raises(v2.batch.h1.H1Error):
            asyncio.run(recorder.request(httpx.Request("POST", v2.batch.h1.BASE_URL + "/chat/completions", json=body)))
        assert recorder.entries == []


def approved_fixture(source, directory):
    batch = v2.batch
    batch.base.write_json(directory / "generation-state.json", {
        "status": "generated_pending_semantic_review", "completed_cases": list(source[1])})
    review = {"reviewer": "Codex", "all_passed": True, "cases": {}}
    for cid, (_, old, _, _) in source[1].items():
        path = directory / "new" / (cid + ".json")
        path.parent.mkdir(exist_ok=True)
        batch.base.write_json(path, old)
        review["cases"][cid] = {"passed": True, "new_sha256": batch.base.sha256(path)}
    batch.base.write_json(directory / "semantic-review.json", review)


def test_successful_34_case_gate_preserves_hyde_filter_and_execution(source, tmp_path):
    approved_fixture(source, tmp_path)
    with patch.object(v2.batch, "OUTPUT", tmp_path):
        paired = v2.batch.reviewed_bundles(source[1], source[0][-1])
    assert len(paired) == 34
    for cid, arms in paired.items():
        old, new = arms["old"], arms["new"]
        assert old[1]["hyde"] == new[1]["hyde"] and old[3] == new[3]
        assert {k: v for k, v in old[2].items() if k != "plan_id"} == {k: v for k, v in new[2].items() if k != "plan_id"}
    assert paired["rag-v0-065"]["old"][3]["entity_ids"] == []


@pytest.mark.parametrize("mutation", ["incomplete", "automatic_failed", "semantic_failed", "stale_sha", "HyDE", "constraint"])
def test_any_failed_or_changed_case_blocks_before_models(source, tmp_path, mutation):
    batch = v2.batch
    approved_fixture(source, tmp_path)
    cid = "rag-v0-002"
    if mutation == "automatic_failed":
        path = tmp_path / "generation-state.json"
        value = batch.base.read_json(path)
        value["status"] = "generated_with_constraint_failures"
    elif mutation in {"HyDE", "constraint"}:
        path = tmp_path / "new" / (cid + ".json")
        value = batch.base.read_json(path)
        if mutation == "HyDE":
            value["hyde"] += "changed"
        else:
            value["expansion_raw"] = json.dumps({"queries": ["王力宏音乐风格", "周杰伦音乐风格", "周杰伦代表作品"]}, ensure_ascii=False)
    else:
        path = tmp_path / "semantic-review.json"
        value = batch.base.read_json(path)
        if mutation == "incomplete":
            del value["cases"][cid]
        elif mutation == "semantic_failed":
            value["cases"][cid]["passed"] = False
        else:
            value["cases"][cid]["new_sha256"] = "0" * 64
    batch.base.write_json(path, value)
    if mutation in {"HyDE", "constraint"}:
        review_path = tmp_path / "semantic-review.json"
        review = batch.base.read_json(review_path)
        review["cases"][cid]["new_sha256"] = batch.base.sha256(path)
        batch.base.write_json(review_path, review)
    with patch.object(batch, "OUTPUT", tmp_path), pytest.raises((batch.base.BaselineError, batch.h1.H1Error)):
        batch.reviewed_bundles(source[1], source[0][-1])


def test_v2_repeated_generation_is_blocked_before_api(source, tmp_path):
    with v2.configured(), patch.object(v2.batch, "OUTPUT", tmp_path), patch.object(v2.batch, "load_source", return_value=source):
        with pytest.raises(v2.batch.base.BaselineError, match="generation_already_attempted"):
            asyncio.run(v2.batch.generate())


def test_independent_verifier_only_switches_output_directory():
    original = verify_v2.replay.OUTPUT
    seen = []
    with patch.object(verify_v2.replay, "main", side_effect=lambda: seen.append(verify_v2.replay.OUTPUT)) as main:
        verify_v2.main()
        assert main.call_count == 1
    assert seen == [verify_v2.OUTPUT]
    assert verify_v2.replay.OUTPUT == original
