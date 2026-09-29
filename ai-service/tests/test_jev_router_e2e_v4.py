import json

from evals.jev_router_e2e_v4 import DATASET_SHA256, read_cases, summarize


def test_dataset_freeze_and_task_annotations():
    rows = read_cases()
    assert len(rows) == 24
    assert DATASET_SHA256 == "216197f910ef0efc7acc44e790744ee0770bbac20f9d9f2e1420f9780a4fdf7c"
    assert len({row["case_id"] for row in rows}) == len(rows)
    assert all(task["fragment"] in row["query"] for row in rows for task in row["expected_tasks"])


def test_summary_requires_task_exact_not_only_route_set():
    gold = {"expected_routes": ["tool_calling"],
            "expected_tasks": [{"route": "tool_calling", "fragment": "播放歌曲", "depends_on": []}],
            "expected_clarify": False, "facet": "unit"}
    output = {"routes": ["tool_calling"],
              "tasks": [{"route": "tool_calling", "fragment": "搜索歌曲", "depends_on": []}],
              "clarify": False, "main_graph_schema_valid": True,
              "fallback_reason": None, "error_type": None, "latency_ms": 10,
              "api_calls": 1, "usage": {"input_tokens": 12, "output_tokens": 3}}
    metrics = summarize([{"gold": gold, "jev": output}], "jev")
    assert metrics["routing"]["exact"] == 1
    assert metrics["tasks"]["full_decision_exact"] == 0
    assert metrics["tasks"]["fragment_accuracy"] == 0
    assert metrics["performance"]["p95_ms"] == 10


def test_all_frozen_rows_are_json_serializable():
    assert len(json.dumps(read_cases(), ensure_ascii=False)) > 1000
