"""Opt-in, real PostgreSQL checkpoint recovery across three processes."""

import json
import os
import subprocess
import sys
import unittest
from pathlib import Path

from app.config.settings import load_runtime_environment


@unittest.skipUnless(
    os.getenv("RUN_WORLDLINE_POSTGRES_RESTART") == "1",
    "set RUN_WORLDLINE_POSTGRES_RESTART=1 for real PostgreSQL restart acceptance",
)
class WorldLinePostgresRestartTests(unittest.TestCase):
    def test_create_restart_explore_restart_restore(self):
        load_runtime_environment()
        self.assertTrue(os.getenv("DATABASE_URL"))
        environment = {
            **os.environ,
            "CHECKPOINT_BACKEND": "postgres",
            "PYTHONPATH": str(Path(__file__).resolve().parents[1]),
        }

        def process(mode, *args):
            completed = subprocess.run(
                [sys.executable, "tests/fixtures/worldline_postgres_restart_worker.py", mode, *args],
                env=environment, capture_output=True, text=True, check=False,
            )
            self.assertEqual(completed.returncode, 0, f"WorldLine restart worker failed in {mode} mode")
            return json.loads(completed.stdout.strip().splitlines()[-1])

        created = process("create")
        self.assertEqual(created["revision"], 1)
        expanded = process("explore", created["session_id"], created["branch_id"])
        self.assertEqual(expanded["before_revision"], 1)
        self.assertEqual(expanded["revision"], 2)
        self.assertEqual(expanded["depth"], 1)
        self.assertEqual(expanded["root"], created["root"])
        self.assertTrue(set(created["nodes"]).issubset(expanded["nodes"]))
        self.assertTrue(set(created["sources"]).issubset(expanded["sources"]))
        self.assertIn("netease:song:4", expanded["nodes"])
        recovered = process("verify", created["session_id"])
        self.assertEqual(recovered["revision"], 2)
        self.assertEqual(recovered["root"], created["root"])
        self.assertEqual(recovered["nodes"], expanded["nodes"])
        self.assertEqual(recovered["sources"], expanded["sources"])
        self.assertIn("netease:artist:2", recovered["visited"])

    def test_same_branch_cannot_commit_twice_across_processes(self):
        load_runtime_environment()
        environment = {
            **os.environ,
            "CHECKPOINT_BACKEND": "postgres",
            "PYTHONPATH": str(Path(__file__).resolve().parents[1]),
        }
        worker = "tests/fixtures/worldline_postgres_restart_worker.py"
        created_process = subprocess.run(
            [sys.executable, worker, "create"], env=environment,
            capture_output=True, text=True, check=False,
        )
        self.assertEqual(created_process.returncode, 0, "WorldLine create worker failed")
        created = json.loads(created_process.stdout.strip().splitlines()[-1])
        args = [sys.executable, worker, "explore", created["session_id"], created["branch_id"]]
        workers = [subprocess.Popen(args, env=environment, stdout=subprocess.PIPE,
                                    stderr=subprocess.PIPE, text=True) for _ in range(2)]
        results = []
        for worker_process in workers:
            stdout, _ = worker_process.communicate(timeout=60)
            results.append((worker_process.returncode, json.loads(stdout.strip().splitlines()[-1])))
        self.assertEqual(sum(code == 0 for code, _ in results), 1)
        failed = next(payload for code, payload in results if code != 0)
        self.assertEqual(failed["error_chain"][0]["code"], "duplicate_exploration")
