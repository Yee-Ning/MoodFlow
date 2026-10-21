"""Keep routine tests independent of the developer's live Jev credentials."""

import pytest

from app.config import settings as configuration
from app.config.settings import Settings, get_settings


@pytest.fixture(autouse=True)
def disable_jev_for_routine_tests(monkeypatch, tmp_path_factory):
    # Routine tests use controlled values even on a workstation with live .env files.
    # Individual tests can still explicitly supply an env file or override these values.
    monkeypatch.setitem(Settings.model_config, "env_file", None)
    monkeypatch.setattr(configuration, "ENV_FILE", tmp_path_factory.getbasetemp() / "no-live-settings.env")
    for key, value in {
        "LLM_API_KEY": "offline-test-key",
        "LLM_BASE_URL": "http://127.0.0.1:9/v1",
        "LLM_MODEL": "offline-test-model",
        "LLM_TIMEOUT_SECONDS": "60",
        "SPRING_BOOT_BASE_URL": "http://127.0.0.1:9",
        "SPRING_BOOT_TIMEOUT_SECONDS": "15",
    }.items():
        monkeypatch.setenv(key, value)
    monkeypatch.setenv("JEV_ENABLED", "false")
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()
