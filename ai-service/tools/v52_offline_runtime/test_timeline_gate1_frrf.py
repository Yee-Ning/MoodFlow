"""Unit and frozen-input regression checks; never load or run a model."""

from copy import deepcopy
import math

import pytest

from app.rag.retrieval.fusion import FusedSearchHit
from tools.v52_offline_runtime import timeline_gate1_frrf as experiment


def hit(point, parent, score):
    return FusedSearchHit(point, score, {"parent_id": parent, "chunk_id": "child-" + point}, [])


def test_formula_and_best_child_preserve_payload():
    children = [hit("first", "parent-a", 0.04), hit("second", "parent-a", 0.03), hit("third", "parent-b", 0.045)]
    ranked, audit = experiment.parent_fusion(children, 0.3)
    assert ranked[0].payload is children[0].payload
    assert ranked[0].evidence is children[0].evidence
    assert ranked[0].fusion_score == pytest.approx(0.049)
    assert audit[0]["observed_child_count"] == 2
    assert audit[0]["best_child_id"] == "child-first"
    assert children[0].fusion_score == 0.04


def test_single_child_has_no_bonus():
    ranked, audit = experiment.parent_fusion([hit("only", "parent", 0.03)], 0.3)
    assert ranked[0].fusion_score == 0.03
    assert audit[0]["other_child_score_sum"] == 0


def test_ties_use_best_child_point_id():
    ranked, _ = experiment.parent_fusion([hit("z", "parent-a", 0.04), hit("a", "parent-z", 0.04)], 0.3)
    assert [item.point_id for item in ranked] == ["a", "z"]


def test_best_child_ties_are_deterministic():
    _, audit = experiment.parent_fusion([hit("z", "parent-a", 0.04), hit("a", "parent-a", 0.04)], 0.3)
    assert audit[0]["best_point_id"] == "a"


def test_order_of_children_does_not_change_result():
    children = [hit("a", "parent-a", 0.03), hit("b", "parent-b", 0.02), hit("c", "parent-a", 0.01)]
    assert experiment.parent_fusion(children, 0.3) == experiment.parent_fusion(list(reversed(children)), 0.3)


def test_empty_input():
    assert experiment.parent_fusion([], 0.3) == ([], [])


@pytest.mark.parametrize("alpha", [-0.1, 1.1, math.nan, math.inf])
def test_invalid_alpha_fails(alpha):
    with pytest.raises(ValueError, match="alpha_invalid"):
        experiment.parent_fusion([], alpha)


@pytest.mark.parametrize("score", [-0.1, math.nan, math.inf])
def test_invalid_child_score_fails(score):
    with pytest.raises(ValueError, match="child_score_invalid"):
        experiment.parent_fusion([hit("a", "parent", score)], 0.3)


def test_duplicate_child_fails_instead_of_double_counting():
    with pytest.raises(ValueError, match="duplicate_child"):
        experiment.parent_fusion([hit("a", "parent", 0.02)] * 2, 0.3)


def test_missing_parent_fails():
    with pytest.raises(ValueError, match="missing_parent"):
        experiment.parent_fusion([hit("a", "", 0.02)], 0.3)


def test_discount_is_not_strict_length_normalization():
    short = experiment.parent_fusion([hit("a", "parent", 0.02)], 0.3)[0][0].fusion_score
    long = experiment.parent_fusion([hit(str(index), "parent", 0.02) for index in range(10)], 0.3)[0][0].fusion_score
    assert long == pytest.approx(3.7 * short)


def test_metric_counts_unique_gold_parent_once():
    assert experiment.recall(["gold", "missing"], [{"parent_id": "gold"}] * 40) == 0.5


def test_metric_budget_is_exactly_40_slots():
    assert experiment.recall(["gold"], [{"parent_id": "noise"}] * 40 + [{"parent_id": "gold"}]) == 0


@pytest.mark.parametrize("gold", [[], ["gold", "gold"]])
def test_invalid_gold_denominator_rejected(gold):
    with pytest.raises(ValueError, match="gold_population_invalid"):
        experiment.recall(gold, [])


def test_final_precision_uses_configured_12_not_return_count():
    metrics = experiment.metrics(["gold"], [{"parent_id": "gold"}], [{"parent_id": "gold"}])
    assert metrics == {"recall_at_40": 1, "recall_at_12": 1, "precision_at_12": 1 / 12, "mrr": 1}


def test_duplicate_final_parent_rejected():
    with pytest.raises(ValueError, match="final_population_invalid"):
        experiment.metrics(["gold"], [], [{"parent_id": "gold"}] * 2)


def decision_inputs():
    ids = [*experiment.frozen.CASE_IDS, *("regression-" + str(index) for index in range(29))]
    controls = {cid: {"metrics": dict.fromkeys(experiment.METRICS, 0.5)} for cid in ids}
    results = {cid: {"metrics": dict.fromkeys(experiment.METRICS, 0.5),
                     "lost_gold": {stage: [] for stage in ("rrf40", "selection", "final12")}} for cid in ids}
    for cid in experiment.frozen.CASE_IDS:
        results[cid]["metrics"]["recall_at_40"] = 0.7
    return controls, results


def test_preregistered_success_gate():
    assert experiment.summarize(*decision_inputs())["adopt"] is True


@pytest.mark.parametrize("stage", ["rrf40", "selection", "final12"])
def test_any_new_gold_loss_vetoes_adoption(stage):
    controls, results = decision_inputs()
    results[experiment.frozen.CASE_IDS[0]]["lost_gold"][stage] = ["gold"]
    assert experiment.summarize(controls, results)["adopt"] is False


def test_global_regression_vetoes_adoption():
    controls, results = decision_inputs()
    for cid in results.keys() - set(experiment.frozen.CASE_IDS):
        results[cid]["metrics"]["recall_at_40"] = 0
    assert experiment.summarize(controls, results)["adopt"] is False


def test_frozen_all34_input_and_selector_regression():
    controls, _, payloads, config, rerank, _, _ = experiment.load_source()
    assert len(controls) == 34
    assert (rerank.maximum_candidates, rerank.top_k, rerank.timeout_seconds) == (40, 12, 30)
    for control in controls.values():
        plan, representatives, audit, selected, _ = experiment.prepare_case(control, payloads, config, 0.3)
        assert plan.model_dump(mode="json") == control["plan"]
        assert len(selected) <= 40
        assert len(audit) == len(representatives)
        assert len({hit.payload["parent_id"] for hit in representatives}) == len(representatives)
        assert set(item.parent_id for item in selected) <= {hit.payload["parent_id"] for hit in representatives[:80]}


def test_corrupt_frozen_order_fails():
    controls, _, payloads, _, _, _, _ = experiment.load_source()
    control = deepcopy(controls[experiment.frozen.CASE_IDS[0]])
    control["rrf_top40"].reverse()
    with pytest.raises(ValueError, match="control_order_drift"):
        experiment.reconstruct(control, payloads)
