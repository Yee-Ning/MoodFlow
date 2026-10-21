"""H4 Parent semantics, missing pass, Gold veto and strict single-variable scope."""

from copy import deepcopy
from types import SimpleNamespace

import pytest

from tools.v52_offline_runtime import h4_metadata_034 as h4


@pytest.mark.parametrize("field", ["source_id", "source_type", "entity_ids"])
@pytest.mark.parametrize("value,passed", [(None, True), ([], True), ("", True), ("yes", True), (["yes"], True), ("no", False), (["no"], False)])
def test_three_tiers(field, value, passed):
    assert h4.signal_pass({field: value}, {"field": field, "targets": ["yes"]}) == passed


def test_scalar_match_any_does_not_treat_source_id_as_characters():
    condition = {"key": "source_id", "match": {"any": ["jc-v1-002"]}}
    assert h4.matches({"source_id": "jc-v1-002"}, condition)
    assert not h4.matches({"source_id": "jc-v2-002"}, condition)
    assert h4.matches({"source_id": ["jc-v1-002"]}, condition)


def test_sibling_entities_union_and_missing_parent_are_different():
    p = SimpleNamespace(children={
        "c1": {"parent_id": "p1", "chunk_id": "c1", "entity_ids": ["song:x"]},
        "c2": {"parent_id": "p1", "chunk_id": "c2", "entity_ids": []},
        "c3": {"parent_id": "p2", "chunk_id": "c3", "entity_ids": []},
    })
    metadata = h4.parent_metadata(p)
    rule = {"field": "entity_ids", "targets": ["song:x"]}
    assert h4.signal_pass(metadata["p1"], rule)
    assert h4.signal_pass(metadata["p2"], rule)
    assert metadata["p1"]["entity_ids"] == ["song:x"]
    p.children["c2"]["source_id"] = "conflicting-source"
    with pytest.raises(h4.base.BaselineError, match="inconsistent_sibling"):
        h4.parent_metadata(p)


def test_cues_do_not_consult_gold_and_skip_broad_artist():
    parents = {"p": {"source_id": "s", "source_url": "https://www.sonymusic.com.tw/a"}}
    assert h4.select_signal("索尼怎样描述", [], "source_id", parents)["targets"] == ["s"]
    assert h4.select_signal("官方YouTube播放量", [], "source_id", parents)["targets"] == []
    assert h4.select_signal("周杰伦访谈", ["artist:jay_chou"], "entity", parents)["targets"] == []
    assert h4.select_signal("范特西的双截棍获奖", ["song:nunchucks", "album:fantasy"], "entity", parents)["targets"] == ["song:nunchucks", "album:fantasy"]
    assert h4.select_signal("研究摘要", [], "doc_type", parents)["targets"] == ["academic_paper"]
    assert h4.select_signal("索尼介绍曲风", [], "doc_type", parents)["targets"] == []


def test_new_filter_is_only_an_and_parent_predicate_and_cannot_rescue_gold():
    projection = SimpleNamespace(children={
        "c1": {"parent_id": "p1", "entity_ids": ["artist:jay_chou"]},
        "c2": {"parent_id": "p2", "entity_ids": ["artist:jay_chou"]},
    })
    parents = {"p1": {"entity_ids": ["artist:jay_chou", "person:vincent_fang"]},
               "p2": {"entity_ids": ["artist:jay_chou"]}}
    case = {"case_id": "test", "question": "周杰伦与方文山", "relevant_parent_ids": ["p2"]}
    query = {"constraints": {"entity_ids": ["artist:jay_chou", "person:vincent_fang"]}}
    plan = {"plan_id": "old", "queries": ["frozen"], "metadata_filter": {"frozen": True}, "candidate_top_k": 40}
    condition = {"effective_qdrant_filter": {"must": [{"key": "entity_ids", "match": {"any": ["artist:jay_chou"]}}]}}
    snapshots = deepcopy((case, query, plan, condition, projection, parents))
    new_plan, new_condition, check = h4.build_variant(case, query, plan, condition, "entity", projection, parents)
    assert check["newly_excluded_gold"] == ["p2"]
    assert new_condition["effective_qdrant_filter"]["must"][0] == condition["effective_qdrant_filter"]
    assert new_condition["effective_qdrant_filter"]["must"][1]["match"]["any"] == ["p1"]
    assert {k: v for k, v in new_plan.items() if k != "plan_id"} == {k: v for k, v in plan.items() if k != "plan_id"}
    assert (case, query, plan, condition, projection, parents) == snapshots
    # Changing the Gold cannot change the filter.
    case2 = {**case, "relevant_parent_ids": ["p1"]}
    assert h4.build_variant(case2, query, plan, condition, "entity", projection, parents)[:2] == (new_plan, new_condition)


def test_prepare_repeat_is_blocked_before_loading_or_network(tmp_path, monkeypatch):
    monkeypatch.setattr(h4, "OUTPUT", tmp_path)
    monkeypatch.setattr(h4.batch, "load_source", lambda: pytest.fail("must block first"))
    with pytest.raises(h4.base.BaselineError, match="already_attempted"):
        h4.prepare()


def test_round_repeat_is_blocked_before_loading_or_network(tmp_path, monkeypatch):
    monkeypatch.setattr(h4, "OUTPUT", tmp_path)
    (tmp_path / "entity").mkdir()
    monkeypatch.setattr(h4, "load_prepared", lambda: pytest.fail("must block first"))
    with pytest.raises(h4.base.BaselineError, match="round_already_attempted"):
        h4.run("entity", None)


def test_gold_veto_stops_before_models_or_network(tmp_path, monkeypatch):
    monkeypatch.setattr(h4, "OUTPUT", tmp_path)
    monkeypatch.setattr(h4, "load_prepared", lambda: (None, None, None, None, {"entity": {"gold_loss_pairs": [{"parent_id": "p"}]}}, None))
    with pytest.raises(h4.base.BaselineError, match="gold_loss_veto"):
        h4.run("entity", None)
    assert not (tmp_path / "entity").exists()


def test_source_exception_cannot_be_implicitly_extended(tmp_path, monkeypatch):
    monkeypatch.setattr(h4, "OUTPUT", tmp_path)
    once = h4.SourceException("source_id", tmp_path)
    assert not once.authorized
    audit = h4.base.h2.RequestAudit()
    audit.active = "rag-v0-053"
    config = SimpleNamespace(selection=SimpleNamespace(exploratory_entity_fallback_below=12))
    with pytest.raises(h4.base.h2.SafetyStop, match="fallback_intercepted"):
        once(SimpleNamespace(plan_id="x"), 10, config, lambda *a: True, audit)
    assert not once.events


def test_explicit_source_exception_has_exact_case_count_once_only(tmp_path, monkeypatch):
    monkeypatch.setattr(h4, "OUTPUT", tmp_path)
    h4.base.write_json(tmp_path / "source-safety-authorization.json", {
        "approved_by": "West", "signal": "source_id", "exact_counts": h4.SOURCE_STOP_COUNTS,
        "unfiltered_backfill": False, "scope": "this H4 source_id round only"})
    once = h4.SourceException("source_id", tmp_path)
    plan = SimpleNamespace(index_profile=SimpleNamespace(collection_name=h4.base.COLLECTION))
    config = SimpleNamespace(selection=SimpleNamespace(exploratory_entity_fallback_below=12))
    audit = h4.base.h2.RequestAudit()
    audit.active = "rag-v0-060"
    with pytest.raises(h4.base.h2.SafetyStop, match="count_or_scope"):
        once(plan, 2, config, lambda *a: True, audit)
    assert once(plan, 3, config, lambda *a: True, audit) is False
    with pytest.raises(h4.base.h2.SafetyStop, match="count_or_scope"):
        once(plan, 3, config, lambda *a: True, audit)
    assert not h4.SourceException("entity", tmp_path).authorized
