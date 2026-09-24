import json
from pathlib import Path

import pytest

from evals.worldline_eval_models import (
    approved_cases,
    dataset_sha256,
    load_worldline_dataset,
)


DATASET = Path(__file__).parents[1] / "datasets" / "worldline_eval_v0.jsonl"


def test_worldline_v0_dataset_contract_and_coverage() -> None:
    cases = load_worldline_dataset(DATASET)

    assert len(cases) == 38
    assert len(approved_cases(cases)) == 38
    assert len({case.case_id for case in cases}) == 38
    assert len(dataset_sha256(DATASET)) == 64
    assert sum(case.task.value == "semantic_expansion" for case in cases) == 14
    assert sum(case.task.value == "genre_exploration" for case in cases) == 10
    assert sum(case.task.value == "entity_resolution" for case in cases) == 6
    assert sum(case.task.value == "checkpoint_recovery" for case in cases) == 4
    artist_ids = {
        value
        for case in cases
        for key, value in case.input.items()
        if key in {"artist_knowledge_id", "root_artist_knowledge_id"}
    }
    artist_ids.update(
        artist["knowledge_entity_id"]
        for case in cases
        for artist in case.expected.get("artists", [])
    )
    assert len(artist_ids) >= 20


def test_dataset_rejects_duplicate_case_id(tmp_path: Path) -> None:
    first = DATASET.read_text(encoding="utf-8").splitlines()[0]
    path = tmp_path / "duplicate.jsonl"
    path.write_text(f"{first}\n{first}\n", encoding="utf-8")

    with pytest.raises(ValueError, match="worldline_eval_duplicate_case"):
        load_worldline_dataset(path)


def test_dataset_rejects_missing_exact_span(tmp_path: Path) -> None:
    payload = json.loads(DATASET.read_text(encoding="utf-8").splitlines()[0])
    payload["expected"]["relations"][0]["exact_span"] = "not in source"
    path = tmp_path / "bad-span.jsonl"
    path.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(ValueError, match="worldline_eval_dataset_invalid"):
        load_worldline_dataset(path)
