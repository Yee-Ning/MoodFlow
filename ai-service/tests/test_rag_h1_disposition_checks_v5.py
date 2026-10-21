"""Only approved object-set coverage and title-script comparison may change."""

from copy import deepcopy
import json

import pytest

from evals import rag_h1_batch_v5 as batch
from evals import rag_h1_bundle_v5 as old
from evals import rag_h1_disposition_checks_v5 as checks
from evals import rag_h1_title_format_v5 as previous
from evals import rag_source_baseline as baseline


@pytest.fixture(scope="module")
def saved():
    _, _, _, _, binding = baseline.preflight()
    _, resolver = old.registry_components(binding)
    records = {r["case_id"]: r for r in old.read_json(batch.OUTPUT / "batch_results.json")}
    return records, resolver


def raw(queries):
    return json.dumps({"queries": queries}, ensure_ascii=False)


@pytest.mark.parametrize("number", ["031", "034", "058"])
def test_saved_responses_pass_unchanged_without_api(saved, number):
    records, resolver = saved
    row = records["rag-v0-" + number]
    content = row["expansion_raw"]
    queries, validation = checks.validate_expansion(content, row, resolver)
    assert queries == json.loads(content)["queries"]
    assert all(c["checks"] == "passed" for c in validation)
    assert row["expansion_raw"] == content


@pytest.mark.parametrize("queries", [
    ["周杰伦音乐风格", "周杰伦风格特点", "周杰伦作品风格"],
    ["周杰伦音乐风格", "林俊杰风格特点", "周杰伦与蔡依林风格"],
])
def test_union_still_rejects_missing_or_added_artists(saved, queries):
    records, resolver = saved
    with pytest.raises(old.H1Error, match="entity_constraint_drift|added_registry_entity"):
        checks.validate_expansion(raw(queries), records["rag-v0-031"], resolver)


@pytest.mark.parametrize("queries", [
    ["《东风破》风格", "《东风破》特点", "《东风破》创作"],
    ["《东风破》风格", "《晴天》特点", "《东风破》与《晴天》"],
    ["《东风破》风格", "《青花瓷》特点", "《东风破》《青花瓷》《不存在作品》"],
])
def test_union_still_rejects_missing_replaced_added_works(saved, queries):
    records, resolver = saved
    with pytest.raises(old.H1Error, match="title_constraint_drift"):
        checks.validate_expansion(raw(queries), records["rag-v0-034"], resolver)


def test_union_does_not_relax_year_negative_or_document_constraints(saved):
    records, resolver = saved
    row = deepcopy(records["rag-v0-031"])
    queries = json.loads(row["expansion_raw"])["queries"]
    with pytest.raises(old.H1Error, match="year_constraint_drift"):
        checks.validate_expansion(raw([queries[0] + "2004年", *queries[1:]]), row, resolver)
    row["completed_query"] += " 2004年"
    with pytest.raises(old.H1Error, match="year_constraint_drift"):
        checks.validate_expansion(raw([queries[0] + "2004年", *queries[1:]]), row, resolver)
    row = deepcopy(records["rag-v0-031"])
    row["constraints"]["negative_terms"] = ["翻唱版"]
    with pytest.raises(old.H1Error, match="negative_constraint_drift"):
        checks.validate_expansion(raw([queries[0] + " 不含翻唱版", *queries[1:]]), row, resolver)
    row = deepcopy(records["rag-v0-031"])
    row["completed_query"] += " 中文资料"
    with pytest.raises(old.H1Error, match="document_constraint_drift"):
        checks.validate_expansion(raw([queries[0] + " 中文资料", *queries[1:]]), row, resolver)


def test_union_not_enabled_for_other_cases_and_049_still_missing_album(saved):
    records, resolver = saved
    row = {**records["rag-v0-031"], "case_id": "rag-v0-054"}
    with pytest.raises(old.H1Error, match="entity_constraint_drift"):
        checks.validate_expansion(row["expansion_raw"], row, resolver)
    row = records["rag-v0-049"]
    with pytest.raises(old.H1Error, match="title_constraint_drift"):
        checks.validate_expansion(row["expansion_raw"], row, resolver)


def test_title_normalization_only_changes_comparison_not_text():
    assert checks.title_key("流浪詩人") == checks.title_key("流浪诗人") == "流浪诗人"
    assert checks.title_key("東風破") == "东风破"
    assert checks.title_key("流浪詩人") != checks.title_key("流浪歌手")


def test_real_title_replacement_or_addition_still_rejected(saved):
    records, resolver = saved
    row = records["rag-v0-058"]
    for text in ("《流浪歌手》由谁作词", "《流浪诗人》与《晴天》对唱", "谁作词采用什么对唱"):
        with pytest.raises(old.H1Error, match="title_constraint_drift"):
            checks.validate_text_constraints(text, row, entity_resolver=resolver)


def test_054_remains_rejected_and_type_mismatch_is_not_multiple_candidates(saved):
    from app.rag.query_understanding.entity_resolver import infer_entity_type
    from app.rag.knowledge.entity_linking.models import EntityType
    records, resolver = saved
    row = records["rag-v0-054"]
    queries = json.loads(row["expansion_raw"])["queries"]
    assert [infer_entity_type(q, "稻香") for q in queries] == [None, EntityType.ALBUM, None]
    candidates = resolver.registry.candidates("稻香")
    assert [(c.entity_id, c.entity_type) for c in candidates] == [("song:rice_field", EntityType.SONG)]
    result = resolver.registry.resolve("稻香", type_hint=EntityType.ALBUM)
    assert result.reason == "registry_candidate_type_mismatch" and result.entity is None
    assert "索尼" not in queries[1] and all("索尼" in q for q in (queries[0], queries[2]))
    with pytest.raises(old.H1Error, match="title_constraint_drift"):
        checks.validate_expansion(row["expansion_raw"], row, resolver)


def test_all_21_prior_successes_replay_unchanged_and_prompts_fixed(saved):
    records, resolver = saved
    passed = [r for r in records.values() if r["batch_status"].startswith("passed_")]
    assert len(passed) == 21
    for row in passed:
        assert checks.validate_expansion(row["expansion_raw"], row, resolver)[0] == row["queries"]
        assert checks.validate_hyde(row["hyde_raw"], row, resolver)[0] == row["hyde"]
        assert previous.validate_text_constraints(row["hyde"], row, hyde=True,
                                                   entity_resolver=resolver)["checks"] == "passed"
    old.verify_prompt()
