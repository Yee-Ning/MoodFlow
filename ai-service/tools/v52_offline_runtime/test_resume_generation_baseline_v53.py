import pytest

from tools.v52_offline_runtime.resume_generation_baseline_v53 import completed_cases


def test_completed_paid_case_is_not_repeated(tmp_path):
    (tmp_path / "a.json").write_text("{}", encoding="utf-8")
    gold = [{"case_id": "a"}, {"case_id": "b"}]
    assert completed_cases(gold, tmp_path, [{"case_id": "a"}, {"case_id": "a"}]) == {"a"}


def test_interrupted_paid_case_requires_offline_recovery(tmp_path):
    with pytest.raises(ValueError, match="incomplete_paid_case"):
        completed_cases([{"case_id": "a"}], tmp_path, [{"case_id": "a"}])


def test_only_fixed_population_is_counted(tmp_path):
    (tmp_path / "unknown.json").write_text("{}", encoding="utf-8")
    assert completed_cases([{"case_id": "a"}], tmp_path, []) == set()
