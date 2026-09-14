import json
import tempfile
import unittest
from contextlib import redirect_stdout
from datetime import datetime, timezone
from io import StringIO
from pathlib import Path

from app.rag.indexing.models import IndexBuildManifest
from app.rag.query_understanding import load_index_profile
from run_query_understanding import main as run_query_understanding_main


def manifest(*, ready: bool = True) -> IndexBuildManifest:
    return IndexBuildManifest(
        status="completed" if ready else "partial",
        ready_for_retrieval=ready,
        created_at=datetime.now(timezone.utc),
        indexing_version="v1",
        schema_version="v2",
        projection_version="v2",
        source_path="data/knowledge/v2/knowledge.jsonl",
        source_fingerprint="sha256:" + "a" * 64,
        source_release_id="knowledge-v2-testrelease",
        source_release_manifest_path="data/knowledge/v2/release.json",
        source_release_manifest_fingerprint="sha256:" + "b" * 64,
        config_fingerprint="sha256:" + "c" * 64,
        projection_path="data/indexing/v1/projection.jsonl",
        projection_fingerprint="sha256:" + "d" * 64,
        collection_name="music_knowledge_test",
        dense_vector_name="dense",
        dense_model="test/model",
        dense_model_revision="abcdef0",
        dense_dimension=32,
        dense_distance="cosine",
        dense_normalized=True,
        bm25_vector_name="bm25",
        bm25_model="qdrant/bm25",
        bm25_options={},
        hnsw_m=16,
        hnsw_ef_construct=100,
        parent_count=1,
        child_count=1,
        eligible_count=1,
        selected_count=1,
        projected_count=1,
        excluded_count=0,
        indexed_count=1 if ready else 0,
        skipped_count=0,
        failed_count=0,
        pruned_count=0,
        collection_point_count=1 if ready else 0,
    )


def write_release(root: Path, value: IndexBuildManifest) -> Path:
    release_path = root / value.source_release_manifest_path
    release_path.parent.mkdir(parents=True)
    release_path.write_text(
        json.dumps(
            {
                "release_id": value.source_release_id,
                "component_versions": {"entity_registry": "v3"},
            }
        ),
        encoding="utf-8",
    )
    manifest_path = root / "data/indexing/v1/manifest.json"
    manifest_path.parent.mkdir(parents=True)
    manifest_path.write_text(value.model_dump_json(), encoding="utf-8")
    return manifest_path


class IndexProfileTests(unittest.TestCase):
    def test_ready_manifest_binds_plan_to_exact_release_versions(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            path = write_release(root, manifest())

            profile = load_index_profile(root, path)

            self.assertEqual(profile.collection_name, "music_knowledge_test")
            self.assertEqual(profile.source_release_id, "knowledge-v2-testrelease")
            self.assertEqual(profile.registry_version, "v3")

    def test_non_ready_manifest_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            path = write_release(root, manifest(ready=False))

            with self.assertRaisesRegex(ValueError, "not_ready"):
                load_index_profile(root, path)

    def test_manifest_outside_project_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as project, tempfile.TemporaryDirectory() as other:
            path = write_release(Path(other), manifest())

            with self.assertRaisesRegex(ValueError, "outside_project"):
                load_index_profile(Path(project), path)

    def test_cli_prints_inspectable_plan_without_llm_or_retrieval(self) -> None:
        output = StringIO()
        with redirect_stdout(output):
            status = run_query_understanding_main(
                ["歌曲《晴天》的创作背景", "--no-llm"]
            )

        payload = json.loads(output.getvalue())
        self.assertEqual(status, 0)
        self.assertEqual(payload["status"], "ready")
        self.assertEqual(
            payload["search_plan"]["entities"][0]["knowledge_entity_id"],
            "song:qing_tian",
        )


if __name__ == "__main__":
    unittest.main()
