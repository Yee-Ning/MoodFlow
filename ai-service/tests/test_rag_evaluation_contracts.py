import json
import tempfile
import unittest
from pathlib import Path

from pydantic import ValidationError

from app.rag.evaluation import (
    AnnotationStatus,
    EvaluationContext,
    ExpectedOutcome,
    RAGEvaluationCase,
    RAGEvaluationTrace,
    SupportingSpanAnnotation,
    TraceStageStatuses,
    load_evaluation_cases,
    load_evaluation_traces,
    write_jsonl,
)


def context(parent_id: str, rank: int) -> EvaluationContext:
    return EvaluationContext(
        parent_id=parent_id,
        rank=rank,
        title=f"Title {parent_id}",
        content=f"Content {parent_id}",
        source_id=f"source-{parent_id}",
        source_name="fixture",
        token_count=10,
    )


class RAGEvaluationContractTests(unittest.TestCase):
    def test_pending_case_allows_labels_to_be_completed_later(self) -> None:
        case = RAGEvaluationCase(
            case_id="fact-001",
            query="晴天的创作背景",
            expected_outcome=ExpectedOutcome.ANSWERABLE,
        )

        self.assertEqual(case.annotation_status, AnnotationStatus.PENDING)
        self.assertEqual(case.relevant_parent_ids, [])

    def test_approved_answerable_case_requires_complete_human_labels(self) -> None:
        with self.assertRaises(ValidationError):
            RAGEvaluationCase(
                case_id="fact-001",
                query="晴天的创作背景",
                expected_outcome=ExpectedOutcome.ANSWERABLE,
                annotation_status=AnnotationStatus.APPROVED,
            )

    def test_approved_answerable_case_accepts_consistent_evidence(self) -> None:
        case = RAGEvaluationCase(
            case_id="fact-001",
            query="晴天的创作背景",
            expected_outcome=ExpectedOutcome.ANSWERABLE,
            annotation_status=AnnotationStatus.APPROVED,
            relevant_parent_ids=["parent-1"],
            supporting_spans=[
                SupportingSpanAnnotation(
                    parent_id="parent-1",
                    exact_text="《晴天》收录于专辑《叶惠美》。",
                )
            ],
            required_facts=["收录专辑"],
            reference_answer="《晴天》收录于《叶惠美》。",
        )

        self.assertEqual(case.annotation_status, AnnotationStatus.APPROVED)

    def test_approved_needs_input_case_rejects_answer_labels(self) -> None:
        with self.assertRaises(ValidationError):
            RAGEvaluationCase(
                case_id="ambiguous-001",
                query="这首歌有什么故事",
                expected_outcome=ExpectedOutcome.NEEDS_INPUT,
                annotation_status=AnnotationStatus.APPROVED,
                relevant_parent_ids=["parent-1"],
            )

    def test_trace_enforces_stage_lineage_and_citation_integrity(self) -> None:
        trace = RAGEvaluationTrace(
            run_id="run-001",
            case_id="fact-001",
            query="晴天的创作背景",
            statuses=TraceStageStatuses(
                query_understanding="ready",
                retrieval="success",
                rerank="success",
                context="success",
                generation="success",
            ),
            candidate_pool=[context("parent-1", 1), context("parent-2", 2)],
            reranked_context=[context("parent-2", 1)],
            final_context=[context("parent-2", 1)],
            actual_answer="回答 [parent-2]",
            citation_parent_ids=["parent-2"],
            context_token_count=10,
            context_token_budget=100,
        )

        self.assertEqual(trace.final_context[0].parent_id, "parent-2")
        with self.assertRaises(ValidationError):
            trace.model_copy(
                update={"citation_parent_ids": ["parent-missing"]}
            ).model_dump()
            # model_copy deliberately skips validation; reconstruct to assert the contract.
            RAGEvaluationTrace.model_validate(
                trace.model_copy(
                    update={"citation_parent_ids": ["parent-missing"]}
                ).model_dump()
            )

    def test_trace_rejects_rerank_items_outside_candidate_pool(self) -> None:
        with self.assertRaises(ValidationError):
            RAGEvaluationTrace(
                run_id="run-001",
                case_id="fact-001",
                query="晴天的创作背景",
                statuses=TraceStageStatuses(query_understanding="ready"),
                candidate_pool=[context("parent-1", 1)],
                reranked_context=[context("parent-2", 1)],
            )

    def test_jsonl_io_round_trips_and_rejects_duplicate_case_ids(self) -> None:
        case = RAGEvaluationCase(
            case_id="fact-001",
            query="晴天的创作背景",
            expected_outcome=ExpectedOutcome.ANSWERABLE,
        )
        trace = RAGEvaluationTrace(
            run_id="run-001",
            case_id=case.case_id,
            query=case.query,
            statuses=TraceStageStatuses(query_understanding="ready"),
        )
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            cases_path = root / "cases.jsonl"
            traces_path = root / "traces.jsonl"
            write_jsonl(cases_path, [case])
            write_jsonl(traces_path, [trace])
            self.assertEqual(load_evaluation_cases(cases_path), [case])
            self.assertEqual(load_evaluation_traces(traces_path), [trace])
            cases_path.write_text(
                "\n".join(
                    json.dumps(case.model_dump(mode="json"), ensure_ascii=False)
                    for _ in range(2)
                ),
                encoding="utf-8",
            )
            with self.assertRaisesRegex(ValueError, "case_id_duplicate"):
                load_evaluation_cases(cases_path)


if __name__ == "__main__":
    unittest.main()
