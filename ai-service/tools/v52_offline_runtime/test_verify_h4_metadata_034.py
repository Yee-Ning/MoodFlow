"""Independent H4 matcher and fixed34 macro mean boundaries."""

import pytest

from tools.v52_offline_runtime import verify_h4_metadata_034 as verify


@pytest.mark.parametrize("payload,passed", [({}, True), ({"entity_ids": []}, True),
    ({"entity_ids": ["target"]}, True), ({"entity_ids": ["other"]}, False)])
def test_missing_entity_is_passed_but_known_mismatch_is_rejected(payload, passed):
    condition = {"should": [{"is_empty": {"key": "entity_ids"}}, {"key": "entity_ids", "match": {"any": ["target"]}}]}
    assert verify.eligible(payload, condition) == passed


@pytest.mark.parametrize("value,passed", [(None, True), (2004, True), (2003, False), ("2004", False)])
def test_year_semantics_stay_three_tier(value, passed):
    condition = {"should": [{"is_empty": {"key": "year"}}, {"key": "year", "range": {"gte": 2004, "lte": 2004}}]}
    assert verify.eligible({"year": value}, condition) == passed


def test_source_id_scalar_and_parent_id_list_filters():
    assert verify.eligible({"source_id": "jc-v1-002"}, {"key": "source_id", "match": {"any": ["jc-v1-002"]}})
    assert not verify.eligible({"source_id": "jc-v2-002"}, {"key": "source_id", "match": {"any": ["jc-v1-002"]}})
    assert verify.eligible({"parent_id": "p1"}, {"key": "parent_id", "match": {"any": ["p1", "p2"]}})


def test_macro_mean_must_include_all34_even_identity_cases():
    old = {str(i): {m: .5 for m in verify.METRICS} for i in range(34)}
    new = {c: dict(m) for c, m in old.items()}
    new["0"]["recall_at_40"] = 1
    result = verify.summarize(old, new)
    assert result["n"] == 34
    assert result["delta"]["recall_at_40"] == pytest.approx(.5 / 34)
    assert result["directions"]["recall_at_40"] == {"up": 1, "same": 33}
    assert result["delta"]["mrr"] == 0
    with pytest.raises(AssertionError):
        verify.summarize(old, {k: v for k, v in new.items() if k != "0"})
