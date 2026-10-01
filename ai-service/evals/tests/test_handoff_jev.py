"""Frozen replay, confusion matrix and release gates cannot hide failures."""

import argparse
import json
import logging
from unittest.mock import AsyncMock

import pytest

from app.agent.decision.jev.client import DecisionAnswer, JevDecisionError
from evals.handoff_jev import (
    ObservedClient,
    CONFIRMATION_DATASET,
    classify_guard,
    envelope_for_case,
    entry_report,
    evaluate_guard,
    guard_metrics,
    guard_report,
    monetary_cost,
    nonnegative_rate,
    read_guard_cases,
    read_entry_cases,
    recommend_guard_threshold,
)
from tests.test_jev_decision_layer import response


def row(*, split="blind", expected=True, fidelity=0.99, risk=0.01, status="evaluated"):
    return dict(
        split=split,
        expected_allow=expected,
        status=status,
        scores={"task_fidelity": fidelity, "authority_override": risk},
        input_tokens=100,
        output_tokens=10,
        usage_complete=True,
        api_attempts=1,
        api_failures=0,
        latency_ms=500,
    )


def test_dataset_freeze_balanced_splits_and_local_permission_boundaries():
    cases = read_guard_cases()
    assert len(cases) == 64
    for case in cases:
        if case["facet"] == "local_contract":
            with pytest.raises(ValueError):
                envelope_for_case(case)
        else:
            assert envelope_for_case(case).active_task.input_text == case["task_input"]


@pytest.mark.parametrize("modify", ["duplicate", "missing", "label", "one_split"])
def test_bad_dataset_is_rejected(tmp_path, modify):
    cases = read_guard_cases()
    if modify == "duplicate":
        cases.append(cases[0])
    elif modify == "missing":
        cases[0].pop("summary")
    elif modify == "label":
        cases[0]["expected_allow"] = "true"
    else:
        cases = [case for case in cases if case["split"] == "dev"]
    path = tmp_path / "cases.jsonl"
    path.write_text("\n".join(json.dumps(case) for case in cases), encoding="utf-8")
    with pytest.raises(ValueError):
        read_guard_cases(path)


def test_false_allows_false_rejects_and_unavailability_are_not_diluted():
    rows = [
        row(),
        row(expected=True, fidelity=0.4),
        row(expected=False),
        row(expected=False, risk=0.9),
        row(expected=False, status="unavailable"),
    ]
    metrics = guard_metrics(rows)
    assert (
        metrics["false_allows"]
        == metrics["false_rejects"]
        == metrics["unavailable"]
        == 1
    )
    assert metrics["fail_open_negative_count"] == 1
    assert metrics["accuracy"] == 0.4
    assert classify_guard(rows[-1], 0.8, 0.2) is None
    assert not guard_report(rows)["synthetic_gate_passed"]


def test_confirmation_has_unseen_semantic_holdout_and_all_envelopes_validate():
    initial = {case["original_input"] for case in read_guard_cases()}
    cases = read_guard_cases(CONFIRMATION_DATASET)
    assert len(cases) == 64
    assert all(
        case["original_input"] not in initial
        for case in cases
        if case["split"] == "blind" and case["facet"] != "local_contract"
    )
    for case in cases:
        if case["facet"] != "local_contract":
            envelope_for_case(case)


def test_dev_sweep_never_selects_threshold_on_blind_data_or_enables_production():
    rows = [
        row(split="dev"),
        row(split="dev", expected=False, risk=0.9),
        row(),
        row(expected=False, risk=0.9),
    ]
    report = guard_report(rows)
    assert report["synthetic_gate_passed"]
    assert report["production_enable_recommended"] is False
    assert len(report["dev_threshold_sweep"]) == 25
    changed = rows + [row(expected=False)]
    assert guard_report(changed)["dev_threshold_sweep"] == report["dev_threshold_sweep"]
    assert not guard_report(changed)["synthetic_gate_passed"]
    assert guard_report(changed)["calibration"] == report["calibration"]


def test_calibration_uses_dev_separation_instead_of_lowering_until_blind_passes():
    dev = [
        row(split="dev", fidelity=0.82),
        row(split="dev", expected=False, fidelity=0.08, risk=0.1),
    ]
    calibrated = recommend_guard_threshold(dev)
    assert calibrated["recommended_candidate_threshold"] == 0.7
    assert calibrated["source"] == "dev_only"
    assert (
        recommend_guard_threshold([row(split="dev")])["recommended_candidate_threshold"]
        is None
    )


@pytest.mark.asyncio
async def test_local_rejection_makes_no_api_call_and_unavailable_is_explicit():
    cases = read_guard_cases()
    valid = next(case for case in cases if case["facet"] != "local_contract")
    invalid = next(case for case in cases if case["facet"] == "local_contract")
    client = AsyncMock()
    client.decide.side_effect = JevDecisionError("failure")
    rows = await evaluate_guard([invalid, valid], client)
    assert rows[0]["status"] == "local_reject" and rows[0]["api_attempts"] == 0
    assert rows[1]["status"] == "unavailable" and rows[1]["api_failures"] == 1
    assert not rows[1]["usage_complete"]
    assert client.decide.await_count == 1


@pytest.mark.asyncio
async def test_observation_keeps_successful_cost_when_later_call_fails():
    client = AsyncMock()
    client.decide.side_effect = [
        response({"task_fidelity": DecisionAnswer("noul", noul=1)}),
        JevDecisionError("failure"),
    ]
    observed = ObservedClient(client)
    await observed.decide({}, {})
    with pytest.raises(JevDecisionError):
        await observed.decide({}, {})
    evidence = observed.evidence()
    assert evidence["api_attempts"] == 2 and evidence["api_failures"] == 1
    assert evidence["reported_input_tokens"] == evidence["reported_output_tokens"] == 1
    assert not evidence["usage_complete"] and evidence["input_tokens"] is None


def test_currency_cost_requires_rates_and_complete_provider_usage():
    assert monetary_cost([row()]) is None
    assert monetary_cost([row()], 2, 4) == 0.00024
    missing = row()
    missing["usage_complete"] = False
    assert monetary_cost([missing], 2, 4) is None


def entry_rows():
    return [
        dict(
            facet=case["facet"],
            baseline_exact=True,
            effective_exact=True,
            jev_independent_exact=True,
            router_source="jev_single",
            error_type=None,
            api_attempts=1,
            api_failures=0,
            latency_ms=500,
            usage_complete=True,
            baseline={
                "latency_ms": 1000,
                "api_calls": 1,
                "usage": {"input_tokens": 100, "output_tokens": 10},
            },
        )
        for case in read_entry_cases()
    ]


def test_entry_gate_excludes_fallback_from_independent_accuracy_and_counts_its_latency():
    rows = entry_rows()
    rows[0].update(router_source="legacy_hybrid_fallback", jev_independent_exact=None)
    report = entry_report(rows)
    assert report["metrics"]["independent_count"] == 29
    assert report["metrics"]["fallback_count"] == 1
    assert report["metrics"]["estimated_active_max_ms"] == 1500
    assert report["production_enable_recommended"] is False


def test_critical_mismatch_cannot_hide_behind_high_overall_accuracy():
    rows = entry_rows()
    victim = next(row for row in rows if row["facet"] == "negated_action")
    victim.update(effective_exact=False, jev_independent_exact=False)
    report = entry_report(rows)
    assert report["gates"]["effective_exact_at_least_95_percent"]
    assert not report["gates"]["critical_cases_exact"]
    assert not report["synthetic_gate_passed"]


def test_fallback_latency_cannot_hide_behind_fast_jev_calls():
    rows = entry_rows()
    for row in rows[:2]:
        row.update(router_source="legacy_hybrid_fallback", jev_independent_exact=None)
        row["baseline"]["latency_ms"] = 10000
    report = entry_report(rows)
    assert report["metrics"]["p95_ms"] == 500
    assert report["metrics"]["estimated_active_p95_ms"] == 6000
    assert not report["gates"]["p95_at_most_2500_ms"]


def test_real_fallback_timing_is_not_counted_twice():
    rows = entry_rows()
    for row in rows[:2]:
        row.update(
            router_source="legacy_hybrid_fallback",
            jev_independent_exact=None,
            latency_ms=1500,
            fallback_latency_included=True,
        )
        row["baseline"]["latency_ms"] = 10000
    report = entry_report(rows)
    assert report["metrics"]["estimated_active_p95_ms"] == 1050
    assert report["gates"]["p95_at_most_2500_ms"]


@pytest.mark.parametrize("rate", ["-1", "nan", "inf"])
def test_invalid_currency_rates_rejected(rate):
    with pytest.raises(argparse.ArgumentTypeError):
        nonnegative_rate(rate)


def test_cli_gate_failure_exits_nonzero_without_network_or_activation(
    tmp_path, monkeypatch, capsys
):
    from evals import handoff_jev

    async def failed_report(args):
        return {"guard": guard_report([row(), row(expected=False)])}

    monkeypatch.setattr(handoff_jev, "replay", failed_report)
    monkeypatch.setattr(
        "sys.argv", ["handoff_jev", "--output", str(tmp_path), "--require-gates"]
    )
    previous = logging.root.manager.disable
    try:
        with pytest.raises(SystemExit) as exc:
            handoff_jev.main()
        assert exc.value.code == 2
        assert json.loads(capsys.readouterr().out)["synthetic_gates"] == {
            "guard": False
        }
    finally:
        logging.disable(previous)
