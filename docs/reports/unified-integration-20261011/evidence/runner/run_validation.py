"""Isolated test runner; no dotenv, production services, or live model calls."""

import json
import os
from pathlib import Path
import sys
import time
import traceback

os.environ.update(
    PYTEST_DISABLE_PLUGIN_AUTOLOAD="1", PYTHONDONTWRITEBYTECODE="1",
    PYTHONUTF8="1", PYTHONIOENCODING="utf-8", OPENBLAS_NUM_THREADS="1",
    OMP_NUM_THREADS="1", PERSISTENCE_BACKEND="sqlite",
    AI_CONTINUITY_STORE_MODE="sqlite", MEMORY_BACKEND="memory",
    CHECKPOINT_BACKEND="memory", JEV_ENABLED="false",
    DEEPEVAL_DISABLE_DOTENV="1", AI_RAG_RETRIEVAL_MODE="empty",
    BUDGET_LEDGER_BACKEND="history", SESSION_CACHE_BACKEND="none",
    CONTEXT_DERIVED_CACHE_BACKEND="none", PROFILE_CACHE_BACKEND="none",
    DEDUP_LOCK_BACKEND="none",
    DATABASE_URL="postgresql://postgres@127.0.0.1:59916/coldchain_baseline",
)
sys.path.insert(0, str(Path.cwd()))
sys.path.insert(0, str(Path.cwd().parent))
sys.path.append(r"D:\code\ProjectsAll\MoodFlow\ai-service\.venv-eval\Lib\site-packages")
from app.config.settings import Settings

Settings.model_config["env_file"] = None
prefix = Settings.model_config.get("env_prefix", "")
for name, value in {
    "api_key": "unit-test-placeholder", "base_url": "http://127.0.0.1:9/v1",
    "model": "unit-test-model", "timeout_seconds": "5",
    "spring_boot_base_url": "http://127.0.0.1:9", "spring_boot_timeout_seconds": "5",
}.items():
    alias = Settings.model_fields[name].validation_alias
    os.environ[(alias if isinstance(alias, str) else prefix + name).upper()] = value
Settings()

import app
import pytest

phase, *targets = sys.argv[1:]
missing_targets = [target for target in targets if not Path(target.split("::", 1)[0]).exists()]
if missing_targets:
    raise SystemExit("Missing test targets: " + ", ".join(missing_targets))
directory = Path(__file__).resolve().parent
started = time.monotonic()
os.environ["COLDCHAIN_TEST_PHASE"] = phase
os.environ["COLDCHAIN_TEST_EVIDENCE_DIR"] = str(directory)
sys.path.insert(0, str(directory))
os.environ["PYTHONPATH"] = os.pathsep.join([str(Path.cwd()), str(Path.cwd().parent), str(directory)])


class Tee:
    def __init__(self, stream, logfile):
        self.stream, self.logfile = stream, logfile

    def write(self, value):
        self.logfile.write(value)
        self.logfile.flush()
        os.fsync(self.logfile.fileno())
        return self.stream.write(value)

    def flush(self):
        self.logfile.flush()
        self.stream.flush()

    def __getattr__(self, name):
        return getattr(self.stream, name)


log = (directory / (phase + ".log")).open("w", encoding="utf-8", buffering=1)
sys.stdout, sys.stderr = Tee(sys.stdout, log), Tee(sys.stderr, log)


class Summary:
    def pytest_sessionfinish(self, session, exitstatus):
        reporter = session.config.pluginmanager.get_plugin("terminalreporter")
        stats = reporter.stats
        summary = {
            "phase": phase, "exit_code": int(exitstatus),
            "elapsed_seconds": round(time.monotonic() - started, 2),
            "counts": {name: len(values) for name, values in stats.items()},
            "failed_nodeids": [report.nodeid for report in stats.get("failed", [])],
            "skipped_nodeids": [report.nodeid for report in stats.get("skipped", [])],
            "code_root": str(app.__file__), "targets": targets,
        }
        (directory / (phase + ".json")).write_text(
            json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8"
        )


print("TEST_CODE_ROOT=" + str(app.__file__), flush=True)
try:
    result = pytest.main([
        "-p", "pytest_asyncio.plugin", "-p", "xdist.plugin", "-p", "no:cacheprovider",
        "-p", "durable_evidence", "-n", "4", "--dist", "loadfile", "-q",
        "--disable-warnings", "--tb=long", *targets,
    ], plugins=[Summary()])
except BaseException:
    traceback.print_exc()
    log.flush()
    os.fsync(log.fileno())
    raise
print("VALIDATION_EXIT_CODE=" + str(int(result)), flush=True)
log.flush()
os.fsync(log.fileno())
raise SystemExit(result)
