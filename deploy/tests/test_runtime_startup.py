"""Exercise the production import path without pytest's shared module cache."""
import os
import subprocess
import sys
from pathlib import Path


def test_production_index_imports_in_fresh_process_and_fails_closed(tmp_path):
    root = Path(__file__).resolve().parents[2]
    environment = {**os.environ, "PYTHONPATH": os.pathsep.join((str(root), str(root / "ai-service"))),
                   "JEV_ENABLED": "false", "AI_RAG_RETRIEVAL_MODE": "empty", "MEMORY_BACKEND": "disabled"}
    script = """
import asyncio
import sys
from pathlib import Path
from deploy.runtime import validate_index
try:
    asyncio.run(validate_index(Path(sys.argv[1])))
except ValueError as exc:
    if not str(exc).startswith('artifact_missing_or_changed:'):
        raise
    print('startup_imports_passed_and_artifacts_rejected')
else:
    raise AssertionError('Missing frozen artifacts must fail closed')
"""
    result = subprocess.run([sys.executable, "-B", "-c", script, str(tmp_path)], cwd=root,
                            env=environment, capture_output=True, text=True, timeout=90)
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "startup_imports_passed_and_artifacts_rejected"
