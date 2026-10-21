import pytest

from tools.v52_offline_runtime import ragsource_generation_preflight as preflight


def saved_item(*, intents=None):
    return {"content": '"周杰伦的首张专辑是《Jay》。"', "metadata": {
        "evidence_id": "parent-proof", "parent_id": "parent-proof", "doc_id": "doc-proof", "citation": "source-proof",
        "source_id": "source-proof", "source_name": "proof", "source_url": None, "title": "proof", "chunk_id": "child-proof",
        "fusion_score": 0.1, "rerank_score": 0.9, "score": 0.9, "score_basis": "rerank",
        "intents": intents if intents is not None else ["worldline"]}}


def test_exact_saved_content_and_metadata_preserved():
    saved = saved_item()
    item = preflight.restored_items([saved])[0]
    assert item.content == saved["content"]
    assert item.metadata == saved["metadata"]
    assert item.id == "evidence:rag:parent-proof"


def test_actual_route_policies_drop_rag_outside_worldline():
    result = preflight.route_probe("周杰伦首张专辑叫什么？", [saved_item()])
    for route in ("general_chat", "tool_calling", "current_song"):
        assert result[route]["included_items"] == 0
        assert result[route]["drop_reasons"] == {"policy_excluded": 1}
        assert not result[route]["rag_in_model_messages"]
    assert result["worldline"]["included_items"] == 1
    assert result["worldline"]["rag_in_model_messages"]


def test_worldline_intent_filter_is_also_real_not_overridden():
    result = preflight.route_probe("周杰伦首张专辑叫什么？", [saved_item(intents=["general_chat"])])
    assert result["worldline"]["included_items"] == 0
    assert result["worldline"]["drop_reasons"] == {"intent_excluded": 1}


@pytest.mark.parametrize("included,expected", [
    (0, "blocked_no_non_worldline_rag_consumer_in_verified_routes"),
    (1, "requires_new_production_path_review_do_not_generate")])
def test_unreviewed_future_path_never_silently_starts_paid_generation(included, expected):
    probe = {route: {"included_items": included} for route in ("general_chat", "tool_calling", "current_song")}
    assert preflight.scope_status([probe]) == expected
