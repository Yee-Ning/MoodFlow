"""Structured music JSON and bounded generic JSON adapter."""

from __future__ import annotations

import json
import re
from hashlib import sha256
from dataclasses import dataclass

from ..base import BaseParser, ParserError
from ..models import (
    BlockType,
    DetectedFileType,
    DocumentBlock,
    JsonValue,
    ParseInput,
    ParserResult,
)


@dataclass(frozen=True, slots=True)
class JsonParserConfig:
    max_depth: int = 6
    max_field_chars: int = 2_000
    max_output_chars: int = 200_000
    max_items: int = 100

    def __post_init__(self) -> None:
        if min(
            self.max_depth,
            self.max_field_chars,
            self.max_output_chars,
            self.max_items,
        ) <= 0:
            raise ValueError("JSON parser limits must be positive")


@dataclass(slots=True)
class _OutputBudget:
    remaining: int
    truncated: bool = False

    def consume(self, value: str, field_limit: int) -> str:
        bounded = value
        if len(bounded) > field_limit:
            bounded = bounded[: max(0, field_limit - 1)] + "…"
            self.truncated = True
        if len(bounded) > self.remaining:
            if self.remaining <= 0:
                self.truncated = True
                return ""
            bounded = bounded[: max(0, self.remaining - 1)] + "…"
            self.truncated = True
        self.remaining -= len(bounded)
        return bounded


class _BlockCollector:
    def __init__(self, source_id: str, config: JsonParserConfig) -> None:
        self.source_id = source_id
        self.config = config
        self.budget = _OutputBudget(config.max_output_chars)
        self.blocks: list[DocumentBlock] = []
        self.headings: dict[int, str] = {}

    def add(
        self,
        block_type: BlockType,
        text: str,
        *,
        level: int | None = None,
        metadata: dict[str, JsonValue] | None = None,
    ) -> None:
        bounded = self.budget.consume(text, self.config.max_field_chars)
        if not bounded:
            return
        block_metadata = dict(metadata or {})
        if block_type is BlockType.LIST:
            block_metadata["items"] = bounded.splitlines()
            block_metadata.setdefault("ordered", False)
        order_index = len(self.blocks)
        block_id = f"{self.source_id}-b{order_index:06d}"
        if block_type is BlockType.HEADING:
            assert level is not None
            lower_levels = [item for item in self.headings if item < level]
            parent_id = self.headings[max(lower_levels)] if lower_levels else None
            for heading_level in list(self.headings):
                if heading_level >= level:
                    del self.headings[heading_level]
            self.headings[level] = block_id
        else:
            parent_id = self.headings[max(self.headings)] if self.headings else None
        self.blocks.append(
            DocumentBlock(
                block_id=block_id,
                block_type=block_type,
                text=bounded,
                level=level,
                order_index=order_index,
                parent_block_id=parent_id,
                metadata=block_metadata,
            )
        )


class JsonParser(BaseParser):
    parser_name = "json"
    parser_version = "1.1.0"
    supported_file_types = frozenset({DetectedFileType.JSON})

    def __init__(self, config: JsonParserConfig | None = None) -> None:
        self.config = config or JsonParserConfig()

    def parse(self, parse_input: ParseInput) -> ParserResult:
        try:
            payload: JsonValue = json.loads(parse_input.raw_bytes.decode("utf-8-sig"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ParserError("json_parse_failed") from exc

        if self._is_wikidata(payload):
            result = self._parse_wikidata(payload, parse_input.snapshot.source_id)
        elif self._is_musicbrainz(payload):
            result = self._parse_musicbrainz(payload, parse_input.snapshot.source_id)
        else:
            result = self._parse_generic(payload, parse_input.snapshot.source_id)
        return self._atomic_records(payload, parse_input.snapshot.source_id, result)

    def _atomic_records(
        self, payload: JsonValue, source_id: str, result: ParserResult
    ) -> ParserResult:
        # Explicit identities only: never infer an entity from prose or headings.
        records: list[tuple[str, dict[str, JsonValue]]] = []
        if self._is_wikidata(payload):
            records = [
                (f"/entities/{key}", value)
                for key, value in payload["entities"].items()
                if isinstance(value, dict)
            ]
        elif self._is_musicbrainz(payload) or self._is_entity_record(payload):
            records = [("/", payload)]
        elif isinstance(payload, list) and payload and all(
            self._is_entity_record(value) for value in payload
        ):
            records = [(f"/{index}", value) for index, value in enumerate(payload)]
        if not records:
            return result
        if len(records) > self.config.max_items:
            raise ParserError("structured_record_item_limit_exceeded")
        blocks = []
        output_chars = 0
        for pointer, record in records:
            # Preserve every field, including fields absent from profile renderers.
            # Generic non-record JSON retains the existing bounded renderer.
            content = json.dumps(record, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
            title = self._first_string(record, "name", "title", "id")
            if not title:
                title, _ = self._localized_value(record.get("labels"))
            title = title or pointer.rsplit("/", 1)[-1] or "JSON Record"
            output_chars += len(content) + len(title)
            if output_chars > self.config.max_output_chars:
                raise ParserError("structured_record_output_limit_exceeded")
            record_id = "record:" + sha256(f"{source_id}:{pointer}".encode()).hexdigest()[:24]
            metadata = {"record_id": record_id, "record_pointer": pointer,
                        "record_profile": result.metadata["json_profile"]}
            heading_id = f"{source_id}-b{len(blocks):06d}"
            blocks.append(DocumentBlock(
                block_id=heading_id, block_type=BlockType.HEADING, text=title,
                level=1, order_index=len(blocks), metadata=metadata,
            ))
            blocks.append(DocumentBlock(
                block_id=f"{source_id}-b{len(blocks):06d}", block_type=BlockType.CODE,
                text=content, order_index=len(blocks), parent_block_id=heading_id,
                metadata={**metadata, "language": "json"},
            ))
        return result.model_copy(update={
            "blocks": blocks,
            "metadata": {**result.metadata, "truncated": False,
                         "structured_record_count": len(records)},
        })

    @staticmethod
    def _is_entity_record(value: JsonValue) -> bool:
        return (
            isinstance(value, dict)
            and isinstance(value.get("id"), (str, int))
            and not isinstance(value.get("id"), bool)
            and bool(str(value["id"]).strip())
            and any(isinstance(value.get(key), str) and value[key].strip()
                    for key in ("name", "title", "type"))
        )

    @staticmethod
    def _is_wikidata(payload: JsonValue) -> bool:
        return (
            isinstance(payload, dict)
            and isinstance(payload.get("entities"), dict)
        )

    @staticmethod
    def _is_musicbrainz(payload: JsonValue) -> bool:
        if not isinstance(payload, dict) or not isinstance(payload.get("id"), str):
            return False
        musicbrainz_keys = {
            "aliases",
            "artist-credit",
            "recordings",
            "relations",
            "release-groups",
            "releases",
        }
        return bool(musicbrainz_keys.intersection(payload))

    def _parse_wikidata(
        self, payload: dict[str, JsonValue], source_id: str
    ) -> ParserResult:
        collector = _BlockCollector(source_id, self.config)
        entities_value = payload["entities"]
        assert isinstance(entities_value, dict)
        entity_items = list(entities_value.items())
        entity_ids: list[str] = []
        aliases: list[str] = []
        external_ids: dict[str, list[str]] = {}
        relations: list[dict[str, JsonValue]] = []
        title: str | None = None
        language = "und"
        structural_truncation = len(entity_items) > self.config.max_items

        for raw_entity_id, raw_entity in entity_items[: self.config.max_items]:
            if not isinstance(raw_entity, dict):
                continue
            entity_id = str(raw_entity.get("id") or raw_entity_id)
            entity_ids.append(entity_id)
            label, label_language = self._localized_value(raw_entity.get("labels"))
            display_name = label or entity_id
            if title is None:
                title = display_name
                language = label_language or self._guess_language(display_name)
            collector.add(BlockType.HEADING, display_name, level=1)
            description, _ = self._localized_value(raw_entity.get("descriptions"))
            if description:
                collector.add(BlockType.PARAGRAPH, description)

            entity_aliases = self._wikidata_aliases(raw_entity.get("aliases"))
            aliases.extend(value for value in entity_aliases if value not in aliases)
            if entity_aliases:
                collector.add(BlockType.HEADING, "Aliases", level=2)
                collector.add(BlockType.LIST, "\n".join(entity_aliases))

            claims = raw_entity.get("claims")
            if isinstance(claims, dict):
                for property_id, statements in list(claims.items())[: self.config.max_items]:
                    if not isinstance(statements, list):
                        continue
                    for statement in statements[: self.config.max_items]:
                        value = self._claim_value(statement)
                        if isinstance(value, str):
                            external_ids.setdefault(str(property_id), []).append(value)
                        elif isinstance(value, dict) and isinstance(value.get("id"), str):
                            relations.append(
                                {
                                    "property_id": str(property_id),
                                    "target_entity_id": value["id"],
                                }
                            )

        if external_ids:
            collector.add(BlockType.HEADING, "External IDs", level=2)
            collector.add(
                BlockType.LIST,
                "\n".join(
                    f"{property_id}: {value}"
                    for property_id, values in external_ids.items()
                    for value in values
                ),
            )
        if relations:
            collector.add(BlockType.HEADING, "Relations", level=2)
            collector.add(
                BlockType.LIST,
                "\n".join(
                    f"{relation['property_id']} -> {relation['target_entity_id']}"
                    for relation in relations
                ),
            )
        metadata: dict[str, JsonValue] = {
            "json_profile": "wikidata",
            "entity_ids": entity_ids,
            "aliases": aliases,
            "external_ids": external_ids,
            "relations": relations,
            "raw_json": payload,
            "truncated": structural_truncation or collector.budget.truncated,
        }
        return ParserResult(
            title=title,
            author=None,
            published_at=None,
            language=language,
            blocks=collector.blocks,
            metadata=metadata,
        )

    def _parse_musicbrainz(
        self, payload: dict[str, JsonValue], source_id: str
    ) -> ParserResult:
        collector = _BlockCollector(source_id, self.config)
        title = self._first_string(payload, "name", "title")
        entity_id = self._first_string(payload, "id")
        display_name = title or entity_id or "MusicBrainz Entity"
        collector.add(BlockType.HEADING, display_name, level=1)
        if entity_id:
            collector.add(BlockType.PARAGRAPH, f"MusicBrainz ID: {entity_id}")
        entity_type = self._first_string(payload, "type", "primary-type")
        if entity_type:
            collector.add(BlockType.PARAGRAPH, f"Type: {entity_type}")

        aliases = self._named_values(payload.get("aliases"))
        if aliases:
            collector.add(BlockType.HEADING, "Aliases", level=2)
            collector.add(BlockType.LIST, "\n".join(aliases))

        collection_specs = (
            ("release-groups", "Release Groups"),
            ("releases", "Releases"),
            ("recordings", "Recordings"),
            ("artists", "Artists"),
        )
        for key, heading in collection_specs:
            values = payload.get(key)
            entries = self._musicbrainz_entries(values)
            if entries:
                collector.add(BlockType.HEADING, heading, level=2)
                collector.add(BlockType.LIST, "\n".join(entries))

        relations = payload.get("relations")
        relation_list = relations if isinstance(relations, list) else []
        if relation_list:
            collector.add(BlockType.HEADING, "Relations", level=2)
            collector.add(
                BlockType.LIST,
                "\n".join(self._relation_text(value) for value in relation_list),
            )

        release_groups = payload.get("release-groups")
        release_group_list = release_groups if isinstance(release_groups, list) else []
        metadata: dict[str, JsonValue] = {
            "json_profile": "musicbrainz",
            "entity_id": entity_id,
            "entity_type": entity_type,
            "aliases": aliases,
            "relations": relation_list,
            "release_groups": release_group_list,
            "releases": payload.get("releases", []),
            "recordings": payload.get("recordings", []),
            "artists": payload.get("artists", []),
            "genres": payload.get("genres", []),
            "tags": payload.get("tags", []),
            "external_ids": {"musicbrainz": [entity_id]} if entity_id else {},
            "raw_json": payload,
            "truncated": collector.budget.truncated,
        }
        return ParserResult(
            title=title,
            author=None,
            published_at=None,
            language=self._guess_language(display_name),
            blocks=collector.blocks,
            metadata=metadata,
        )

    def _parse_generic(self, payload: JsonValue, source_id: str) -> ParserResult:
        collector = _BlockCollector(source_id, self.config)
        title = (
            self._first_string(payload, "title", "name")
            if isinstance(payload, dict)
            else None
        )
        collector.add(BlockType.HEADING, title or "JSON Document", level=1)
        structural_truncation = self._render_generic(
            payload, collector, depth=0, label="data"
        )
        ids: dict[str, JsonValue] = {}
        self._collect_ids(payload, ids, depth=0)
        metadata: dict[str, JsonValue] = {
            "json_profile": "generic",
            "ids": ids,
            "raw_json": payload,
            "truncated": structural_truncation or collector.budget.truncated,
        }
        return ParserResult(
            title=title,
            author=None,
            published_at=None,
            language=self._guess_language(title or ""),
            blocks=collector.blocks,
            metadata=metadata,
        )

    def _render_generic(
        self,
        value: JsonValue,
        collector: _BlockCollector,
        *,
        depth: int,
        label: str,
    ) -> bool:
        if depth > self.config.max_depth:
            collector.budget.truncated = True
            return True
        truncated = False
        if isinstance(value, dict):
            items = list(value.items())
            if len(items) > self.config.max_items:
                truncated = True
            for key, child in items[: self.config.max_items]:
                if isinstance(child, (dict, list)):
                    collector.add(
                        BlockType.HEADING,
                        str(key),
                        level=min(depth + 2, 6),
                    )
                    truncated = self._render_generic(
                        child, collector, depth=depth + 1, label=str(key)
                    ) or truncated
                else:
                    collector.add(
                        BlockType.PARAGRAPH,
                        f"{key}: {self._scalar_text(child)}",
                    )
        elif isinstance(value, list):
            if len(value) > self.config.max_items:
                truncated = True
            scalar_items: list[str] = []
            for index, child in enumerate(value[: self.config.max_items]):
                if isinstance(child, (dict, list)):
                    collector.add(
                        BlockType.HEADING,
                        f"{label} {index + 1}",
                        level=min(depth + 2, 6),
                    )
                    truncated = self._render_generic(
                        child,
                        collector,
                        depth=depth + 1,
                        label=f"{label} {index + 1}",
                    ) or truncated
                else:
                    scalar_items.append(self._scalar_text(child))
            if scalar_items:
                collector.add(BlockType.LIST, "\n".join(scalar_items))
        else:
            collector.add(BlockType.PARAGRAPH, self._scalar_text(value))
        if truncated:
            collector.budget.truncated = True
        return truncated

    def _collect_ids(
        self, value: JsonValue, target: dict[str, JsonValue], *, depth: int
    ) -> None:
        if depth > self.config.max_depth or len(target) >= self.config.max_items:
            return
        if isinstance(value, dict):
            for key, child in value.items():
                normalized_key = str(key).lower()
                if (
                    normalized_key == "id" or normalized_key.endswith("_id")
                ) and isinstance(child, (str, int)):
                    target[str(key)] = child
                    if len(target) >= self.config.max_items:
                        return
                else:
                    self._collect_ids(child, target, depth=depth + 1)
        elif isinstance(value, list):
            for child in value[: self.config.max_items]:
                self._collect_ids(child, target, depth=depth + 1)

    @staticmethod
    def _localized_value(value: JsonValue) -> tuple[str | None, str | None]:
        if not isinstance(value, dict):
            return None, None
        for language in ("zh-hans", "zh", "zh-hant", "en"):
            entry = value.get(language)
            if isinstance(entry, dict) and isinstance(entry.get("value"), str):
                return entry["value"], language
        for language, entry in value.items():
            if isinstance(entry, dict) and isinstance(entry.get("value"), str):
                return entry["value"], str(language)
        return None, None

    def _wikidata_aliases(self, value: JsonValue) -> list[str]:
        if not isinstance(value, dict):
            return []
        aliases: list[str] = []
        for entries in value.values():
            if not isinstance(entries, list):
                continue
            for entry in entries:
                if isinstance(entry, dict) and isinstance(entry.get("value"), str):
                    alias = entry["value"]
                    if alias not in aliases:
                        aliases.append(alias)
                    if len(aliases) >= self.config.max_items:
                        return aliases
        return aliases

    @staticmethod
    def _claim_value(statement: JsonValue) -> JsonValue:
        if not isinstance(statement, dict):
            return None
        mainsnak = statement.get("mainsnak")
        if not isinstance(mainsnak, dict):
            return None
        datavalue = mainsnak.get("datavalue")
        if not isinstance(datavalue, dict):
            return None
        return datavalue.get("value")

    def _named_values(self, value: JsonValue) -> list[str]:
        if not isinstance(value, list):
            return []
        names: list[str] = []
        for entry in value[: self.config.max_items]:
            if isinstance(entry, dict):
                name = self._first_string(entry, "name", "title")
                if name and name not in names:
                    names.append(name)
        return names

    def _musicbrainz_entries(self, value: JsonValue) -> list[str]:
        if not isinstance(value, list):
            return []
        entries: list[str] = []
        for item in value[: self.config.max_items]:
            if not isinstance(item, dict):
                continue
            title = self._first_string(item, "title", "name") or "Unnamed"
            entity_id = self._first_string(item, "id")
            entries.append(f"{title} ({entity_id})" if entity_id else title)
        return entries

    @staticmethod
    def _relation_text(value: JsonValue) -> str:
        if not isinstance(value, dict):
            return str(value)
        relation_type = value.get("type") or "relation"
        for key in ("url", "artist", "release", "release-group", "recording"):
            target = value.get(key)
            if isinstance(target, dict):
                display = target.get("resource") or target.get("name") or target.get("title") or target.get("id")
                if display:
                    return f"{relation_type}: {display}"
        return str(relation_type)

    @staticmethod
    def _first_string(value: dict[str, JsonValue], *keys: str) -> str | None:
        for key in keys:
            candidate = value.get(key)
            if isinstance(candidate, str) and candidate.strip():
                return candidate.strip()
        return None

    @staticmethod
    def _scalar_text(value: JsonValue) -> str:
        if value is None:
            return "null"
        if value is True:
            return "true"
        if value is False:
            return "false"
        return str(value)

    @staticmethod
    def _guess_language(value: str) -> str:
        return "zh" if re.search(r"[\u3400-\u9fff]", value) else "en" if value else "und"
