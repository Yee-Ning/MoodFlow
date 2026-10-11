"""Offline replay must distinguish upstream absence, quota loss and identity dedup."""

from dataclasses import replace
import json
import sys

import pytest

from app.agent.foundation.context_sources import source_item
from evals import agent_pack_quota as quota_eval


def item(identity, text, *, source="user_profile", relevance=0):
    kind = "user_profile" if source == "user_profile" else "long_term_memory"
    value = source_item(identity, kind, text, source=source, intents=["general_chat"])
    return value.model_copy(update={"relevance": relevance})


def case(items=(), important=(), **updates):
    value = quota_eval.QuotaCase("case:one", "test", "general_chat", "user_profile",
        "音乐", tuple(items), frozenset(important), len(items))
    return replace(value, **updates)


def test_low_ranked_important_item_is_reported_as_quota_loss():
    values = [item("important", "重要" * 90), item("distractor", "无关" * 90, relevance=1)]
    row = quota_eval.evaluate(case(values, ["important"]))
    assert row["selected_ids"] == ["distractor"]
    assert row["important_dropped_by_quota"] == ["important"]
    assert row["false_dropped_important"] == ["important"]
    assert row["drop_reasons"] == {"user_profile_budget_exceeded": 1}
    assert row["final_tokens"] <= 240 < row["candidate_tokens"]


def test_identity_dedup_does_not_propose_increasing_token_quota():
    row = quota_eval.evaluate(case([item("a", "相同内容"), item("b", "相同内容")], ["b"]))
    assert row["false_dropped_important"] == ["b"]
    assert row["important_dropped_by_quota"] == []
    summary = quota_eval.summarize([row])[0]
    assert summary["recommendation"]["suggested_review_tokens"] == 240
    assert summary["recommendation"]["direction"] == "maintain"
    assert not summary["recommendation"]["applied"]


def test_candidate_cap_absence_is_not_counted_as_l1_mispruning():
    values = [item(f"p:{index}", f"value:{index}") for index in range(17)]
    row = quota_eval.evaluate(case(values, ["p:16"]))
    assert row["collected_count"] == 16 and row["candidate_cap_dropped"] == 1
    assert row["candidate_cap_missing_important"] == ["p:16"]
    assert row["upstream_or_cap_missing_important"] == ["p:16"]
    assert row["important_available"] == row["important_kept"] == 0
    assert row["false_dropped_important"] == row["important_dropped_by_quota"] == []


@pytest.mark.parametrize("change", [
    {"owner": "unknown"}, {"source": "unknown"}, {"upstream_count": 0},
    {"items": (item("a", "x"), item("a", "y")), "upstream_count": 2},
    {"items": (item("a", "x", source="history_memory"),)},
    {"items": (item("a", "x").model_copy(update={"required": True}),)},
])
def test_invalid_candidate_pools_fail_explicitly(change):
    with pytest.raises(ValueError):
        quota_eval.evaluate(replace(case([item("a", "x")]), **change))


def test_empty_denominators_are_na_and_synthetic_groups_stay_separate():
    empty = quota_eval.evaluate(case())
    nonempty = quota_eval.evaluate(case([item("a", "same"), item("b", "same")]))
    stress = {**nonempty, "suite": "synthetic_projection_pressure"}
    groups = quota_eval.summarize([empty, nonempty, stress])
    assert len(groups) == 2
    real = next(row for row in groups if row["suite"] == "test")
    assert real["nonempty_cases"] == 1 and real["dropped_case_frequency"] == 1
    assert real["available_useful_recall"] is None
    assert quota_eval.summarize([empty])[0]["dropped_case_frequency"] is None


def test_exact_span_matching_decodes_json_and_tracks_text_under_other_identity():
    text = '引号"与<tag>\n正文'
    values = [item("a", text, source="history_memory"), item("b", text, source="history_memory")]
    row = quota_eval.evaluate(case(values, ["b"], source="history_memory", spans={"b": (text,)}))
    assert row["eligible_supporting_spans"] == 1
    assert row["retained_supporting_spans"] == 0
    assert row["retained_supporting_text_any_identity"] == 1
    assert row["important_dropped_by_quota"] == []


def test_excerpted_important_items_are_not_assumed_semantically_complete():
    value = item("a", "摘录").model_copy(update={"metadata": {
        "intents": ["general_chat"], "token_before_projection": 500,
        "projection_reason": "profile_excerpt_truncated"}})
    row = quota_eval.evaluate(case([value], ["a"]))
    assert row["important_kept"] == 1
    assert row["projected_important_items"] == row["selected_projected_important_items"] == 1
    assert row["eligible_supporting_spans"] == 0


def archived_rows():
    candidates = [{"parent_id": f"p:{i}", "doc_id": "d:one", "source_id": "source:one",
        "source_name": "fixture", "source_url": None, "title": "title",
        "primary_child_id": f"c:{i}", "primary_child_content": f"Child文本{i}", "fusion_score": .1}
        for i in range(27)]
    return {
        quota_eval.ASSETS[3]: [{"case_id": "rag:one", "relevant_parent_ids": ["p:23", "p:26"],
            "supporting_spans": [{"parent_id": "p:23", "exact_text": "Child文本23"}]}],
        quota_eval.ASSETS[4]: [{"case_id": "rag:one", "query": "音乐", "retrieval": {"candidates": candidates}}],
        quota_eval.ASSETS[5]: [{"case_id": "rag:one", "inputs": [{"parent_id": f"p:{i}",
            "retriever_rank": i + 1, "reranker_rank_full": 27 - i, "reranker_score": i / 27}
            for i in range(27)]}],
    }


def test_rag_caps_retriever_window_before_reusing_scores(monkeypatch, tmp_path):
    rows = archived_rows()
    monkeypatch.setattr(quota_eval, "read_rows", lambda path: rows[path.relative_to(tmp_path).as_posix()])
    value = next(quota_eval.rag_cases(tmp_path))
    assert len(value.items) == 12
    assert value.items[0].id == "evidence:rag:p:23"
    assert all("p:26" not in entry.id for entry in value.items)
    row = quota_eval.evaluate(value)
    assert "evidence:rag:p:26" in row["upstream_or_cap_missing_important"]
    assert row["candidate_cap_dropped"] == 3
    assert row["candidate_cap_missing_important"] == ["evidence:rag:p:26"]
    assert row["eligible_supporting_spans"] == row["retained_supporting_spans"] == 1


def test_rag_missing_score_does_not_invent_observations(monkeypatch, tmp_path):
    rows = archived_rows()
    rows[quota_eval.ASSETS[5]][0].pop("inputs")
    monkeypatch.setattr(quota_eval, "read_rows", lambda path: rows[path.relative_to(tmp_path).as_posix()])
    with pytest.raises(ValueError, match="cannot invent"):
        list(quota_eval.rag_cases(tmp_path))
    rows[quota_eval.ASSETS[4]][0]["retrieval"] = None
    assert next(quota_eval.rag_cases(tmp_path)).items == ()


def test_cli_refuses_overwriting_existing_results_before_evaluating(monkeypatch, tmp_path):
    output = tmp_path / "quota-eval.json"
    output.write_text("preserved", encoding="utf-8")
    monkeypatch.setattr(sys, "argv", ["quota-eval", "--output", str(tmp_path)])
    monkeypatch.setattr(quota_eval, "run", lambda: pytest.fail("must refuse before evaluating"))
    with pytest.raises(SystemExit) as error:
        quota_eval.main()
    assert error.value.code == 2 and output.read_text(encoding="utf-8") == "preserved"


def test_frozen_assets_replay_is_deterministic_and_policy_is_unchanged():
    first, second = quota_eval.run(), quota_eval.run()
    assert first == second and first["production_policy_unchanged"]
    assert first["case_count"] == 382 and len(first["asset_sha256"]) == 6
    assert len({row["case_id"] for row in first["cases"]}) == 382
    assert all(row["final_tokens"] <= row["token_quota"] for row in first["cases"])
    assert all(not row["recommendation"]["applied"] for row in first["summary"])
    rendered = json.dumps(first, ensure_ascii=False)
    assert "primary_child_content" not in rendered and "candidate_memories" not in rendered
    assert {row["source"] for row in first["summary"]} == {
        "received_envelope", "session_view", "user_profile", "history_memory", "rag"}
