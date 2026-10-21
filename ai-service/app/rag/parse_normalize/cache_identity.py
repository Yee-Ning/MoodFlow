"""Stable, offline cache identities; never serialize runtime counters or secrets."""

from __future__ import annotations

import inspect
import json
from dataclasses import asdict, is_dataclass
from hashlib import sha256


def identity_hash(value: object) -> str:
    return sha256(json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode("utf-8")).hexdigest()


def callable_identity(value: object) -> dict[str, object]:
    target = getattr(value, "__func__", value)
    name = f"{getattr(target, '__module__', type(target).__module__)}.{getattr(target, '__qualname__', type(target).__qualname__)}"
    try:
        source = inspect.getsource(target)
    except (OSError, TypeError):
        source = name
    # Primitive closure/default values distinguish command-builder settings,
    # without admitting mutable fixtures, call counters or object addresses.
    def primitive(item):
        return item if isinstance(item, (str, int, float, bool, type(None))) else type(item).__qualname__
    return {
        "name": name, "implementation": sha256(source.encode()).hexdigest(),
        "defaults": [primitive(x) for x in (getattr(target, "__defaults__", None) or ())],
        "closure": [primitive(c.cell_contents) for c in (getattr(target, "__closure__", None) or ())],
    }


def component_identity(value: object) -> dict[str, object]:
    """Custom backends may expose cache_identity() for additional configuration."""
    hook = getattr(value, "cache_identity", None)
    if callable(hook):
        return hook()
    result = {"class": f"{type(value).__module__}.{type(value).__qualname__}"}
    for name in ("parser_name", "parser_version", "version_label", "timeout_seconds", "backend", "executable"):
        item = getattr(value, name, None)
        if isinstance(item, (str, int, float, bool)):
            result[name] = item
    config = getattr(value, "config", None)
    if is_dataclass(config):
        result["config"] = asdict(config)
    for name in ("run", "convert", "command_builder", "import_module"):
        item = getattr(value, name, None)
        if callable(item):
            result[name] = callable_identity(item)
    return result
