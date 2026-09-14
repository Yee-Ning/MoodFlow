import asyncio
import json
import unittest
from pathlib import Path

from app.rag.query_understanding import (
    QueryUnderstandingService,
    QueryUnderstandingStatus,
    load_query_understanding_config,
)


FIXTURE = (
    Path(__file__).resolve().parent
    / "fixtures"
    / "query_understanding"
    / "golden_v1.jsonl"
)


def load_cases() -> list[dict[str, object]]:
    return [
        json.loads(line)
        for line in FIXTURE.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


class QueryUnderstandingGoldenTests(unittest.TestCase):
    def test_golden_corpus_covers_at_least_fifty_queries(self) -> None:
        cases = load_cases()

        self.assertGreaterEqual(len(cases), 50)
        self.assertGreaterEqual(
            sum(item["status"] == "needs_input" for item in cases), 5
        )

    def test_deterministic_pipeline_matches_golden_expectations(self) -> None:
        config = load_query_understanding_config().model_copy(
            update={"llm_fallback_enabled": False}
        )
        service = QueryUnderstandingService(config=config)

        async def evaluate() -> None:
            for case in load_cases():
                query = str(case["query"])
                with self.subTest(query=query):
                    result = await service.understand(query)
                    self.assertEqual(result.status.value, case["status"])
                    if result.status is QueryUnderstandingStatus.NEEDS_INPUT:
                        self.assertIsNotNone(result.clarification_question)
                        continue
                    assert result.analysis is not None
                    assert result.search_plan is not None
                    actual_ids = set(result.analysis.constraints.entity_ids)
                    self.assertTrue(
                        set(case.get("entity_ids", [])) <= actual_ids,
                        (query, actual_ids),
                    )
                    actual_types = {
                        item.value for item in result.analysis.query_types
                    }
                    self.assertTrue(
                        set(case.get("query_types", [])) <= actual_types,
                        (query, actual_types),
                    )
                    constraints = result.analysis.constraints
                    for field in (
                        "start_year",
                        "end_year",
                        "languages",
                        "content_types",
                        "negative_terms",
                    ):
                        if field not in case:
                            continue
                        actual = getattr(constraints, field)
                        if isinstance(actual, list):
                            actual = [
                                item.value if hasattr(item, "value") else item
                                for item in actual
                            ]
                        self.assertEqual(actual, case[field], (query, field))
                    warning = case.get("warning")
                    if warning is not None:
                        self.assertIn(warning, result.search_plan.warnings)

        asyncio.run(evaluate())


if __name__ == "__main__":
    unittest.main()
