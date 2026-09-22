from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from app.config.settings import load_runtime_environment
from scripts import migrate_agent_postgres


class RuntimeEnvironmentTests(unittest.TestCase):
    def test_loads_persistence_settings_without_overriding_process_environment(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            env_file = Path(directory) / ".env"
            env_file.write_text(
                "MEMORY_BACKEND=postgres\n"
                "DATABASE_URL=postgresql://root@127.0.0.1:5432/moodflow_agent\n"
                "PGPASSFILE=C:/secure/pgpass.conf\n",
                encoding="utf-8",
            )
            with patch("app.config.settings.ENV_FILE", env_file), patch.dict(
                os.environ, {"MEMORY_BACKEND": "memory"}, clear=True
            ):
                load_runtime_environment()
                self.assertEqual(os.environ["MEMORY_BACKEND"], "memory")
                self.assertEqual(
                    os.environ["DATABASE_URL"],
                    "postgresql://root@127.0.0.1:5432/moodflow_agent",
                )
                self.assertEqual(os.environ["PGPASSFILE"], "C:/secure/pgpass.conf")

    def test_migration_cli_loads_environment_before_backend_selection(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            env_file = Path(directory) / ".env"
            env_file.write_text("MEMORY_BACKEND=postgres\n", encoding="utf-8")
            with patch("app.config.settings.ENV_FILE", env_file), patch.dict(
                os.environ, {}, clear=True
            ), patch.object(
                migrate_agent_postgres, "run_all_migrations", return_value=[]
            ) as migrate:
                migrate_agent_postgres.main()
                migrate.assert_called_once_with(include_memory=True)


if __name__ == "__main__":
    unittest.main()
