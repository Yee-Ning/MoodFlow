"""Task-local isolated validation; no live env file or production services."""
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import time
from collections import Counter

ROOT = Path(__file__).resolve().parents[3]
SERVICE = ROOT / "ai-service"
os.chdir(SERVICE)
sys.path.insert(0, str(SERVICE))
sys.path.append(str(Path(sys.executable).resolve().parents[2] / ".venv-eval/Lib/site-packages"))
if (SERVICE / ".env").exists():
    raise RuntimeError("Validation worktree must not contain a live env file")
os.environ.update(
    OPENBLAS_NUM_THREADS="1", OMP_NUM_THREADS="1", PYTHONUTF8="1", PYTHONIOENCODING="utf-8",
    PYTEST_DISABLE_PLUGIN_AUTOLOAD="1", PERSISTENCE_BACKEND="sqlite",
    AI_CONTINUITY_STORE_MODE="sqlite", MEMORY_BACKEND="memory", CHECKPOINT_BACKEND="memory",
    DATABASE_URL="postgresql://postgres@127.0.0.1:15483/agent_pack_e_test",
    JEV_ENABLED="false", DEEPEVAL_DISABLE_DOTENV="1", AI_RAG_RETRIEVAL_MODE="empty",
    LLM_API_KEY="offline-test-placeholder", LLM_BASE_URL="https://offline.invalid/v1",
    LLM_MODEL="test", LLM_GENERATION_MODEL="deepseek-flash", LLM_TIMEOUT_SECONDS="1",
    SPRING_BOOT_BASE_URL="http://127.0.0.1:8080", SPRING_BOOT_TIMEOUT_SECONDS="1",
)
for name in ("RUN_LIVE_POSTGRES_SMOKE", "RUN_WORLDLINE_POSTGRES_RESTART", "RUN_LIVE_JEV", "RUN_REAL_QDRANT"):
    os.environ.pop(name, None)

def frozen_sources():
    paths = subprocess.check_output(
        ["git", "ls-tree", "-r", "--name-only", "d7aff705", "--", "ai-service/app", "ai-service/tests"],
        cwd=ROOT, text=True,
    ).splitlines()
    paths.extend(str(path.relative_to(ROOT)).replace("\\", "/")
                 for directory in (SERVICE / "tests", SERVICE / "evals")
                 for path in directory.rglob("*.py"))
    return {name: hashlib.sha256((ROOT / name).read_bytes()).hexdigest()
            for name in paths if (ROOT / name).is_file()}

class Counts:
    def __init__(self):
        self.counts = Counter()
        self.failed = []
        self.skipped = []
        self.by_file = Counter()

    def pytest_runtest_logreport(self, report):
        if type(report).__name__.lower() == "subtestreport":
            self.counts["subtests_" + report.outcome] += 1
        elif report.when == "call":
            self.counts[report.outcome] += 1
            if report.passed:
                self.by_file[report.nodeid.split("::")[0]] += 1
        elif report.failed:
            self.counts["errors"] += 1
        elif report.skipped:
            self.counts["skipped"] += 1
        if report.failed:
            self.failed.append(report.nodeid)
        if report.skipped:
            self.skipped.append({"nodeid": report.nodeid, "reason": str(report.longrepr[-1])
                                 if isinstance(report.longrepr, tuple) else str(report.longrepr)})

    def pytest_warning_recorded(self, warning_message, when, nodeid, location):
        self.counts["warnings"] += 1

import pytest  # noqa: E402 -- Install the offline environment before collection.

result_path = Path(sys.argv[1]).resolve()
args = ["-p", "pytest_asyncio.plugin", *sys.argv[2:]]
if "--legacy-smoke-only" in args:
    args.remove("--legacy-smoke-only")
    expected = "tests/test_live_postgres_smoke.py::LivePostgresSmokeTests::test_checkpoint_history_and_handoff_survive_process_restart"
    if expected not in args or any(arg.startswith("tests/") and arg != expected for arg in args):
        raise RuntimeError("Legacy smoke opt-in is restricted to the single offline restart case")
    os.environ["RUN_LIVE_POSTGRES_SMOKE"] = "1"
    from app.agent.postgres_migrations import run_all_migrations
    run_all_migrations(os.environ["DATABASE_URL"], include_memory=False)
counts = Counts()
before = frozen_sources()
started = time.monotonic()
code = int(pytest.main(args, plugins=[counts]))
after = frozen_sources()
result = {
    "schema_version": 1, "merge_baseline": "d7aff705", "exit_code": code,
    "elapsed_seconds": round(time.monotonic() - started, 2), "args": args,
    "counts": dict(counts.counts), "failed_nodeids": counts.failed,
    "skipped": counts.skipped, "passed_by_file": dict(counts.by_file),
    "source_frozen": before == after, "source_hashes": before,
}
result_path.parent.mkdir(parents=True, exist_ok=True)
result_path.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
print(json.dumps({key: value for key, value in result.items()
                  if key not in {"source_hashes", "passed_by_file", "skipped"}}, ensure_ascii=False))
raise SystemExit(code if before == after else 1)
