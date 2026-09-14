import json
import tempfile
import unittest
from contextlib import redirect_stdout
from io import StringIO
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import run_retrieval_eval
from app.rag.query_understanding.models import IndexProfile
from app.rag.retrieval import RetrievalResult, RetrievalStatus


def profile() -> IndexProfile:
    return IndexProfile(
        collection_name="music_knowledge_test",
        source_release_id="knowledge-v2-test",
        index_schema_version="v2",
        projection_version="v2",
        registry_version="v3",
    )


class EmptyRetriever:
    async def retrieve(self, plan):
        return RetrievalResult(
            status=RetrievalStatus.EMPTY,
            plan_id=plan.plan_id,
            index_profile=profile(),
        )


class RunRetrievalEvaluationTests(unittest.TestCase):
    def test_cli_scores_empty_candidate_pool_without_applying_rerank(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            dataset = root / "golden.jsonl"
            dataset.write_text(
                json.dumps(
                    {
                        "case_id": "fact-001",
                        "query": "晴天创作背景",
                        "relevant_parent_ids": ["parent-a"],
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
            output = StringIO()
            with (
                patch.object(
                    run_retrieval_eval,
                    "load_retrieval_index_binding",
                    return_value=SimpleNamespace(profile=profile()),
                ),
                redirect_stdout(output),
            ):
                status = run_retrieval_eval.main(
                    [
                        "--dataset",
                        str(dataset),
                        "--index-manifest",
                        "manifest.json",
                        "--no-llm",
                    ],
                    project_root=root,
                    retrieval_factory=lambda binding, config: EmptyRetriever(),
                )

        payload = json.loads(output.getvalue())
        self.assertEqual(status, 0)
        self.assertEqual(payload["pipeline_stage"], "candidate_pool_evaluation")
        self.assertFalse(payload["rerank_applied"])
        self.assertEqual(payload["metrics"]["case_count"], 1)
        self.assertEqual(payload["metrics"]["recall_at_40"], 0)
        self.assertEqual(payload["case_statuses"], {"fact-001": "empty"})


if __name__ == "__main__":
    unittest.main()
