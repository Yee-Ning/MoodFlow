import asyncio
import json
import tempfile
import unittest
from contextlib import redirect_stdout
from io import StringIO
from pathlib import Path

import run_rag_answer_generation
from app.rag.evaluation import (
    AnnotationStatus,
    CitationAwareAnswerGenerator,
    EvaluationContext,
    ExpectedOutcome,
    RAGEvaluationCase,
    RAGEvaluationTrace,
    SupportingSpanAnnotation,
    TraceStageStatuses,
    generate_evaluation_answers,
    load_evaluation_traces,
    write_jsonl,
)


class ScriptedRuntime:
    model_name = "fixture-model"

    def __init__(self, responses: list[str]) -> None:
        self.responses = list(responses)
        self.calls: list[dict] = []

    async def generate(self, payload, **kwargs):
        self.calls.append({"payload": payload, **kwargs})
        if not self.responses:
            raise RuntimeError("no scripted response")
        return self.responses.pop(0)


def context(parent_id: str = "parent-1") -> EvaluationContext:
    return EvaluationContext(
        parent_id=parent_id,
        rank=1,
        title="周杰伦资料",
        content="周杰伦于2000年发行首张专辑。",
        source_id="source-1",
        source_name="测试来源",
    )


def case(
    case_id: str = "rag-v0-001",
    outcome: ExpectedOutcome = ExpectedOutcome.ANSWERABLE,
) -> RAGEvaluationCase:
    if outcome is not ExpectedOutcome.ANSWERABLE:
        return RAGEvaluationCase(
            case_id=case_id,
            query=f"查询 {case_id}",
            expected_outcome=outcome,
            annotation_status=AnnotationStatus.APPROVED,
        )
    return RAGEvaluationCase(
        case_id=case_id,
        query=f"查询 {case_id}",
        expected_outcome=outcome,
        annotation_status=AnnotationStatus.APPROVED,
        relevant_parent_ids=["parent-1"],
        supporting_spans=[
            SupportingSpanAnnotation(
                parent_id="parent-1",
                exact_text="周杰伦于2000年发行首张专辑。",
            )
        ],
        required_facts=["2000年发行首张专辑"],
        reference_answer="周杰伦于2000年发行首张专辑。",
    )


def trace(evaluation_case: RAGEvaluationCase, *, with_context: bool = True) -> RAGEvaluationTrace:
    final = [context()] if with_context else []
    return RAGEvaluationTrace(
        run_id="run-001",
        case_id=evaluation_case.case_id,
        query=evaluation_case.query,
        statuses=TraceStageStatuses(
            query_understanding=(
                "needs_input"
                if evaluation_case.expected_outcome is ExpectedOutcome.NEEDS_INPUT
                else "ready"
            ),
            retrieval="success" if with_context else "empty",
            rerank="success" if with_context else "empty",
            context="success" if with_context else "empty",
        ),
        candidate_pool=final,
        reranked_context=final,
        final_context=final,
    )


class RAGAnswerGenerationTests(unittest.TestCase):
    def test_generator_retries_unknown_citation_then_accepts_grounded_answer(self) -> None:
        runtime = ScriptedRuntime(
            [
                json.dumps(
                    {
                        "answer": "错误引用。[parent-x]",
                        "citation_parent_ids": ["parent-x"],
                    },
                    ensure_ascii=False,
                ),
                json.dumps(
                    {
                        "answer": "周杰伦于2000年发行首张专辑。[parent-1]",
                        "citation_parent_ids": ["parent-1"],
                    },
                    ensure_ascii=False,
                ),
            ]
        )

        answer = asyncio.run(
            CitationAwareAnswerGenerator(runtime).generate("何时出道", [context()])
        )

        self.assertEqual(answer.citation_parent_ids, ["parent-1"])
        self.assertEqual([item["retry"] for item in runtime.calls], [False, True])
        self.assertNotIn("reference_answer", runtime.calls[0]["payload"])

    def test_batch_handles_answer_needs_input_and_insufficient_evidence(self) -> None:
        answerable = case()
        needs_input = case("rag-v0-002", ExpectedOutcome.NEEDS_INPUT)
        insufficient = case("rag-v0-003", ExpectedOutcome.INSUFFICIENT_EVIDENCE)
        runtime = ScriptedRuntime(
            [
                json.dumps(
                    {
                        "answer": "有据可查。[parent-1]",
                        "citation_parent_ids": ["parent-1"],
                    },
                    ensure_ascii=False,
                )
            ]
        )

        generated = asyncio.run(
            generate_evaluation_answers(
                [answerable, needs_input, insufficient],
                [trace(answerable), trace(needs_input, with_context=False), trace(insufficient, with_context=False)],
                generator=CitationAwareAnswerGenerator(runtime),
            )
        )

        self.assertEqual(
            [item.statuses.generation for item in generated],
            ["success", "needs_input", "insufficient_evidence"],
        )
        self.assertEqual(generated[0].citation_parent_ids, ["parent-1"])
        self.assertIn("generation_total", generated[0].timings_ms)
        self.assertGreaterEqual(generated[0].timings_ms["generation_total"], 0)
        self.assertIsNone(generated[1].actual_answer)
        self.assertEqual(len(runtime.calls), 1)

    def test_cli_persists_partial_trace_when_one_generation_fails(self) -> None:
        evaluation_case = case()
        runtime = ScriptedRuntime([])
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            dataset = root / "dataset.jsonl"
            traces = root / "traces.jsonl"
            output = root / "generated.jsonl"
            write_jsonl(dataset, [evaluation_case])
            write_jsonl(traces, [trace(evaluation_case)])
            stdout = StringIO()
            with redirect_stdout(stdout):
                status = run_rag_answer_generation.main(
                    [
                        "--dataset",
                        str(dataset),
                        "--traces",
                        str(traces),
                        "--output",
                        str(output),
                    ],
                    project_root=root,
                    generator=CitationAwareAnswerGenerator(runtime),
                )
            result = json.loads(stdout.getvalue())
            persisted = load_evaluation_traces(output)

        self.assertEqual(status, 2)
        self.assertEqual(result["failed_count"], 1)
        self.assertEqual(persisted[0].statuses.generation, "failed")
        self.assertIn("generation_failed:RuntimeError", persisted[0].warnings)


if __name__ == "__main__":
    unittest.main()
