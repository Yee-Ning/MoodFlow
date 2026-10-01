from pathlib import Path
import os
from unittest.mock import patch
import pytest
from pydantic import ValidationError

from app.config.settings import Settings, jev_requested


def test_jev_fields_use_existing_settings_and_hide_key(tmp_path, monkeypatch):
    monkeypatch.delenv("JEV_ENABLED", raising=False)
    env = tmp_path / ".env"
    env.write_text(
        "TYPESAFE_API_KEY=fake-secret\nJEV_ENABLED=true\nJEV_MODEL=jev-latest\n"
        "JEV_ROUTER_ENABLED=true\nJEV_SHADOW_MODE=true\n",
        encoding="utf-8",
    )
    settings = Settings(
        _env_file=env,
        api_key="test",
        base_url="https://example.com",
        model="test",
        timeout_seconds=10,
        SPRING_BOOT_BASE_URL="http://127.0.0.1:8080",
        SPRING_BOOT_TIMEOUT_SECONDS=10,
    )
    assert settings.typesafe_api_key.get_secret_value() == "fake-secret"
    assert (
        settings.jev_enabled
        and settings.jev_router_enabled
        and settings.jev_shadow_mode
    )
    assert settings.jev_model == "jev-latest"
    assert "fake-secret" not in repr(settings)


def test_example_contains_no_typesafe_credential():
    example = Path(__file__).resolve().parents[1] / ".env.example"
    lines = example.read_text(encoding="utf-8").splitlines()
    assert "TYPESAFE_API_KEY=" in lines


def test_jev_gate_does_not_load_unrelated_env_values(tmp_path):
    env = tmp_path / ".env"
    env.write_text("JEV_ENABLED=true\nPERSISTENCE_BACKEND=postgres\n", encoding="utf-8")
    with (
        patch("app.config.settings.ENV_FILE", env),
        patch.dict(os.environ, {}, clear=True),
    ):
        assert jev_requested()
        assert "PERSISTENCE_BACKEND" not in os.environ


def handoff_settings(**values):
    return Settings(
        _env_file=None,
        api_key="test",
        base_url="https://example.com",
        model="test",
        timeout_seconds=10,
        SPRING_BOOT_BASE_URL="http://127.0.0.1:8080",
        SPRING_BOOT_TIMEOUT_SECONDS=10,
        **values,
    )


def test_handoff_aliases_parse_independent_controls():
    config = handoff_settings(
        JEV_HANDOFF_ROUTER_ENABLED="true",
        JEV_HANDOFF_ROUTER_SHADOW_MODE="false",
        JEV_HANDOFF_GUARD_ENABLED="false",
        JEV_HANDOFF_GUARD_SHADOW_MODE="true",
        JEV_HANDOFF_GUARD_THRESHOLD="0.9",
        JEV_HANDOFF_INJECTION_THRESHOLD="0.1",
    )
    assert (
        config.jev_handoff_router_enabled and not config.jev_handoff_router_shadow_mode
    )
    assert not config.jev_handoff_guard_enabled and config.jev_handoff_guard_shadow_mode
    assert (
        config.jev_handoff_guard_threshold == 0.9
        and config.jev_handoff_injection_threshold == 0.1
    )


@pytest.mark.parametrize(
    "field", ["JEV_HANDOFF_GUARD_THRESHOLD", "JEV_HANDOFF_INJECTION_THRESHOLD"]
)
@pytest.mark.parametrize("value", [-0.01, 1.01, float("nan")])
def test_handoff_threshold_config_validation(field, value):
    with pytest.raises(ValidationError):
        handoff_settings(**{field: value})
