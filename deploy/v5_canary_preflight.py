"""Read-only canary evidence collector. Never load secrets or change production.

Run on the Docker host, or pipe this file to an existing SSH connection. A
successful probe is evidence collection, not permission to open the canary.
"""
from __future__ import annotations

import argparse
import ast
import hashlib
import json
import os
import re
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

GATES = {
    "A": "588fa6f776776e2d4662cdaf09e38e872745623a",
    "C": "37ffdabb8e7c5a959027928b94b3de092b54be23",
    "R2": "feea34747f98081f613467be71cef00614e73d9e",
    "D": "a3cc649c643b3167805a2c6e36722b6344d8beef",
    "T1T2_AI": "b983411b0029b154b8d50c4fe23b579128c758f9",
    "T1T2_Java": "c4096608e92ac6ad3077b807beced04932d3f5c6",
}
# Deliberately do not inspect all environment variables, .env or /run/secrets.
CONFIG_KEYS = (
    "AI_AGENT_GRAPH_MODE", "HANDOFF_CANARY_ENABLED", "HANDOFF_CANARY_PERCENTAGE",
    "JEV_ENABLED", "JEV_ROUTER_ENABLED", "JEV_ROUTER_V5_SHADOW_ENABLED",
    "JEV_HANDOFF_ROUTER_ENABLED", "JEV_HANDOFF_ROUTER_SHADOW_MODE",
    "JEV_HANDOFF_GUARD_ENABLED", "JEV_HANDOFF_GUARD_SHADOW_MODE",
    "JEV_HANDOFF_GUARD_THRESHOLD",
)
SOURCE_PATHS = (
    "app/config/settings.py", "app/api/chat.py",
    "app/agent/handoff/rollout.py", "app/agent/handoff/routing.py",
    "app/agent/handoff/guard.py", "app/agent/handoff/progress.py",
    "app/agent/foundation/long_horizon/operations.py",
    "app/agent/foundation/long_horizon/write_reconciliation.py",
    "app/agent/foundation/long_horizon/recovery_receipts.py",
    "app/agent/foundation/long_horizon/coordinator.py",
    "app/agent/foundation/subgraphs/tool_calling/assistant.py",
    "app/tools/music.py",
)
MARKERS = (
    "jev.v5_shadow ", "jev.v5_shadow_failure ", "jev.v5_shadow_dropped ",
    "jev.handoff_entry ", "jev.handoff_entry_local ",
    "jev.handoff_entry_failure ", "jev.handoff_guard ", "handoff.trace ",
)


def source_digest(content: bytes) -> str:
    """Compare source across Git CRLF checkouts without weakening code checks."""
    return hashlib.sha256(content.replace(b"\r\n", b"\n")).hexdigest()


def runtime_snapshot(root: Path) -> dict:
    """Runs inside the container: public config and application source only."""
    environment = {key: os.getenv(key) for key in CONFIG_KEYS}
    defaults = {}
    path = root / "app/config/settings.py"
    if path.is_file():
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if not isinstance(node, ast.AnnAssign) or not isinstance(node.value, ast.Call):
                continue
            arguments = {item.arg: item.value for item in node.value.keywords}
            alias, default = arguments.get("validation_alias"), arguments.get("default")
            if (isinstance(alias, ast.Constant) and alias.value in CONFIG_KEYS
                    and isinstance(default, ast.Constant)):
                defaults[alias.value] = default.value
    return {
        "environment": environment,
        "source_defaults": defaults,
        "effective_config": {
            key: environment[key] if environment[key] is not None else defaults.get(key)
            for key in CONFIG_KEYS
        },
        "source_sha256": {
            name: source_digest((root / name).read_bytes()) if (root / name).is_file() else None
            for name in SOURCE_PATHS
        },
    }


def command(args: list[str], root: Path, *, check: bool = True) -> subprocess.CompletedProcess:
    return subprocess.run(args, cwd=root, text=True, encoding="utf-8",
                          stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                          timeout=60, check=check)


def blockers_for(gates: dict, runtime: dict, image_matches: dict, counts: dict,
                 services: list[dict], log_truncated: bool) -> list[str]:
    blockers = [f"production_gate_missing:{name}" for name, value in gates.items() if not value]
    for path, matches in image_matches.items():
        if not matches:
            blockers.append(f"running_source_unverified:{path}")
    config = runtime["effective_config"]
    expected = {
        "AI_AGENT_GRAPH_MODE": "foundation", "HANDOFF_CANARY_ENABLED": "false",
        "HANDOFF_CANARY_PERCENTAGE": "10", "JEV_ENABLED": "true",
        "JEV_HANDOFF_ROUTER_ENABLED": "true", "JEV_HANDOFF_ROUTER_SHADOW_MODE": "false",
        "JEV_HANDOFF_GUARD_ENABLED": "true",
        "JEV_HANDOFF_GUARD_THRESHOLD": "0.8",
    }
    for name, value in expected.items():
        actual = config.get(name)
        if name in {"HANDOFF_CANARY_PERCENTAGE", "JEV_HANDOFF_GUARD_THRESHOLD"}:
            try:
                matches = not isinstance(actual, bool) and float(actual) == float(value)
            except (ValueError, TypeError):
                matches = False
        else:
            matches = str(actual).lower() == value
        if not matches:
            blockers.append(f"configuration_not_ready:{name}")
    status = {row["service"]: row for row in services}
    for name in ("postgres", "qdrant", "netease", "java", "ai-service", "frontend"):
        if status.get(name, {}).get("health") != "healthy":
            blockers.append(f"service_not_healthy:{name}")
    if status.get("migrate", {}).get("state") != "exited" or status.get("migrate", {}).get("exit_code") != 0:
        blockers.append("migration_not_successful")
    if log_truncated:
        blockers.append("log_window_truncated")
    for marker in ("jev.v5_shadow", "jev.handoff_entry", "jev.handoff_guard"):
        if not counts.get(marker):
            blockers.append(f"monitoring_no_events:{marker}")
    # Existing entry/Guard events lack trustworthy per-request canary/sample
    # labels. Marker presence alone cannot establish rates or ACK success.
    blockers.extend(("active_monitoring_denominators_unverified", "player_ack_monitoring_unverified",
                     "fallback_alert_delivery_unverified", "java_image_provenance_unverified",
                     "live_canary_non_canary_paths_unverified"))
    return blockers


def collect(root: Path, since: str, tail: int) -> dict:
    if not re.fullmatch(r"[1-9][0-9]*[mh]", since) or not 1 <= tail <= 100_000:
        raise ValueError("invalid_log_window")
    compose = ["docker", "compose", "--env-file", ".env.production", "-f", "docker-compose.production.yml"]
    gates = {name: command(["git", "merge-base", "--is-ancestor", ref, "HEAD"], root, check=False).returncode == 0
             for name, ref in GATES.items()}
    # Send source on stdin, not to disk in production. No runtime app import,
    # model call, session identity resolution or credentials are needed.
    probe = globals().get("_PREFLIGHT_SOURCE") or Path(__file__).read_text(encoding="utf-8")
    result = subprocess.run(compose + ["exec", "-T", "ai-service", "python", "-", "--inside-container"],
                            cwd=root, input=probe, text=True, encoding="utf-8",
                            stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, timeout=60, check=True)
    runtime = json.loads(result.stdout)
    image_matches = {}
    for name, digest in runtime["source_sha256"].items():
        git = command(["git", "show", f"HEAD:ai-service/{name}"], root, check=False)
        image_matches[name] = bool(digest and git.returncode == 0
                                   and source_digest(git.stdout.encode("utf-8")) == digest)
    ps = command(compose + ["ps", "--all", "--format", "json"], root).stdout
    records = json.loads(ps) if ps.lstrip().startswith("[") else [json.loads(line) for line in ps.splitlines() if line.strip()]
    services = [{"service": row.get("Service"), "state": row.get("State"),
                 "health": row.get("Health"), "exit_code": row.get("ExitCode"),
                 "image": row.get("Image")} for row in records]
    logs = command(compose + ["logs", "--no-color", "--since", since, "--tail", str(tail), "ai-service"], root).stdout.splitlines()
    counts = {marker.strip(): sum(marker in line for line in logs) for marker in MARKERS}
    sys.path.insert(0, str(root))
    from deploy.jev_shadow_summary import summarize
    blockers = blockers_for(gates, runtime, image_matches, counts, services, len(logs) >= tail)
    worktree_clean = not command(["git", "status", "--porcelain"], root).stdout.strip()
    if not worktree_clean:
        blockers.append("production_worktree_dirty")
    return {
        "observed_at_utc": datetime.now(timezone.utc).isoformat(),
        "production_repository_head": command(["git", "rev-parse", "HEAD"], root).stdout.strip(),
        "production_worktree_clean": worktree_clean,
        "production_gate_ancestors": gates, "runtime": runtime,
        "running_ai_source_matches_checkout": image_matches, "services": services,
        "log_window": {"since": since, "tail_limit": tail, "line_count": len(logs), "possibly_truncated": len(logs) >= tail},
        "marker_counts": counts, "shadow_summary": summarize(logs),
        "blockers": blockers, "canary_opened": False, "production_mutations": 0,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path("/opt/moodflow"))
    parser.add_argument("--since", default="2h")
    parser.add_argument("--tail", type=int, default=10_000)
    parser.add_argument("--inside-container", action="store_true", help=argparse.SUPPRESS)
    args = parser.parse_args()
    try:
        if args.inside_container:
            result = runtime_snapshot(Path("/app"))
        else:
            result = collect(args.root.resolve(), args.since, args.tail)
    except Exception as exc:
        # Never print command output or exception text: it could contain secrets.
        print(json.dumps({"probe_failed": True, "error_type": type(exc).__name__, "canary_opened": False}))
        return 2
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if args.inside_container or not result["blockers"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
