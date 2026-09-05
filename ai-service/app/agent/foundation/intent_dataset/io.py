import json
from collections.abc import Iterable, Iterator
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ValidationError

from app.agent.foundation.intent_dataset.schemas import IntentDatasetSample


def read_jsonl_objects(path: Path) -> Iterator[tuple[int, Any]]:
    with path.open("r", encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, start=1):
            if not line.strip():
                continue
            try:
                yield line_number, json.loads(line)
            except json.JSONDecodeError as error:
                raise ValueError(
                    f"{path}:{line_number}: invalid JSON: {error.msg}"
                ) from error


def load_samples(path: Path) -> list[IntentDatasetSample]:
    samples: list[IntentDatasetSample] = []
    for line_number, raw in read_jsonl_objects(path):
        try:
            samples.append(IntentDatasetSample.model_validate(raw))
        except ValidationError as error:
            raise ValueError(f"{path}:{line_number}: {error}") from error
    return samples


def _json_ready(record: BaseModel | dict[str, Any]) -> dict[str, Any]:
    if isinstance(record, BaseModel):
        return record.model_dump(mode="json", exclude_none=True)
    return record


def write_jsonl(path: Path, records: Iterable[BaseModel | dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary_path = path.with_suffix(f"{path.suffix}.tmp")
    with temporary_path.open("w", encoding="utf-8", newline="\n") as handle:
        for record in records:
            serialized = json.dumps(
                _json_ready(record),
                ensure_ascii=False,
                separators=(",", ":"),
            )
            handle.write(f"{serialized}\n")
    temporary_path.replace(path)
