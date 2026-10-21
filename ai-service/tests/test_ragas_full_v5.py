"""Live scope and existing production failure boundaries, no API calls."""

import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import httpx

from evals import ragas_full_v5 as full


class FullRequestTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.recorder = full.FullRecorder(Path(self.directory.name))

    async def test_all_50_including_needs_input_are_in_scope_but_not_extra_cases(self):
        rows = full.gate0.verify_inputs()
        with full.smoke_scope(rows):
            scope_token = full.smoke.SCOPE.set({"stage": "judge", "case_id": "rag-v0-048", "metric": "faithfulness"})
            try:
                await self.recorder.request(httpx.Request("POST", "https://api.deepseek.com/v1/chat/completions",
                    json={"model": "deepseek-chat", "temperature": 0}))
                full.smoke.SCOPE.set({"stage": "judge", "case_id": "not-in-gold"})
                with self.assertRaisesRegex(ValueError, "unapproved_provider"):
                    await self.recorder.request(httpx.Request("POST", "https://api.deepseek.com/v1/chat/completions",
                        json={"model": "deepseek-chat", "temperature": 0}))
            finally:
                full.smoke.SCOPE.reset(scope_token)
        self.assertEqual(len(self.recorder.entries), 1)

    async def test_nonzero_or_boolean_temperature_is_rejected(self):
        for value in (0.01, 1, False, None):
            with self.subTest(value=value), self.assertRaisesRegex(ValueError, "numeric_zero"):
                await self.recorder.request(httpx.Request("POST", "https://api.deepseek.com/v1/chat/completions",
                    json={"model": "deepseek-chat", "temperature": value}))
        self.assertEqual(self.recorder.entries, [])

    async def test_third_generation_call_is_blocked_before_serialization(self):
        token = full.smoke.SCOPE.set({"stage": "generation", "case_id": "rag-v0-001"})
        self.addCleanup(full.smoke.SCOPE.reset, token)
        self.recorder.entries = [{"stage": "generation", "case_id": "rag-v0-001"}] * 2
        with self.assertRaisesRegex(ValueError, "no_more_than_two"):
            await self.recorder.request(httpx.Request("POST", "https://api.deepseek.com/responses",
                json={"temperature": 0}))


class FullFailureTests(unittest.TestCase):
    def test_schema_code_is_recorded_without_changing_production_diagnostic(self):
        from app.agent.structured_output.errors import FailureType, StructuredOutputError, ValidationFailure
        from app.agent.foundation.subgraphs.worldline.generation import WorldLineGenerationError
        cause = ValidationFailure(FailureType.SCHEMA_VALIDATION_ERROR, "extra_forbidden:narrative_note")
        terminal = StructuredOutputError("worldline_narrative", FailureType.RETRY_EXHAUSTED,
            attempts=2, last_failure_type=FailureType.SCHEMA_VALIDATION_ERROR)
        terminal.__cause__ = cause
        error = WorldLineGenerationError("worldline_narrative_invalid")
        error.__cause__ = terminal
        result = full.terminal_detail(error)
        self.assertEqual(result["production_validation_code"], "unclassified")
        self.assertEqual(result["validation_code"], "extra_forbidden:narrative_note")
        self.assertEqual(result["attempts"], 2)
        self.assertEqual(result["failure_type"], "schema_validation_error")

    def test_evaluation_invariant_is_never_labeled_production_failure(self):
        error = RuntimeError()
        error.__cause__ = ValueError("gate1_actual_outbound_temperature_not_zero")
        with self.assertRaisesRegex(RuntimeError, "evaluation_invariant"):
            full.terminal_detail(error)

    def test_preparation_preserves_gate0_input_bytes_before_live_calls(self):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "prepared"
            with patch.object(full, "OUTPUT", output):
                full.prepare()
                self.assertEqual((output / "inputs.json").read_bytes(),
                                 (full.gate0.OUTPUT / "inputs.json").read_bytes())
                preparation = json.loads((output / "preparation.json").read_text())
                self.assertEqual(len(preparation["case_ids"]), 50)
                self.assertEqual(list((output / "answers").glob("*.json")), [])
                with self.assertRaises(FileExistsError):
                    full.prepare()


if __name__ == "__main__":
    unittest.main()
