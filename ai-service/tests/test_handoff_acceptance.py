import json

import pytest

from scripts.handoff_acceptance import (
    assert_public,
    cases,
    parse_sse,
    run_acceptance,
    validate_exchange,
)


def test_real_explorer_to_curator_case_preserves_original_requests():
    suite = cases()
    search = next(item for item in suite if item["id"] == "search_read_only")
    assert search["message"] == "搜索周杰伦的晴天，不要播放"
    worldline = next(item for item in suite if item["id"] == "worldline_grounding")
    assert worldline["message"] == "探索周杰伦的音乐世界线"
    compound = next(item for item in suite if item["id"] == "explorer_to_curator_sse")
    assert compound["message"] == "先探索周杰伦的音乐世界线，然后暂停播放"
    assert compound["endpoint"] == "chat"
    assert compound["routes"] == ["worldline", "tool_calling"]
    assert compound["actions"] == ["pause_player"]
    assert compound["sources"] is True
    assert compound["player_context"]["has_current_song"] is True


def response():
    return {
        "status": "success",
        "message": "你好",
        "results": [
            {
                "task_id": "one",
                "status": "success",
                "subgraph": "general_chat",
                "answer": "你好",
            },
        ],
    }


def stream(events):
    return "".join(
        f"event: {name}\ndata: {json.dumps(data)}\n\n" for name, data in events
    )


def test_json_and_sse_require_real_public_success():
    assert validate_exchange(response(), routes=["general_chat"])["status"] == "success"
    body = stream([("message", {"delta": "你好"}), ("final", response()), ("done", {})])
    assert validate_exchange(body, streaming=True, routes=["general_chat"])[
        "event_names"
    ] == ["message", "final", "done"]
    assert parse_sse(body.replace("\n", "\r\n"))[-1] == ("done", {})


@pytest.mark.parametrize(
    "events",
    [
        [("final", response())],
        [("final", response()), ("done", {}), ("done", {})],
        [("error", {"message": "offline"}), ("done", {})],
        [("final", response()), ("message", {"delta": "late"}), ("done", {})],
    ],
)
def test_http_200_is_insufficient_when_sse_is_incomplete_or_failed(events):
    with pytest.raises(ValueError):
        validate_exchange(stream(events), streaming=True, routes=["general_chat"])


@pytest.mark.parametrize("field", ["handoff_trace", "api_key", "context_summary"])
def test_nested_private_fields_are_rejected(field):
    with pytest.raises(ValueError, match="private_field"):
        assert_public({"results": [{"metadata": {field: "hidden"}}]})


def test_grounding_and_plan_are_required():
    with pytest.raises(ValueError, match="missing_grounded_sources"):
        validate_exchange(response(), routes=["general_chat"], sources=True)
    with pytest.raises(ValueError, match="unexpected_task_plan"):
        validate_exchange(response(), routes=["worldline"])


def test_missing_target_can_be_clarified_before_dispatch_but_never_counted_as_success():
    public = {"status": "needs_input", "message": "请先选择歌曲。", "results": []}
    assert (
        validate_exchange(
            public,
            status="needs_input",
            routes=["current_song"],
            allow_early_clarification=True,
        )["routes"]
        == []
    )
    with pytest.raises(ValueError, match="unexpected_response_status"):
        validate_exchange(
            public, routes=["current_song"], allow_early_clarification=True
        )


def test_action_stream_must_match_final_and_reject_duplicate_execution():
    action = {
        "action_id": "action:one",
        "task_id": "one",
        "name": "pause_player",
        "arguments": {},
    }
    public = response()
    public["results"][0]["metadata"] = {"client_actions": [action]}
    body = stream([("action", action), ("final", public), ("done", {})])
    assert validate_exchange(
        body, streaming=True, routes=["general_chat"], actions=["pause_player"]
    )["action_names"] == ["pause_player"]
    with pytest.raises(ValueError, match="duplicate_action"):
        validate_exchange(
            stream(
                [
                    ("action", action),
                    ("action", action),
                    ("final", public),
                    ("done", {}),
                ]
            ),
            streaming=True,
        )
    with pytest.raises(ValueError, match="action_final_mismatch"):
        validate_exchange(
            stream([("final", public), ("done", {})]),
            streaming=True,
            routes=["general_chat"],
            actions=["pause_player"],
        )


def test_unavailable_server_is_recorded_without_business_calls_or_exception_details(
    monkeypatch, tmp_path
):
    class UnavailableClient:
        def __init__(self, **kwargs):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def get(self, path):
            raise RuntimeError("private upstream detail")

        def post(self, *args, **kwargs):
            pytest.fail("business call after failed preflight")

    monkeypatch.setattr("scripts.handoff_acceptance.httpx.Client", UnavailableClient)
    output = tmp_path / "report.json"
    assert run_acceptance("http://127.0.0.1:8001", output) == 2
    report = json.loads(output.read_text(encoding="utf-8"))
    assert report["business_cases_performed"] is False
    assert "private upstream detail" not in output.read_text(encoding="utf-8")
