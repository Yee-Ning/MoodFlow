import json

import pytest

from app.agent.foundation.models import TaskStatus
from app.agent.foundation.subgraphs.worldline.graph import _narrative_failure_detail, create_worldline_subgraph
from app.agent.foundation.subgraphs.worldline.generation import StructuredWorldLineGenerator
from app.agent.foundation.subgraphs.worldline.models import EntityType
from app.agent.structured_output import StructuredOutputError
from app.agent.structured_output.errors import FailureType, ValidationFailure
from tests.test_worldline_generation import ScriptedRuntime
from tests.test_worldline_graph import EmptyRetriever, FakeMusicService, entity, invoke


def test_failure_details_preserve_safe_category_and_drop_arbitrary_exception_text():
    failure = ValidationFailure(FailureType.SEMANTIC_VALIDATION_ERROR, "unknown_citation")
    wrapped = StructuredOutputError("worldline_narrative", FailureType.RETRY_EXHAUSTED,
                                    attempts=2, last_failure_type=failure.failure_type)
    wrapped.__cause__ = failure
    assert _narrative_failure_detail(wrapped) == {
        "failure_type": "semantic_validation_error", "attempts": 2, "validation_code": "unknown_citation"}
    failure.code = "private user text and credential"
    assert _narrative_failure_detail(wrapped)["validation_code"] == "unclassified"
    unknown = RuntimeError("private user text and credential")
    unknown.__cause__ = unknown
    assert "private" not in json.dumps(_narrative_failure_detail(unknown))


@pytest.mark.asyncio
async def test_invalid_narrative_remains_failed_with_safe_reason_after_original_two_attempts():
    runtime = ScriptedRuntime(['{"narrative":"这是一段没有来源的具体歌曲事实。"}'] * 2)
    graph = create_worldline_subgraph(
        music_service=FakeMusicService(entity(186016, EntityType.SONG, "晴天")),
        retriever=EmptyRetriever(), generator=StructuredWorldLineGenerator(runtime))
    output = await invoke(graph, "探索歌曲《晴天》的世界线")
    result = output["task_result"]
    assert result.status is TaskStatus.FAILED and result.data["reason"] == "narrative_generation_failed"
    assert result.data["generation_failure"] == {
        "failure_type": "semantic_validation_error", "attempts": 2, "validation_code": "missing_citation"}
    assert len(runtime.calls) == 2
