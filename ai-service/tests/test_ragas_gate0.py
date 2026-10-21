"""Gate 0 tests use stdlib unittest in .venv-ragas; no production conftest imports."""

import copy
import hashlib
import json
import math
from pathlib import Path
import unittest

from evals.ragas_gate0 import (
    Gate0ForbiddenOperation, Gate0Guard, OUTPUT, PLACEHOLDER,
    configure_offline, load_sample, normalize_scores, prepare_sample,
)


def fixtures():
    gold = [{"case_id": "case", "question": "question", "ground_truth": "reference"}]
    ranking, projection = [], []
    for i in range(12):
        text = f"child text {i}"
        ranking.append({"rank": i + 1, "child_id": f"c{i}", "parent_id": f"p{i}",
                        "point_id": str(i), "source_id": "source", "rerank_score": 0.5})
        projection.append({"chunk_id": f"c{i}", "parent_id": f"p{i}", "point_id": str(i),
                           "payload": {"parent_id": f"p{i}", "content": text,
                                       "source_content_sha256": hashlib.sha256(text.encode()).hexdigest()}})
    baseline = [{"case_id": "case", "question": "question", "healthy": True, "rerank_top12": ranking}]
    return gold, baseline, projection


class Gate0DataTests(unittest.TestCase):
    def test_top12_keeps_order_and_exact_raw_child_text(self):
        gold, baseline, projection = fixtures()
        sample, provenance = prepare_sample(gold, baseline, projection, "case")
        self.assertEqual(sample["contexts"], [f"child text {i}" for i in range(12)])
        self.assertEqual(sample["answer"], PLACEHOLDER)
        self.assertEqual(sample["ground_truth"], "reference")
        self.assertEqual(len(provenance), 12)

    def test_mutated_child_text_is_rejected(self):
        gold, baseline, projection = fixtures()
        projection[0]["payload"]["content"] = "changed"
        with self.assertRaisesRegex(ValueError, "content_hash"):
            prepare_sample(gold, baseline, projection, "case")

    def test_mismatched_parent_is_rejected(self):
        gold, baseline, projection = fixtures()
        baseline[0]["rerank_top12"][0]["parent_id"] = "wrong"
        with self.assertRaisesRegex(ValueError, "identity_mismatch"):
            prepare_sample(gold, baseline, projection, "case")

    def test_incomplete_top12_is_rejected(self):
        gold, baseline, projection = fixtures()
        baseline[0]["rerank_top12"].pop()
        with self.assertRaisesRegex(ValueError, "population_mismatch"):
            prepare_sample(gold, baseline, projection, "case")

    def test_question_drift_is_rejected(self):
        gold, baseline, projection = fixtures()
        baseline[0]["question"] = "rewritten"
        with self.assertRaisesRegex(ValueError, "question_or_health"):
            prepare_sample(gold, baseline, projection, "case")

    def test_duplicate_case_is_rejected(self):
        gold, baseline, projection = fixtures()
        gold.append(copy.deepcopy(gold[0]))
        with self.assertRaisesRegex(ValueError, "duplicate_case"):
            prepare_sample(gold, baseline, projection, "case")

    def test_nan_is_null_with_reason_and_zero_is_not_missing(self):
        result = normalize_scores({"missing": math.nan, "zero": 0.0})
        self.assertEqual(result["missing"], {"value": None, "status": "expected_nan_judge_disabled"})
        self.assertEqual(result["zero"], {"value": 0.0, "status": "finite"})
        self.assertNotIn("NaN", json.dumps(result, allow_nan=False))

    def test_guard_blocks_network_subprocess_and_secret_files(self):
        for event, args in [("socket.connect", ()), ("socket.getaddrinfo", ()),
                            ("socket.sendto", ()), ("subprocess.Popen", ()),
                            ("open", (Path("secret/key.txt"),)),
                            ("open", (".env",)), ("open", (".env.production",))]:
            with self.subTest(event=event, args=args):
                guard = Gate0Guard()
                with self.assertRaises(Gate0ForbiddenOperation):
                    guard.audit(event, args)
                self.assertEqual(len(guard.denied), 1)

    def test_guard_allows_local_dataset_and_runtime_files(self):
        guard = Gate0Guard()
        guard.audit("open", ("data/projection.jsonl",))
        guard.audit("open", ("site-packages/ragas/evaluation.py",))
        guard.audit("open", (5,))
        self.assertEqual(guard.denied, [])

    def test_guard_does_not_allow_arbitrary_localhost_api_connections(self):
        guard = Gate0Guard()
        with self.assertRaises(Gate0ForbiddenOperation):
            guard.audit("socket.connect", (object(), ("127.0.0.1", 6333)))
        self.assertEqual(guard.local_socketpairs, 0)

    def test_real_frozen_one_case_input(self):
        sample, provenance = load_sample()
        self.assertEqual(len(sample["contexts"]), 12)
        self.assertEqual(provenance[0]["rank"], 1)
        self.assertEqual(sample["answer"], PLACEHOLDER)

    def test_saved_gate0_contexts_exactly_match_frozen_inputs(self):
        expected, provenance = load_sample()
        saved = json.loads((OUTPUT / "four_columns.jsonl").read_text(encoding="utf-8"))
        self.assertEqual(saved, expected)
        for context, evidence in zip(saved["contexts"], provenance, strict=True):
            self.assertEqual(hashlib.sha256(context.encode()).hexdigest(), evidence["content_sha256"])

    def test_saved_dry_run_is_not_misrepresented_as_quality_scores(self):
        manifest = json.loads((OUTPUT / "run_manifest.json").read_text(encoding="utf-8"))
        result = json.loads((OUTPUT / "dry_run.json").read_text(encoding="utf-8"))
        contract = json.loads((OUTPUT / "api_contract.json").read_text(encoding="utf-8"))
        self.assertFalse(result["scores_are_quality_measurements"])
        self.assertTrue(manifest["protected_unchanged"])
        self.assertFalse(manifest["gate1_authorized"])
        self.assertEqual(manifest["case_count"], 1)
        self.assertEqual(manifest["real_judge_calls"], 0)
        self.assertEqual(manifest["secret_file_reads"], 0)
        self.assertEqual(manifest["network_denials"], 0)
        self.assertEqual(len(result["scores"]), 2)
        self.assertTrue(all(s["value"] is None for s in result["scores"].values()))
        self.assertFalse(contract["metrics"]["factual_correctness"]["requires_embeddings"])
        self.assertTrue(contract["metrics"]["answer_correctness"]["requires_embeddings"])


if __name__ == "__main__":
    configure_offline()
    unittest.main()
