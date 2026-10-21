"""Approved-subset boundaries, immutable inputs, parameters and repeat barriers."""

from copy import deepcopy
from unittest.mock import patch

import pytest

from tools.v52_offline_runtime import dimension_023_v2 as run
from tools.v52_offline_runtime import verify_dimension_023_v2 as verify


@pytest.fixture(scope="module")
def source():
    return run.load_source()


def test_exact_23_successes_and_frozen_198_v1_v2_pins(source):
    inputs, paired, provenance, protected = source
    assert tuple(paired) == run.CASE_IDS == verify.CASE_IDS
    assert len(paired) == 23 and not set(paired) & run.EXCLUDED
    assert len(run.batch.assert_frozen_198()) == 198
    assert len(provenance) == 23 and protected.items() >= run.v2.protected_hashes().items()
    for cid, arms in paired.items():
        old, new = arms["old"], arms["new"]
        assert old[1]["hyde"] == new[1]["hyde"] and old[3] == new[3]
        assert old[1] == run.base.read_json(run.SOURCE / "old" / (cid + ".json"))
        assert new[1] == run.base.read_json(run.SOURCE / "new" / (cid + ".json"))
        op, np = deepcopy(old[2]), deepcopy(new[2])
        op.pop("plan_id"); np.pop("plan_id")
        for index, variant in enumerate(np["queries"][:3]):
            variant["text"] = op["queries"][index]["text"]
        assert np == op
        assert old[2]["candidate_top_k"] == new[2]["candidate_top_k"] == 40
        assert old[2]["final_top_k"] == new[2]["final_top_k"] == 12
    assert paired["rag-v0-065"]["old"][3]["entity_ids"] == []
    assert paired["rag-v0-065"]["new"][3]["year_range"] == [2004, 2004]


@pytest.mark.parametrize("mutation", ["review_false", "stale_sha", "HyDE", "old_queries", "constraint"])
def test_any_source_or_gate_drift_blocks_before_network(monkeypatch, mutation):
    original = run.base.read_json
    cid = "rag-v0-002"

    def altered(path):
        data = deepcopy(original(path))
        if path == run.SOURCE / "semantic-review.json":
            if mutation == "review_false":
                data["cases"][cid]["passed"] = False
            elif mutation == "stale_sha":
                data["cases"][cid]["new_sha256"] = "0" * 64
        elif path == run.SOURCE / "new" / (cid + ".json"):
            if mutation == "HyDE":
                data["hyde"] = "changed"
            elif mutation == "constraint":
                data["expansion_raw"] = '{"queries":["王力宏","周杰伦音乐","周杰伦曲风"]}'
        elif path == run.SOURCE / "old" / (cid + ".json") and mutation == "old_queries":
            data["queries"][0] = "changed old query"
        return data

    monkeypatch.setattr(run.base, "read_json", altered)
    with pytest.raises((run.base.BaselineError, run.batch.h1.H1Error)):
        run.load_source()


def test_excluded_case_cannot_be_swapped_into_subset(monkeypatch):
    monkeypatch.setattr(run, "CASE_IDS", ("rag-v0-055", *run.CASE_IDS[1:]))
    with pytest.raises(run.base.BaselineError, match="approved_population"):
        run.load_source()


@pytest.mark.parametrize("field,value", [("rrf_k", 61), ("overfetch_multiplier", 3), ("per_leg_top_k", 40)])
def test_numeric_drift_is_rejected(field, value):
    original = run.batch.load_source()
    inputs = list(original[0])
    inputs[4] = inputs[4].model_copy(update={"fusion": inputs[4].fusion.model_copy(update={field: value})})
    with patch.object(run.batch, "load_source", return_value=(inputs, *original[1:])):
        with pytest.raises(run.base.BaselineError, match="numeric_parameters"):
            run.load_source()


def test_repeat_is_rejected_before_models_or_qdrant(source, tmp_path, monkeypatch):
    monkeypatch.setattr(run, "OUTPUT", tmp_path)
    monkeypatch.setattr(run, "load_source", lambda: source)
    with patch.object(run.base.baseline.rag_source, "_local_models", side_effect=AssertionError("models must not load")):
        with pytest.raises(run.base.BaselineError, match="already_attempted"):
            run.run(run.base.h2.ReadOnlyGuard())


def test_no_generation_entry_point(monkeypatch):
    monkeypatch.setattr(run.sys, "argv", ["subset", "generate"])
    with pytest.raises(ValueError, match="only_allows"):
        run.main()
