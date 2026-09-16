import hashlib
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import run_rag_gold_evidence_pack
from app.rag.evaluation.gold_evidence import (
    SourceParent,
    build_source_candidates,
    candidate_records,
    load_frozen_parents,
    render_source_review_pack,
)
from app.rag.evaluation.io import write_jsonl
from app.rag.evaluation.models import AnnotationStatus, ExpectedOutcome, RAGEvaluationCase


def pending_case() -> RAGEvaluationCase:
    return RAGEvaluationCase(
        case_id="gold-001",
        query="《晴天》的创作背景",
        expected_outcome=ExpectedOutcome.ANSWERABLE,
    )


def parents() -> list[SourceParent]:
    return [
        SourceParent(
            parent_id=f"parent-{index}",
            doc_id=f"doc-{index}",
            source_id=f"source-{index // 2}",
            source_title="晴天采访",
            source_name="档案馆",
            source_url="https://example.org/source",
            source_document_path=f"data/normalized/source-{index}/document.json",
            breadcrumb="晴天 > 创作背景",
            content=f"《晴天》的创作背景来自音乐采访第 {index} 段，介绍歌曲制作。",
        )
        for index in range(7)
    ]


def source_row(index: int) -> dict[str, object]:
    source_id = f"source-{index}"
    return {
        "doc_id": f"doc-{index}",
        "chunk_id": f"parent-{index}",
        "parent_id": None,
        "breadcrumb": "晴天 > 创作背景",
        "content": f"《晴天》创作背景原文第 {index} 段。",
        "metadata": {
            "source_id": source_id,
            "source": "档案馆",
            "normalized_path": f"data/normalized/{source_id}/document.json",
            "document_metadata": {
                "source_metadata": {
                    "source_id": source_id,
                    "title": "晴天采访",
                    "publisher": "档案馆",
                    "url": "https://example.org/source",
                }
            },
        },
    }


class GoldEvidenceTests(unittest.TestCase):
    def test_selects_five_from_source_parents_and_preserves_exact_spans(self) -> None:
        selected = build_source_candidates([pending_case()], parents())["gold-001"]
        self.assertEqual(len(selected), 5)
        self.assertEqual(len({item.parent_id for item in selected}), 5)
        self.assertEqual(
            [item.source_id for item in selected],
            sorted(item.source_id for item in selected),
        )
        by_id = {item.parent_id: item for item in parents()}
        for item in selected:
            self.assertIn(item.excerpt, by_id[item.parent_id].content)
            self.assertIn(item.suggested_supporting_span, by_id[item.parent_id].content)
            self.assertEqual(item.source_id, by_id[item.parent_id].source_id)
        records = candidate_records(
            [pending_case()], {"gold-001": selected},
            release_id="release-v1", source_sha256="sha256:" + "a" * 64,
        )
        self.assertNotIn("expected_outcome", records[0])
        self.assertNotIn("score", json.dumps(records))
        self.assertNotIn("rank", json.dumps(records))
        pack = render_source_review_pack(
            [pending_case()], {"gold-001": selected},
            release_id="release-v1", source_sha256="sha256:" + "a" * 64,
        )
        self.assertIn("APPROVE / CORRECT / REJECT", pack)
        self.assertIn("required_facts", pack)
        self.assertIn("reference_answer", pack)
        self.assertIn("初判后才展开待审骨架中的候选值", pack)
        self.assertIn("[查看原文](../../data/normalized/", pack)
        self.assertNotIn("旧 Top3 的 Parent ID", pack)

    def test_candidate_selection_does_not_consume_seed_outcome(self) -> None:
        answerable = pending_case()
        needs_input = answerable.model_copy(
            update={"expected_outcome": ExpectedOutcome.NEEDS_INPUT}
        )
        first = build_source_candidates([answerable], parents())["gold-001"]
        second = build_source_candidates([needs_input], parents())["gold-001"]
        self.assertEqual(first, second)

    def test_rejects_prefilled_or_approved_case(self) -> None:
        prefilled = pending_case().model_copy(update={"relevant_parent_ids": ["old-parent"]})
        with self.assertRaisesRegex(ValueError, "prefilled evidence labels"):
            build_source_candidates([prefilled], parents())
        approved = pending_case().model_copy(update={"annotation_status": AnnotationStatus.APPROVED})
        with self.assertRaisesRegex(ValueError, "not pending"):
            build_source_candidates([approved], parents())

    def test_rejects_too_few_distinct_sources(self) -> None:
        same_source = [
            parent.__class__(**{**parent.__dict__, "source_id": "one-source"})
            for parent in parents()
        ]
        with self.assertRaisesRegex(ValueError, "insufficient independent source candidates"):
            build_source_candidates([pending_case()], same_source)

    def test_comparison_shows_both_subjects_from_independent_sources(self) -> None:
        source_parents = parents()
        source_parents[0] = SourceParent(
            **{**source_parents[0].__dict__, "content": "《晴天》是抒情歌曲。"}
        )
        source_parents[2] = SourceParent(
            **{**source_parents[2].__dict__, "content": "《夜曲》使用了不同编曲。"}
        )
        comparison = pending_case().model_copy(update={"query": "晴天和夜曲的风格有什么区别"})
        selected = build_source_candidates([comparison], source_parents)["gold-001"]
        chosen = {item.parent_id for item in selected}
        self.assertIn("parent-0", chosen)
        self.assertIn("parent-2", chosen)

    def test_frozen_release_checksum_and_source_lineage(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source_path = root / "knowledge_enriched_chunks.jsonl"
            payload = "\n".join(json.dumps(source_row(index)) for index in range(5)) + "\n"
            source_path.write_text(payload, encoding="utf-8")
            digest = hashlib.sha256(source_path.read_bytes()).hexdigest()
            manifest_path = root / "knowledge_release_manifest.json"
            manifest = {
                "release_id": "release-v1",
                "artifacts": {"knowledge_enriched_chunks.jsonl": f"sha256:{digest}"},
            }
            manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
            release, actual, loaded = load_frozen_parents(manifest_path)
            self.assertEqual(release, "release-v1")
            self.assertEqual(actual, f"sha256:{digest}")
            self.assertEqual(len(loaded), 5)
            self.assertEqual(loaded[0].source_id, "source-0")
            bad_row = source_row(0)
            bad_row["metadata"]["normalized_path"] = "../untrusted.json"
            tampered = "\n".join(
                json.dumps(bad_row if index == 0 else source_row(index))
                for index in range(5)
            ) + "\n"
            source_path.write_text(tampered, encoding="utf-8")
            manifest["artifacts"]["knowledge_enriched_chunks.jsonl"] = (
                "sha256:" + hashlib.sha256(source_path.read_bytes()).hexdigest()
            )
            manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "unsafe Source Document path"):
                load_frozen_parents(manifest_path)
            source_path.write_text(payload + "\n", encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "checksum mismatch"):
                load_frozen_parents(manifest_path)

    def test_cli_writes_separate_pack_without_mutating_pending_gold(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            pending_path = root / "gold.pending.jsonl"
            write_jsonl(pending_path, [pending_case()])
            original = pending_path.read_bytes()
            review_path = root / "review.md"
            candidates_path = root / "candidates.jsonl"
            with patch.object(
                run_rag_gold_evidence_pack,
                "load_frozen_parents",
                return_value=("release-v1", "sha256:" + "a" * 64, parents()),
            ):
                with patch("sys.argv", [
                    "run_rag_gold_evidence_pack.py",
                    "--pending-gold", str(pending_path),
                    "--release-manifest", str(root / "manifest.json"),
                    "--review-pack", str(review_path),
                    "--candidates-jsonl", str(candidates_path),
                ]):
                    run_rag_gold_evidence_pack.main()
            self.assertEqual(pending_path.read_bytes(), original)
            self.assertEqual(len(candidates_path.read_text(encoding="utf-8").splitlines()), 1)
            self.assertIn("parent-", review_path.read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
