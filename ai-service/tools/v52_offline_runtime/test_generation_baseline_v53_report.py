import pytest

from tools.v52_offline_runtime import generation_baseline_v53_report as report


def test_distribution_uses_only_finite_values_and_actual_median():
    rows = [{"case_id": "a", "score": 1}, {"case_id": "b", "score": 0}, {"case_id": "c", "score": None}]
    result = report.distribution(rows, "score")
    assert result["mean"] == result["p50"] == 0.5
    assert result["finite"] == 2 and result["not_scorable"] == 1
    assert result["lowest5"] == [{"case_id": "b", "score": 0}, {"case_id": "a", "score": 1}]


@pytest.mark.parametrize("value", [float("nan"), float("inf"), -0.1, 1.1])
def test_invalid_metric_rejected(value):
    with pytest.raises(ValueError, match="out_of_range"):
        report.distribution([{"case_id": "a", "score": value}], "score")


def test_unmeasured_historical_cases_are_not_called_failures():
    baseline = [{"case_id": "a", "answerability": "insufficient", "generation_status": "failed"}]
    fixed = [{"case_id": "a", "generation_status": "success"}]
    result = report.cohorts({}, {"a": {"answerability": "insufficient"}}, baseline, fixed, [])
    assert result[0]["historical_group"] == "H0_fixed10"
    assert result[0]["current_status"] == "not_in_current34_not_generated"


def test_packing_gap_not_blindly_attributed_to_generator():
    assert report.attribution(0.5, 0).startswith("Gold seen in rerank")
    assert report.attribution(0, 0).startswith("retrieval_Gold_coverage_zero")
    assert report.attribution(0.5, 0.5).startswith("Gold reached actual contexts")


def test_meta_claim_screening_is_separate_from_truth_judgment():
    assert report.meta_claim_candidate({"statement": "The current world line graph has no edges."})
    assert report.meta_claim_candidate({"statement": "本次输入中的目录事实为空。"})
    assert not report.meta_claim_candidate({"statement": "该歌曲发表于2001年。"})
