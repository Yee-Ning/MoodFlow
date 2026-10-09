"""Run pytest with fixture settings, without reading the project's private .env."""

from contextlib import contextmanager
import io
import os
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]


def disable_project_dotenv():
    from dotenv.main import DotEnv

    original = DotEnv._get_stream
    protected = (ROOT / ".env").resolve()

    @contextmanager
    def fixture_stream(self):
        if self.dotenv_path and Path(self.dotenv_path).resolve() == protected:
            yield io.StringIO("")
        else:
            with original(self) as stream:
                yield stream

    DotEnv._get_stream = fixture_stream


def main():
    args = sys.argv[1:]
    postgres_port = None
    if "--postgres-port" in args:
        index = args.index("--postgres-port")
        postgres_port = int(args[index + 1])
        if not 1 <= postgres_port <= 65535:
            raise ValueError("invalid isolated PostgreSQL port")
        del args[index:index + 2]
    # Retain only OS/runtime paths, never inherited service credentials.
    allowed = {"PATH", "SYSTEMROOT", "WINDIR", "COMSPEC", "TEMP", "TMP", "USERPROFILE",
               "APPDATA", "LOCALAPPDATA", "PROGRAMFILES", "PROGRAMFILES(X86)", "SYSTEMDRIVE",
               "NUMBER_OF_PROCESSORS", "PROCESSOR_ARCHITECTURE", "PATHEXT"}
    for key in list(os.environ):
        if key.upper() not in allowed:
            del os.environ[key]
    os.environ.update({
        "LLM_API_KEY": "fixture-key", "LLM_BASE_URL": "http://127.0.0.1:9/v1",
        "LLM_MODEL": "fixture-model", "LLM_TIMEOUT_SECONDS": "1",
        "SPRING_BOOT_BASE_URL": "http://127.0.0.1:9", "SPRING_BOOT_TIMEOUT_SECONDS": "1",
        "JEV_ENABLED": "false", "CHECKPOINT_BACKEND": "memory", "MEMORY_BACKEND": "memory",
        "PYTHONUTF8": "1", "PYTHONIOENCODING": "utf-8",
    })
    if postgres_port is not None:
        # Only the explicitly created loopback test role/database is usable;
        # never accept a production URL or read credentials from .env.
        os.environ["DATABASE_URL"] = f"postgresql://journal_recovery@127.0.0.1:{postgres_port}/journal_recovery"
    # Subprocess acceptance workers inherit the same targeted dotenv guard.
    boot = ROOT / ".dev" / "journal-checkpoint-recovery" / "isolation"
    boot.mkdir(parents=True, exist_ok=True)
    (boot / "sitecustomize.py").write_text(
        "from scripts.validate_journal_recovery import disable_project_dotenv\n"
        "disable_project_dotenv()\n", encoding="utf-8",
    )
    os.environ["PYTHONPATH"] = os.pathsep.join((str(ROOT), str(boot)))
    sys.path.insert(0, str(ROOT))
    disable_project_dotenv()
    # PYTHONUTF8 affects only newly started interpreters. Restart this parent
    # as well, so subprocess.run(text=True) decodes the child's UTF-8 stdout
    # consistently on Windows. Do this before importing pytest/application code.
    if not sys.flags.utf8_mode:
        os.execv(sys.executable, [sys.executable, "-X", "utf8", str(Path(__file__).resolve()), *sys.argv[1:]])
    import pytest

    return pytest.main(args or ["-q", "tests"])


if __name__ == "__main__":
    raise SystemExit(main())
