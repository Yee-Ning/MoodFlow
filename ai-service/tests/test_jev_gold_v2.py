import json
from collections import Counter
import pytest

from evals import jev_gold_v2
from evals.jev_gold_v2 import (
    BOUNDARY_PAIRS, DECISIONS, OUTPUT, QUESTIONABLE, REVIEW, SOURCE,
    freeze_gold, summary, write_review,
)


@pytest.fixture(autouse=True)
def isolated_gold_outputs(monkeypatch, tmp_path):
    originals = {name: (globals()[name], globals()[name].read_bytes()) for name in ("OUTPUT", "REVIEW")}
    for name, (original, data) in originals.items():
        target = tmp_path / original.name
        target.write_bytes(data)
        monkeypatch.setattr(jev_gold_v2, name, target)
        monkeypatch.setitem(globals(), name, target)
    yield
    for original, data in originals.values():
        assert original.read_bytes() == data


def test_all_questionable_cases_are_decided_and_old_gold_is_immutable():
    before = SOURCE.read_bytes()
    frozen = OUTPUT.read_bytes()
    freeze_gold()
    rows = [json.loads(line) for line in OUTPUT.read_text(encoding="utf-8").splitlines()]
    original = [json.loads(line) for line in before.decode("utf-8").splitlines()]
    assert SOURCE.read_bytes() == before
    assert OUTPUT.read_bytes() == frozen
    assert set(DECISIONS) == QUESTIONABLE
    assert [(r["case_id"], r["original_label"]) for r in rows[:len(original)]] == [
        (r["case_id"], r["gold_routes"]) for r in original
    ]
    assert all(r["capability_contract_version"] == "1.0" for r in rows)
    assert all(r["action"] in {"RELABEL", "REMOVE_FROM_SCORING"}
               for r in rows if r["case_id"] in QUESTIONABLE)


def test_removed_cases_remain_negative_and_paired_boundaries_are_balanced():
    rows = [json.loads(line) for line in OUTPUT.read_text(encoding="utf-8").splitlines()]
    removed = [r for r in rows if r["action"] == "REMOVE_FROM_SCORING"]
    assert {r["case_id"] for r in removed} == {"manual-world-017", "manual-multi-025"}
    assert all(r["scoring_status"] == "negative_unsupported" and not r["adjudicated_label"] for r in removed)
    counts = Counter(r.get("pair_id") for r in rows if r["action"] == "ADD_BOUNDARY")
    assert len(counts) == len(BOUNDARY_PAIRS)
    assert set(counts.values()) == {2}
    for pair_id in counts:
        pair = [r for r in rows if r.get("pair_id") == pair_id]
        assert pair[0]["adjudicated_label"] != pair[1]["adjudicated_label"]
    totals = summary(rows)
    assert totals["historical_actions"] == {"KEEP": 32, "RELABEL": 13, "REMOVE_FROM_SCORING": 2}
    assert totals["negative_unsupported"] == 4
    assert totals["followup_excluded"] == 2
    assert totals["total"] == 63
    write_review(rows)
    review = REVIEW.read_text(encoding="utf-8")
    assert all(case_id in review for case_id in QUESTIONABLE)
    assert "KEEP **32**" in review and "RELABEL **13**" in review


def test_invalid_adjudication_is_rejected(monkeypatch):
    before = OUTPUT.read_bytes()
    with monkeypatch.context() as adjudication:
        adjudication.setitem(DECISIONS, "manual-hard-019", ("KEEP", ["general_chat"], "bad", "bad"))
        with pytest.raises(ValueError, match="KEEP changed"):
            freeze_gold()
    assert OUTPUT.read_bytes() == before
    freeze_gold()
