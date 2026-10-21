"""Old-leg accounting must distinguish fusion rank from leg rank."""
import pytest

from evals.rag_h2_parent_report_v5 import fused_positions


def test_fusion_position_uses_all_old_legs_and_ignores_duplicate_point_in_one_leg():
    hit_a = {"point_id": "a", "rank": 1, "child_id": "ca"}
    hit_b = {"point_id": "b", "rank": 2, "child_id": "cb"}
    case = {"leg_results": [
        {"weight": 1, "hits": [hit_a, hit_b, {**hit_a, "rank": 3}]},
        {"weight": 1, "hits": [{**hit_b, "rank": 1}]},
    ]}
    output = fused_positions(case)
    assert [r["point_id"] for r in output] == ["b", "a"]
    assert output[0]["fusion_score"] == pytest.approx(1/62+1/61)
    assert output[1]["fusion_score"] == pytest.approx(1/61)
    assert output[0]["fusion_rank"] == 1


def test_tied_fusion_scores_use_point_id_and_empty_input_is_empty():
    assert fused_positions({"leg_results": []}) == []
    case = {"leg_results": [
        {"weight": 1, "hits": [{"point_id": "b", "rank": 1}]},
        {"weight": 1, "hits": [{"point_id": "a", "rank": 1}]},
    ]}
    assert [r["point_id"] for r in fused_positions(case)] == ["a", "b"]
