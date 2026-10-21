"""Current production replay of the existing eight-case provider trace."""

import asyncio
import unittest

from evals import ragas_full_v5 as run
from evals.ragas_full_v5_saved_generation import replay


class SavedGenerationTests(unittest.TestCase):
    def test_all_eight_smoke_requests_deliveries_and_failures_replay_exactly(self):
        directory = run.smoke.OUTPUT
        rows = run.read_json(directory / "inputs.json")
        results = asyncio.run(replay(directory, rows))
        self.assertEqual(len(results), 8)
        self.assertEqual(sum(r["saved_attempts"] for r in results), 12)
        self.assertEqual(sum(r["generation_status"] == "success" for r in results), 5)


if __name__ == "__main__":
    unittest.main()
