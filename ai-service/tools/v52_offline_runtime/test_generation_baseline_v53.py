"""Offline guards for new-generation population, failure taxonomy and usage."""

import pytest
from types import SimpleNamespace

from tools.v52_offline_runtime import generation_baseline_v53 as run


@pytest.mark.parametrize("code,expected", [
    ("paragraph_missing_citation", "paragraph_missing_citation"),
    ("extra_forbidden:sources_used", "extra_forbidden"),
    ("extra_forbidden:additionalProperties", "additionalProperties"),
    ("unknown_citation:abc", "unknown_citation"),
    ("extra_forbidden:narrative_length", "narrative_length"),
    ("request_timeout", "timeout"), ("missing_citation", "other:missing_citation")])
def test_failure_classification(code, expected):
    assert run.failure_category(code) == expected


def test_usage_handles_responses_and_chat_without_duplicate_total():
    entries = [{"usage": {"input_tokens": 5, "output_tokens": 2, "total_tokens": 7}},
               {"usage": {"prompt_tokens": 8, "completion_tokens": 3, "total_tokens": 11, "prompt_cache_hit_tokens": 4}}]
    totals = run.usage_totals(entries)
    assert totals["input_tokens"] == 13 and totals["output_tokens"] == 5
    assert totals["reported_total_tokens"] == 18 and totals["cache_hit_input_tokens"] == 4


def test_missing_usage_is_visible_not_invented():
    totals = run.usage_totals([{}])
    assert totals["usage_missing"] == 1 and totals["reported_total_tokens"] == 0


@pytest.mark.parametrize("citations,status", [([], "refused"), (["source"], "success")])
def test_valid_abstention_remains_visible_as_refusal(citations, status):
    assert run.accepted_status(citations) == status


def test_v53_population_and_unchanged_ground_truths():
    selected = run.selected_gold()
    assert len(selected) == 34
    assert {row["answerability"] for row in selected} == {"full", "partial"}
    old = {row["case_id"]: row for row in run.rows(run.GOLD.with_name("rag_eval_gold_v5_2.frozen.jsonl"))}
    assert all(row["relevant_parent_ids"] == old[row["case_id"]]["relevant_parent_ids"] for row in selected)
    for row in selected:
        if row["case_id"] in {"rag-v0-036", "rag-v0-039"}:
            assert row["ground_truth"] == old[row["case_id"]]["ground_truth"]


def test_protected_hash_drift_fails(tmp_path, monkeypatch):
    monkeypatch.setattr(run, "ROOT", tmp_path)
    path = tmp_path / "source.json"
    path.write_text("original", encoding="utf-8")
    pins = {"source.json": run.digest(path)}
    run.check_pins(pins)
    path.write_text("drift", encoding="utf-8")
    with pytest.raises(ValueError, match="protected_drift"):
        run.check_pins(pins)


def test_wrapped_guard_allows_exact_windows_internal_socketpair(monkeypatch):
    socket_object = object()
    caller = SimpleNamespace(f_globals={"__name__": "socket"},
        f_code=SimpleNamespace(co_name="_fallback_socketpair"),
        f_locals={"csock": socket_object, "addr": "127.0.0.1", "port": 50123})

    class ParentGuard:
        def __init__(self):
            self.local_socketpairs = 0

        def audit(self, event, args):
            raise AssertionError("exact local IPC must be recognized before delegated stack inspection")

    monkeypatch.setattr(run, "sys", SimpleNamespace(_getframe=lambda depth: caller, addaudithook=lambda audit: None))
    guard = run.install_guard(None, SimpleNamespace(ReadOnlyGuard=ParentGuard))
    guard.audit("socket.connect", (socket_object, ("127.0.0.1", 50123)))
    assert guard.local_socketpairs == 1


@pytest.mark.parametrize("host", ["api.deepseek.com", b"api.deepseek.com"])
def test_guard_accepts_httpcore_dns_host_bytes(monkeypatch, host):
    class ParentGuard:
        def audit(self, event, args):
            raise AssertionError("authorized provider DNS should be recognized by the outer wrapper")

    monkeypatch.setattr(run, "sys", SimpleNamespace(addaudithook=lambda audit: None))
    guard = run.install_guard(None, SimpleNamespace(ReadOnlyGuard=ParentGuard))
    guard.audit("socket.getaddrinfo", (host, 443))
