"""Deterministic H2 calculations and stage/anchor failure boundaries."""
import math

import pytest

from evals.rag_h2_parent_diagnosis_v5 import anchor_check, cosine, old_stage, overlap, token_labels


def test_cosine_has_scale_invariance_and_preserves_negative_values():
    assert cosine([2, 0], [7, 0]) == 1
    assert cosine([2, 0], [-7, 0]) == -1
    assert cosine([2, 0], [0, 7]) == 0
    assert cosine([1, 1], [0, 2]) == pytest.approx(1/math.sqrt(2))


@pytest.mark.parametrize("a,b", [([], []), ([0], [0]), ([1], [1, 2]), ([float('nan')], [1])])
def test_invalid_vectors_are_rejected(a, b):
    with pytest.raises(ValueError):
        cosine(a, b)


def test_overlap_counts_unique_native_dimensions_and_does_not_turn_empty_into_zero():
    result = overlap([11, 11, 22, 33], [11, 22, 44])
    assert result["query_terms"] == 3
    assert result["query_coverage"] == pytest.approx(2/3)
    assert result["jaccard"] == .5
    assert result["missing_ids"] == [33]
    assert overlap([], [])['query_coverage'] is None


def test_parent_presence_does_not_imply_gold_child_contains_evidence():
    anchor = {"anchor_id": "a", "claim": "c", "exact_text": "2011投资", "child_ids": ["c1"]}
    children = [{"chunk_id": "c1", "payload": {"content": "2019活动"}}]
    result = anchor_check(anchor, "2011投资", children)
    assert result["parent_contains"]
    assert not result["gold_child_contains"]
    assert result["containing_children"] == []


def test_raw_hit_below_rrf_is_not_called_invisible():
    case = {"leg_results": [{"leg_id": "q2:lexical", "hits": [{"parent_id": "p"}]}],
            **{s: [] for s in ["rrf_top40", "selection_input_rrf", "selection", "rerank_top12"]}}
    assert old_stage(case, "p")["classification"] == "raw_retrieved_below_rrf80"
    assert old_stage(case, "other")["classification"] == "not_in_any_leg"


def test_normalized_hash_labels_use_native_lemma_and_do_not_invent_unmapped_terms():
    from types import SimpleNamespace
    class Native:
        def embed_query(self, s):
            return SimpleNamespace(indices=[7] if s == "音乐" else [8, 9])
    result = token_labels("音乐风格", [7, 123], Native())
    assert result["labels"]["7"] == ["音乐"]
    assert result["unresolved_ids"] == [123]
