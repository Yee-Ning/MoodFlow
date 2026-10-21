"""Independent replay handles ranking units, filters and censored language data."""

import pytest

from tools.v52_offline_runtime import verify_dimension_034 as verify


def test_fusion_is_per_child_skips_within_leg_duplicates_and_uses_uuid_ties():
    def hit(point, parent):
        return {"point_id": point, "parent_id": parent}
    a, b, c = hit("a", "p"), hit("b", "p"), hit("c", "q")
    legs = [{"error_code": None, "limit": 80, "weight": 1, "hits": [b, a, b]},
            {"error_code": None, "limit": 80, "weight": 1, "hits": [a, c]}]
    ranks = verify.fuse(legs)
    assert [h["point_id"] for h in ranks] == ["a", "b", "c"]
    assert ranks[0]["fusion_score"] == 1/62 + 1/61
    assert ranks[1]["fusion_score"] == 1/61


def test_score_cuts_children_before_parent_dedup_and_precision_denominator_is_12():
    ranks = [{"parent_id": "other"}] * 40 + [{"parent_id": "gold"}]
    rerank = [{"parent_id": "other"}, {"parent_id": "gold"}]
    result = verify.score(["gold"], ranks, rerank)
    assert result["recall_at_40"] == 0 and result["recall_at_12"] == 1
    assert result["precision_at_12"] == 1/12 and result["mrr"] == .5


def test_unknown_year_pass_and_entity_filter_are_independent():
    temporal = {"should": [{"key": "year", "range": {"gte": 2004, "lte": 2004}},
                            {"is_empty": {"key": "year"}}]}
    entity = {"key": "entity_ids", "match": {"any": ["artist:jay_chou"]}}
    assert verify.matches({}, temporal)
    assert not verify.matches({}, {"must": [entity, temporal]})
    assert not verify.matches({"entity_ids": ["artist:jay_chou"], "year": 2008}, {"must": [entity, temporal]})


@pytest.mark.parametrize("old,new,expected", [(None, None, "absent_both"), (None, 50, "newly_observed"),
    (50, None, "no_longer_observed"), (55, 15, "improved"), (15, 55, "worsened"), (55, 55, "unchanged")])
def test_missing_rank_is_censored_never_zero_or_infinite(old, new, expected):
    assert verify.rank_direction(old, new) == expected


def test_language_uses_parent_body_not_incorrect_metadata():
    assert verify.english("He mixes Western instruments with Chinese instruments and melodies.")["is_english"]
    assert not verify.english("周杰伦的音乐融合传统与现代元素。他是中国流行音乐的代表人物。Jay Chou")["is_english"]
    assert not verify.english("Chou")["is_english"]
