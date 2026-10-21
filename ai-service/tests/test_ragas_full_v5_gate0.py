"""All-50 inclusion, immutable evidence and three-case offline boundaries."""

import copy
import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from evals import ragas_full_v5_gate0 as gate
from evals.ragas_gate0 import Gate0ForbiddenOperation, Gate0Guard, read_jsonl


class FullGate0Tests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.gold = read_jsonl(gate.GOLD)
        cls.baseline = read_jsonl(gate.STEP2 / "cases.jsonl")
        cls.projection = read_jsonl(gate.PROJECTION)
        cls.rows = gate.build_inputs(cls.gold, cls.baseline, cls.projection)

    def test_population_is_all_frozen_rows_including_seven_needs_input(self):
        self.assertEqual([r["case_id"] for r in self.rows], [r["case_id"] for r in self.gold])
        self.assertEqual(sum(r["answerability"] == "needs_input" for r in self.rows), 7)
        for row, frozen in zip(self.rows, self.gold, strict=True):
            self.assertEqual(row["question"], frozen["question"])
            self.assertEqual(row["ground_truth"], frozen["ground_truth"])
            self.assertEqual(row["answerability"], frozen["answerability"])
            self.assertIsNone(row["answer"])

    def test_all_600_contexts_are_exact_ranked_child_text_not_parent_text(self):
        by_child = {r["chunk_id"]: r for r in self.projection}
        baseline = {r["case_id"]: r for r in self.baseline}
        for row in self.rows:
            self.assertEqual(len(row["contexts"]), 12)
            self.assertEqual([p["child_id"] for p in row["provenance"]],
                             [h["child_id"] for h in baseline[row["case_id"]]["rerank_top12"]])
            for text, provenance in zip(row["contexts"], row["provenance"], strict=True):
                child = by_child[provenance["child_id"]]
                self.assertEqual(text, child["payload"]["content"])
                self.assertEqual(provenance["parent_id"], child["parent_id"])
                self.assertEqual(hashlib.sha256(text.encode()).hexdigest(), provenance["content_sha256"])

    def test_omitted_duplicate_or_relabeled_population_is_rejected(self):
        relabeled = copy.deepcopy(self.gold)
        relabeled[0]["answerability"] = "needs_input"
        duplicate = [*self.gold[:-1], self.gold[0]]
        for population in (self.gold[:-1], duplicate, relabeled):
            with self.subTest(count=len(population)), self.assertRaisesRegex(ValueError, "population"):
                gate.build_inputs(population, self.baseline, self.projection)

    def test_unhealthy_or_changed_retrieval_mapping_is_rejected(self):
        for mutation in ("healthy", "parent"):
            baseline = copy.deepcopy(self.baseline)
            if mutation == "healthy":
                baseline[0]["healthy"] = False
            else:
                baseline[0]["rerank_top12"][0]["parent_id"] = "unrecognized-parent"
            with self.subTest(mutation=mutation), self.assertRaises(ValueError):
                gate.build_inputs(self.gold, baseline, self.projection)

    def test_existing_inputs_cannot_be_overwritten(self):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory)
            frozen = output / "inputs.json"
            frozen.write_text("frozen-test-marker", encoding="utf-8")
            with patch.object(gate, "OUTPUT", output), self.assertRaisesRegex(ValueError, "already_frozen"):
                gate.prepare()
            self.assertEqual(frozen.read_text(), "frozen-test-marker")

    def test_input_byte_tampering_is_rejected_before_generation(self):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory)
            (output / "preparation.json").write_text(json.dumps({"inputs_sha256": "original-digest"}))
            (output / "inputs.json").write_text("[]")
            with patch.object(gate, "OUTPUT", output), self.assertRaisesRegex(ValueError, "inputs_modified"):
                gate.verify_inputs()

    def test_mock_saved_requests_pass_and_evidence_loss_or_extra_case_fails(self):
        dry = gate.read_json(gate.OUTPUT / "dry_run.json")
        results = [{**c, "answer": c["mock_answer"], "generation_status": "success"} for c in dry["cases"]]
        calls = [gate.read_json(p) for p in sorted((gate.OUTPUT / "mock_generation_calls").glob("*.json"))]
        self.assertEqual(len(gate.check_dry_run(self.rows, results, calls)), 3)
        changed = copy.deepcopy(calls)
        payload = json.loads(changed[0]["request"]["input"][0]["content"])
        payload["sources"].pop()
        changed[0]["request"]["input"][0]["content"] = json.dumps(payload)
        with self.assertRaisesRegex(ValueError, "protocol_mismatch"):
            gate.check_dry_run(self.rows, results, changed)
        with self.assertRaisesRegex(ValueError, "population_or_attempt_count"):
            gate.check_dry_run(self.rows, results, [*calls, calls[0]])

    def test_gate0_guard_blocks_provider_qdrant_and_secret_access(self):
        guard = Gate0Guard()
        for event, args in [("socket.connect", (object(), ("api.deepseek.com", 443))),
                            ("socket.connect", (object(), ("127.0.0.1", 6333))),
                            ("open", ("ai-service/.env",)), ("open", ("secret/key",))]:
            with self.subTest(event=event, args=str(args)), self.assertRaises(Gate0ForbiddenOperation):
                guard.audit(event, args)

    def test_real_bug_is_still_present_in_saved_smoke_failure(self):
        failure = gate.read_json(gate.smoke.OUTPUT / "answers/rag-v0-006.json")
        self.assertEqual(failure["generation_status"], "failed_production_fallback")
        self.assertEqual(failure["generation_failure"]["validation_code"], "paragraph_missing_citation")
        sources = gate.verify_sources()
        self.assertGreater(sources["smoke_artifacts_verified"], 90)

    def test_finalize_refuses_an_incomplete_or_nonzero_call_dry_run(self):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory)
            for name in ("environment.json", "dry_run.json"):
                (output / name).write_bytes((gate.OUTPUT / name).read_bytes())
            calls = output / "mock_generation_calls"
            calls.mkdir()
            for source in (gate.OUTPUT / "mock_generation_calls").glob("*.json"):
                (calls / source.name).write_bytes(source.read_bytes())
            for field, value in (("status", "failed"), ("real_api_calls", 1), ("credentials_loaded", True)):
                dry = gate.read_json(gate.OUTPUT / "dry_run.json")
                dry[field] = value
                (output / "dry_run.json").write_text(json.dumps(dry), encoding="utf-8")
                with (self.subTest(field=field), patch.object(gate, "OUTPUT", output),
                      patch.object(gate, "verify_inputs", return_value=self.rows),
                      self.assertRaisesRegex(ValueError, "gate0_incomplete")):
                    gate.finalize()
                self.assertFalse((output / "run_manifest.json").exists())


if __name__ == "__main__":
    unittest.main()
