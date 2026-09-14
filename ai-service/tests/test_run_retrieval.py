import json
import tempfile
import unittest
from contextlib import redirect_stdout
from io import StringIO
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import run_retrieval
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


class FakeCandidatePoolRetriever:
    def __init__(self) -> None:
        self.plan = None

    async def retrieve(self, plan):
        self.plan = plan
        return RetrievalResult(
            status=RetrievalStatus.EMPTY,
            plan_id=plan.plan_id,
            index_profile=profile(),
        )


class RunRetrievalTests(unittest.TestCase):
    def test_cli_stops_at_top_40_candidate_pool_without_rerank(self) -> None:
        retriever = FakeCandidatePoolRetriever()

        def factory(binding, config):
            self.assertEqual(binding.profile, profile())
            self.assertEqual(config.retrieval_version, "v1")
            return retriever

        with tempfile.TemporaryDirectory() as temporary:
            output = StringIO()
            with (
                patch.object(
                    run_retrieval,
                    "load_retrieval_index_binding",
                    return_value=SimpleNamespace(profile=profile()),
                ),
                redirect_stdout(output),
            ):
                status = run_retrieval.main(
                    [
                        "周杰伦《晴天》的创作背景",
                        "--index-manifest",
                        "data/indexing/manifest.json",
                        "--no-llm",
                    ],
                    project_root=Path(temporary),
                    retrieval_factory=factory,
                )

        payload = json.loads(output.getvalue())
        self.assertEqual(status, 0)
        self.assertEqual(payload["pipeline_stage"], "candidate_pool")
        self.assertEqual(payload["candidate_pool_limit"], 40)
        self.assertFalse(payload["rerank_applied"])
        self.assertIsNotNone(retriever.plan)
        self.assertEqual(retriever.plan.index_profile, profile())

    def test_cli_reports_binding_errors_without_starting_retrieval(self) -> None:
        output = StringIO()
        with (
            patch.object(
                run_retrieval,
                "load_retrieval_index_binding",
                side_effect=ValueError("retrieval_index_manifest_invalid"),
            ),
            redirect_stdout(output),
        ):
            status = run_retrieval.main(
                ["晴天", "--index-manifest", "missing.json", "--no-llm"]
            )

        self.assertEqual(status, 1)
        self.assertEqual(
            json.loads(output.getvalue()),
            {"status": "failed", "error": "retrieval_index_manifest_invalid"},
        )


if __name__ == "__main__":
    unittest.main()
