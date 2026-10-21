"""Single-variable scope, original ranking service, cutoff and decision gates."""

import asyncio

from pydantic import ValidationError
import pytest

from app.rag.reranking.config import load_rerank_config
from app.rag.reranking.models import RerankInput
from tests.reranking_helpers import candidate_pool, plan
from tests.test_reranking_service import StaticScorer
from tools.v52_offline_runtime import rerank_034 as run
from tools.v52_offline_runtime import verify_rerank_034 as verify


@pytest.mark.parametrize("count,top_k", [(40, 16), (60, 12), (10, 16)])
def test_original_service_with_isolated_expanded_contracts(count, top_k):
    config = run.ExperimentConfig.model_validate({**load_rerank_config().model_dump(), "maximum_candidates": max(40, count), "top_k": top_k})
    scorer = run.RecordingScorer(StaticScorer([index / count for index in range(count)]))
    with run.experiment_contracts():
        result = asyncio.run(run.service.QwenCandidateReranker(config, scorer=scorer).rerank(plan(), candidate_pool(count)))
    assert result.status.value == "success" and not result.fallback_used
    assert len(result.candidates) == min(top_k, count) and scorer.calls == 1
    assert result.candidates[0].retrieval_rank == count
    assert result.candidates[0].candidate.parent_id == "parent-" + str(count)
    assert [item.candidate_index for item in scorer.inputs] == list(range(count))
    assert run.input_builder.RerankInput is RerankInput


def test_production_bounds_remain_intact():
    original = load_rerank_config()
    with pytest.raises(ValidationError):
        type(original).model_validate({**original.model_dump(), "top_k": 16})
    assert original.top_k == 12 and original.maximum_candidates == 40 and original.timeout_seconds == 30


@pytest.mark.parametrize("scores", [[.4999, .5, .9], [.1, .2], [.5, .5]])
def test_cutoff_before_topk_equals_filtering_sorted_topk(scores):
    ranked = sorted(scores, reverse=True)
    assert [score for score in ranked if score >= .5][:12] == [score for score in ranked[:12] if score >= .5]


def test_top16_precision_denominator_and_fixed12_comparator():
    ranking = [{"parent_id": "noise-" + str(index)} for index in range(15)] + [{"parent_id": "gold"}]
    actual = run.metrics(["gold"], [], ranking, 16)
    assert actual["recall_at_k"] == 1 and actual["precision_at_k"] == 1 / 16
    assert actual["mrr"] == 1 / 16 and actual["fixed_recall_at_12"] == 0
    assert actual == verify.score(["gold"], [], ranking, 16)


@pytest.mark.parametrize("ranking", [[], [{"parent_id": "gold"}]])
def test_short_and_empty_output_keeps_configured_denominator(ranking):
    actual = run.metrics(["gold"], [], ranking, 12)
    assert actual["precision_at_k"] == len(ranking) / 12
    assert actual["returned_precision"] == len(ranking)
    assert actual == verify.score(["gold"], [], ranking, 12)


@pytest.mark.parametrize("ranking", [[{"parent_id": "gold"}] * 2, [{"parent_id": str(index)} for index in range(13)]])
def test_duplicate_or_oversized_output_is_rejected(ranking):
    with pytest.raises(ValueError, match="population_invalid"):
        run.metrics(["gold"], [], ranking, 12)


def test_first40_child_cut_precedes_parent_deduplication():
    ranks = [{"parent_id": "noise"}] * 40 + [{"parent_id": "gold"}]
    actual = run.metrics(["gold"], ranks, [{"parent_id": "gold"}], 12)
    assert actual["recall_at_40"] == 0 and actual["recall_at_k"] == 1


@pytest.mark.parametrize("lost,killed,adopt", [([], [], True), (["gold"], [], False), ([], ["gold"], False)])
def test_gold_veto_overrides_passing_metric(lost, killed, adopt):
    result = verify.decision({"mrr": .5, "recall_at_k": .5}, {"mrr": .6, "recall_at_k": .5}, lost, killed)
    assert result["metric_threshold_met"] and result["adopt"] == adopt


def test_improvement_below_preregistered_threshold_is_rejected():
    assert not verify.decision({"mrr": .5, "recall_at_k": .5}, {"mrr": .51, "recall_at_k": .51}, [], [])["adopt"]


def test_all_three_specs_differ_from_control_in_one_logical_parameter():
    control = {"candidate_limit": 40, "top_k": 12, "threshold": None}
    assert list(run.ROUNDS) == ["top16", "pool60", "cutoff05"]
    for spec in run.ROUNDS.values():
        assert sum(spec[key] != control[key] for key in control) == 1


def test_repeat_round_stops_before_source_or_inference(tmp_path, monkeypatch):
    monkeypatch.setattr(run, "OUTPUT", tmp_path)
    (tmp_path / "top16").mkdir()
    with pytest.raises(run.base.BaselineError, match="already_attempted"):
        run.run_round("top16", None, None, None, None)


def test_frozen_real_selection_replays_and_pool_expands_only_within_top80():
    controls, _, projection, config, _ = run.load_source()
    for control in controls.values():
        before = run.prepare_pool(control, projection, config, 40)[1]
        after = run.prepare_pool(control, projection, config, 60)[1]
        assert len(after) >= len(before)
        assert {item.parent_id for item in before} <= {item.parent_id for item in after}
        assert len(after) <= 60
    assert len(controls) == 34
