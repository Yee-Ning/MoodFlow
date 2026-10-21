"""Independent subset population and per-case macro/pattern calculations."""

import pytest

from tools.v52_offline_runtime import verify_dimension_023_v2 as verify


def test_exact_46_order_and_no_excluded_case():
    rows = [{"case_id": cid, "arm": arm} for cid in verify.CASE_IDS for arm in ("old", "new")]
    verify.validate_population(rows)
    for bad in (rows[:-1], [*rows, rows[0]], [{"case_id": "rag-v0-055", "arm": "old"}, *rows[1:]], list(reversed(rows))):
        with pytest.raises(AssertionError):
            verify.validate_population(bad)


def test_macro_mean_is_case_weighted_and_directions_are_per_metric():
    rows = []
    for old, new in ((0, 1), (1, .5)):
        rows.append({"old": {m: old for m in verify.METRICS}, "new": {m: new for m in verify.METRICS},
                     "delta": {m: new - old for m in verify.METRICS}})
    result = verify.aggregate(rows)
    assert result["n"] == 2
    for metric in verify.METRICS:
        assert result["old"][metric] == .5 and result["new"][metric] == .75 and result["delta"][metric] == .25
        assert result["directions"][metric] == {"up": 1, "down": 1}


@pytest.mark.parametrize("delta,direction", [(0, "same"), (1/30, "up"), (-1/30, "down")])
def test_metric_direction_is_not_rounded_away(delta, direction):
    assert verify.direction(delta) == direction


def test_english_primary_cohort_was_fixed_before_scores():
    assert len(verify.ENGLISH_GOLD) == 3
    assert "parent-22eb9a7aa7d6e6e1138330ec" in verify.ENGLISH_GOLD
    assert "parent-f0c08762a7823c85fdcf2a93" in verify.ENGLISH_GOLD
