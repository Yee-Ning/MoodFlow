"""Canary preparation must fail closed and never inspect secrets."""
import json
from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml

from deploy.v5_canary_preflight import (
    CONFIG_KEYS, GATES, SOURCE_PATHS, blockers_for, collect, runtime_snapshot, source_digest,
)


def services():
    return [{"service": name, "health": "healthy", "state": "running", "exit_code": 0}
            for name in ("postgres", "qdrant", "netease", "java", "ai-service", "frontend")] + [
        {"service": "migrate", "state": "exited", "exit_code": 0}]


def config():
    return {"effective_config": {
        "AI_AGENT_GRAPH_MODE": "foundation", "HANDOFF_CANARY_ENABLED": False,
        "HANDOFF_CANARY_PERCENTAGE": 10, "JEV_ENABLED": True,
        "JEV_HANDOFF_ROUTER_ENABLED": True, "JEV_HANDOFF_ROUTER_SHADOW_MODE": False,
        "JEV_HANDOFF_GUARD_ENABLED": True,
        "JEV_HANDOFF_GUARD_THRESHOLD": 0.8,
    }}


def test_snapshot_uses_allowlist_defaults_and_missing_module_is_null(tmp_path, monkeypatch):
    path = tmp_path / "app/config/settings.py"
    path.parent.mkdir(parents=True)
    path.write_text('''class Settings:
    graph: str = Field(default="foundation", validation_alias="AI_AGENT_GRAPH_MODE")
    enabled: bool = Field(default=False, validation_alias="HANDOFF_CANARY_ENABLED")
    secret: str = Field(default="do-not-report", validation_alias="LLM_API_KEY")
''', encoding="utf-8")
    monkeypatch.setenv("HANDOFF_CANARY_ENABLED", "true")
    monkeypatch.setenv("LLM_API_KEY", "private-test-value")
    visited = []
    getenv = __import__("os").getenv

    def allowlisted(key):
        visited.append(key)
        assert key in CONFIG_KEYS
        return getenv(key)

    monkeypatch.setattr("deploy.v5_canary_preflight.os.getenv", allowlisted)
    snapshot = runtime_snapshot(tmp_path)
    assert snapshot["effective_config"]["HANDOFF_CANARY_ENABLED"] == "true"
    assert snapshot["source_defaults"]["HANDOFF_CANARY_ENABLED"] is False
    assert snapshot["source_sha256"]["app/agent/handoff/rollout.py"] is None
    assert "private-test-value" not in json.dumps(snapshot)
    assert "do-not-report" not in json.dumps(snapshot)
    assert visited == list(CONFIG_KEYS)


def test_source_digest_handles_checkout_newlines_but_detects_code_change():
    assert source_digest(b"abc\r\n") == source_digest(b"abc\n")
    assert source_digest(b"abc\n") != source_digest(b"abd\n")


def test_valid_readiness_evidence_still_requires_metrics_ack_alerts_and_live_paths():
    result = blockers_for(dict.fromkeys(GATES, True), config(), dict.fromkeys(SOURCE_PATHS, True),
                          dict.fromkeys(("jev.v5_shadow", "jev.handoff_entry", "jev.handoff_guard"), 1),
                          services(), False)
    assert result == ["active_monitoring_denominators_unverified", "player_ack_monitoring_unverified",
                      "fallback_alert_delivery_unverified", "java_image_provenance_unverified",
                      "live_canary_non_canary_paths_unverified"]


def test_old_production_image_and_missing_events_cannot_pass():
    runtime = config()
    runtime["effective_config"]["HANDOFF_CANARY_ENABLED"] = None
    result = blockers_for(dict.fromkeys(GATES, False), runtime, dict.fromkeys(SOURCE_PATHS, False),
                          {}, [], True)
    assert "production_gate_missing:A" in result
    assert "configuration_not_ready:HANDOFF_CANARY_ENABLED" in result
    assert "running_source_unverified:app/agent/handoff/rollout.py" in result
    assert "monitoring_no_events:jev.handoff_entry" in result
    assert "service_not_healthy:ai-service" in result
    assert "migration_not_successful" in result
    assert "log_window_truncated" in result


@pytest.mark.parametrize("key,value", [
    ("AI_AGENT_GRAPH_MODE", "handoff"), ("HANDOFF_CANARY_ENABLED", True),
    ("HANDOFF_CANARY_PERCENTAGE", 100), ("JEV_HANDOFF_ROUTER_ENABLED", False),
    ("JEV_HANDOFF_ROUTER_SHADOW_MODE", True), ("JEV_HANDOFF_GUARD_THRESHOLD", 0.7),
])
def test_wrong_configuration_is_blocked_without_mutation(key, value):
    runtime = config()
    runtime["effective_config"][key] = value
    result = blockers_for(dict.fromkeys(GATES, True), runtime, {}, {}, services(), False)
    assert f"configuration_not_ready:{key}" in result
    assert runtime["effective_config"][key] == value


@pytest.mark.parametrize("since,tail", [("0h", 100), ("2h; touch /tmp/unsafe", 10), ("2h", 0), ("2h", 100001)])
def test_invalid_log_window_never_launches_a_command(tmp_path, monkeypatch, since, tail):
    def unexpected(*args, **kwargs):
        pytest.fail("invalid input must not launch subprocesses")
    monkeypatch.setattr("deploy.v5_canary_preflight.subprocess.run", unexpected)
    with pytest.raises(ValueError, match="invalid_log_window"):
        collect(tmp_path, since, tail)


def test_production_template_exposes_only_closed_canary_switch():
    root = Path(__file__).resolve().parents[2]
    compose = yaml.safe_load((root / "docker-compose.production.yml").read_text(encoding="utf-8"))
    environment = compose["x-ai"]["environment"]
    assert environment["HANDOFF_CANARY_ENABLED"] == "${HANDOFF_CANARY_ENABLED:-false}"
    assert environment["AI_AGENT_GRAPH_MODE"] == "foundation"
    assert environment["JEV_ROUTER_ENABLED"] == "false"
    assert "JEV_HANDOFF_ROUTER_ENABLED" not in environment
    assert "JEV_HANDOFF_GUARD_THRESHOLD" not in environment


@pytest.mark.parametrize("array_format", [False, True])
def test_collector_keeps_only_public_evidence_and_runs_read_only_commands(tmp_path, monkeypatch, array_format):
    source = "# public source\n"
    snapshot = {**config(), "source_sha256": dict.fromkeys(SOURCE_PATHS, source_digest(source.encode()))}
    ps = [{"Service": row["service"], "Health": row.get("health"),
           "State": row["state"], "ExitCode": row["exit_code"], "Image": "test-image"}
          for row in services()]
    calls = []

    def execute(args, root, *, check=True):
        calls.append(args)
        assert root == tmp_path
        if args[:3] == ["git", "merge-base", "--is-ancestor"]:
            return SimpleNamespace(returncode=0, stdout="")
        if args[:2] == ["git", "show"]:
            return SimpleNamespace(returncode=0, stdout=source)
        if args == ["git", "rev-parse", "HEAD"]:
            return SimpleNamespace(returncode=0, stdout="a" * 40 + "\n")
        if args == ["git", "status", "--porcelain"]:
            return SimpleNamespace(returncode=0, stdout="")
        if "ps" in args:
            return SimpleNamespace(returncode=0, stdout=json.dumps(ps) if array_format else "\n".join(map(json.dumps, ps)))
        if "logs" in args:
            return SimpleNamespace(returncode=0, stdout="unrelated private user query: do-not-save\n")
        pytest.fail(f"unexpected command: {args}")

    def container(args, **kwargs):
        calls.append(args)
        assert args[-4:] == ["ai-service", "python", "-", "--inside-container"]
        assert "runtime_snapshot" in kwargs["input"]
        return SimpleNamespace(stdout=json.dumps(snapshot))

    monkeypatch.setattr("deploy.v5_canary_preflight.command", execute)
    monkeypatch.setattr("deploy.v5_canary_preflight.subprocess.run", container)
    result = collect(tmp_path, "2h", 100)
    assert all(result["running_ai_source_matches_checkout"].values())
    assert result["shadow_summary"]["production"]["sample_size"] == 0
    assert result["canary_opened"] is False and result["production_mutations"] == 0
    assert "do-not-save" not in json.dumps(result)
    assert not any(word in args for args in calls for word in ("up", "restart", "build", "fetch", "push"))


def test_cli_hides_exception_text_and_reports_failure(monkeypatch, capsys):
    from deploy.v5_canary_preflight import main

    def fail(*args):
        raise RuntimeError("credential-must-not-be-printed")
    monkeypatch.setattr("deploy.v5_canary_preflight.collect", fail)
    monkeypatch.setattr("sys.argv", ["probe"])
    assert main() == 2
    output = capsys.readouterr().out
    assert "credential-must-not-be-printed" not in output
    assert json.loads(output) == {"probe_failed": True, "error_type": "RuntimeError", "canary_opened": False}
