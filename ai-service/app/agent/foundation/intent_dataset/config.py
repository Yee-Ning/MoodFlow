import json
from pathlib import Path
from typing import Any

from app.agent.foundation.intent_dataset.schemas import TRAINABLE_INTENTS
from app.agent.foundation.routing import IntentType


def load_taxonomy(path: Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8") as handle:
        taxonomy = json.load(handle)
    if not isinstance(taxonomy, dict):
        raise ValueError("taxonomy must be a JSON object")
    entries = taxonomy.get("intents")
    if not isinstance(entries, list):
        raise ValueError("taxonomy.intents must be a list")

    by_name: dict[str, dict[str, Any]] = {}
    for entry in entries:
        if not isinstance(entry, dict) or not isinstance(entry.get("name"), str):
            raise ValueError("each taxonomy intent requires a string name")
        name = entry["name"]
        if name in by_name:
            raise ValueError(f"duplicate taxonomy intent: {name}")
        try:
            intent = IntentType[name]
        except KeyError as error:
            raise ValueError(f"taxonomy contains unknown intent: {name}") from error
        if entry.get("value") != intent.value:
            raise ValueError(f"taxonomy value does not match IntentType.{name}")
        if not isinstance(entry.get("description"), str) or not entry["description"].strip():
            raise ValueError(f"taxonomy intent {name} requires a description")
        expected_trainable = intent is not IntentType.UNKNOWN
        if entry.get("trainable") is not expected_trainable:
            raise ValueError(f"taxonomy trainable flag is invalid for {name}")
        by_name[name] = entry

    expected_names = {intent.name for intent in IntentType}
    if set(by_name) != expected_names:
        missing = sorted(expected_names - set(by_name))
        extra = sorted(set(by_name) - expected_names)
        raise ValueError(f"taxonomy is out of sync; missing={missing}, extra={extra}")
    return taxonomy


def load_label_mapping(path: Path) -> dict[str, int]:
    with path.open("r", encoding="utf-8") as handle:
        raw_mapping = json.load(handle)
    if not isinstance(raw_mapping, dict):
        raise ValueError("label mapping must be a JSON object")

    expected_names = {intent.name for intent in TRAINABLE_INTENTS}
    if set(raw_mapping) != expected_names:
        missing = sorted(expected_names - set(raw_mapping))
        extra = sorted(set(raw_mapping) - expected_names)
        raise ValueError(f"label mapping is out of sync; missing={missing}, extra={extra}")
    if any(
        isinstance(index, bool) or not isinstance(index, int)
        for index in raw_mapping.values()
    ):
        raise ValueError("label mapping indices must be integers")
    expected_indices = list(range(len(TRAINABLE_INTENTS)))
    if sorted(raw_mapping.values()) != expected_indices:
        raise ValueError("label mapping indices must be unique and contiguous from zero")

    return dict(sorted(raw_mapping.items(), key=lambda item: item[1]))
