import asyncio

from evals.worldline_eval_runner import evaluate_case
from evals.worldline_gold_v1_adapter import adapt_worldline_gold_dataset
from evals.worldline_gold_v1_loader import load_worldline_gold_dataset
from evals.tests.worldline_gold_v1_fixtures import (
    case_payload,
    source_payload,
    write_dataset,
)


def test_adapter_builds_semantic_identity_and_executable_genre_cases(
    tmp_path,
) -> None:
    nujabes_content = "Nujabes is associated with Hip-Hop."
    second_content = "Second Artist is associated with Hip-Hop."
    dataset_path, ledger_path = write_dataset(
        tmp_path,
        [
            case_payload(nujabes_content),
            case_payload(
                second_content,
                suffix="second-artist",
                artist_name="Second Artist",
                artist_id="artist:second_artist",
                catalog_id="456",
            ),
        ],
        [
            source_payload(nujabes_content),
            source_payload(second_content, suffix="second-artist"),
        ],
    )

    adapted = adapt_worldline_gold_dataset(
        load_worldline_gold_dataset(dataset_path, ledger_path)
    )

    assert adapted.approved_gold_case_count == 2
    assert adapted.semantic_case_count == 2
    assert adapted.identity_case_count == 2
    assert adapted.genre_case_count == 1
    assert len(adapted.cases) == 5
    assert all(case.label_provenance.review_status == "approved" for case in adapted.cases)


def test_adapted_gold_cases_execute_through_existing_runner(tmp_path) -> None:
    first_content = "Nujabes is associated with Hip-Hop."
    second_content = "Second Artist is associated with Hip-Hop."
    dataset_path, ledger_path = write_dataset(
        tmp_path,
        [
            case_payload(first_content),
            case_payload(
                second_content,
                suffix="second-artist",
                artist_name="Second Artist",
                artist_id="artist:second_artist",
                catalog_id="456",
            ),
        ],
        [
            source_payload(first_content),
            source_payload(second_content, suffix="second-artist"),
        ],
    )
    adapted = adapt_worldline_gold_dataset(
        load_worldline_gold_dataset(dataset_path, ledger_path)
    )

    results = asyncio.run(
        _evaluate(adapted.cases)
    )

    assert len(results) == 5
    assert all(result.passed for result in results), [
        (result.case_id, result.failures, result.actual) for result in results
    ]


async def _evaluate(cases):
    return [
        await evaluate_case(case, postgres_env_flag="RUN_WORLDLINE_EVAL_POSTGRES")
        for case in cases
    ]
