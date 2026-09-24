"""Lazy CLI dispatch for the existing run_jev_eval.py entry point."""

from __future__ import annotations

import asyncio
from importlib import import_module
from inspect import iscoroutinefunction
from pathlib import Path
from typing import Any

COMMANDS: dict[str, tuple[str, str, tuple[Any, ...]]] = {
    "prepare-v3": ("evals.jev_targeted_v3", "prepare_splits", ()),
    "router-v3": ("evals.jev_targeted_v3", "run_router_multilabel_v3", ()),
    "router-calibrate-v3": ("evals.jev_targeted_v3", "calibrate_router_v3", ()),
    "memory-diagnostic-v3": ("evals.jev_memory_diagnostic_v3", "run_memory_exact_hnsw_v3", ()),
    "memory-retrieval-dev-v3": ("evals.jev_memory_retrieval_v3", "run_memory_retrieval_v3", ("dev",)),
    "memory-retrieval-test-v3": ("evals.jev_memory_retrieval_v3", "run_memory_retrieval_v3", ("test",)),
    "memory-calibrate-v3": ("evals.jev_memory_retrieval_v3", "calibrate_memory_retrieval_v3", ()),
    "memory-filter-dev-v3": ("evals.jev_memory_filter_v3", "run_memory_filter_v3", ("dev",)),
    "memory-filter-test-v3": ("evals.jev_memory_filter_v3", "run_memory_filter_v3", ("test",)),
    "memory-filter-calibrate-v3": ("evals.jev_memory_filter_v3", "calibrate_memory_filter_v3", ()),
    "memory-filter-decide-v3": ("evals.jev_memory_filter_v3", "decide_memory_filter_test_v3", ()),
    "memory-boundary-audit-v3": ("evals.jev_memory_filter_v3", "audit_episodic_boundary_v3", ()),
    "context-audit-v3": ("evals.jev_context_audit_v3", "prepare_context_audit_v3", ()),
    "context-calibrate-v3": ("evals.jev_context_audit_v3", "calibrate_context_v3", ()),
    "context-test-v3": ("evals.jev_context_audit_v3", "replay_context_test_v3", ()),
    "report-v3": ("evals.jev_targeted_v3_report", "write_report_v3", ()),
}


def dispatch(command: str) -> Path:
    module_name, function_name, arguments = COMMANDS[command]
    function = getattr(import_module(module_name), function_name)
    if iscoroutinefunction(function):
        return asyncio.run(function(*arguments))
    return function(*arguments)
