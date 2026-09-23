"""Keep routine tests independent of the developer's live Jev credentials."""

import pytest

from app.config.settings import get_settings


@pytest.fixture(autouse=True)
def disable_jev_for_routine_tests(monkeypatch):
    monkeypatch.setenv("JEV_ENABLED", "false")
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()
