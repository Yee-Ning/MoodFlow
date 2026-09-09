import json
import unittest
from datetime import UTC, datetime

from app.rag.parse_normalize.base import ParserError
from app.rag.parse_normalize.models import BlockType, ParseInput
from app.rag.parse_normalize.normalizer import MarkdownNormalizer
from app.rag.parse_normalize.parsers.json import JsonParser, JsonParserConfig
from app.rag.source_ingestion.models import RawSnapshotRecord


def _parse_input(payload: object, source_id: str = "json-source") -> ParseInput:
    raw = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    snapshot = RawSnapshotRecord(
        source_id=source_id,
        original_url="https://example.test/entity.json",
        content_type="application/json; charset=utf-8",
        content_hash="sha256:" + "0" * 64,
        fetched_at=datetime(2026, 9, 9, tzinfo=UTC),
        raw_path=f"data/raw/json/{source_id}.json",
        http_status=200,
        source_quality="medium",
        corpus_version="jay-chou-corpus-v1",
    )
    return ParseInput(snapshot=snapshot, raw_bytes=raw, candidate=None)


WIKIDATA_PAYLOAD = {
    "entities": {
        "Q238819": {
            "pageid": 227918,
            "ns": 0,
            "title": "Q238819",
            "lastrevid": 123,
            "modified": "2026-01-01T00:00:00Z",
            "type": "item",
            "id": "Q238819",
            "labels": {
                "zh": {"language": "zh", "value": "周杰伦"},
                "en": {"language": "en", "value": "Jay Chou"},
            },
            "descriptions": {
                "zh": {"language": "zh", "value": "台湾创作歌手"}
            },
            "aliases": {
                "zh": [
                    {"language": "zh", "value": "周董"},
                    {"language": "zh", "value": "杰伦"},
                ]
            },
            "claims": {
                "P434": [
                    {
                        "mainsnak": {
                            "datavalue": {
                                "value": "a223958d-5c56-4b2c-a30a-87e357bc121b",
                                "type": "string",
                            }
                        }
                    }
                ],
                "P175": [
                    {
                        "mainsnak": {
                            "datavalue": {
                                "value": {
                                    "entity-type": "item",
                                    "numeric-id": 238819,
                                    "id": "Q238819",
                                },
                                "type": "wikibase-entityid",
                            }
                        }
                    }
                ],
            },
            "sitelinks": {},
        }
    }
}


MUSICBRAINZ_PAYLOAD = {
    "id": "a223958d-5c56-4b2c-a30a-87e357bc121b",
    "name": "Jay Chou",
    "sort-name": "Chou, Jay",
    "type": "Person",
    "country": "TW",
    "aliases": [{"name": "周杰伦", "locale": "zh"}],
    "relations": [
        {
            "type": "official homepage",
            "url": {"resource": "https://www.jvrmusic.com.tw/"},
        }
    ],
    "release-groups": [
        {
            "id": "7f149117-94d1-33c8-8d9a-e15d4aa76520",
            "title": "Fantasy",
            "primary-type": "Album",
        }
    ],
    "genres": [{"name": "mandopop", "count": 1}],
    "tags": [{"name": "singer-songwriter", "count": 2}],
}


class JsonParserTests(unittest.TestCase):
    def test_wikidata_keeps_raw_structure_and_extracts_music_entity_fields(self) -> None:
        result = JsonParser().parse(_parse_input(WIKIDATA_PAYLOAD, "wikidata-source"))

        self.assertEqual(result.title, "周杰伦")
        self.assertEqual(result.language, "zh")
        self.assertEqual(result.metadata["json_profile"], "wikidata")
        self.assertEqual(result.metadata["entity_ids"], ["Q238819"])
        self.assertEqual(result.metadata["aliases"], ["周董", "杰伦"])
        self.assertEqual(
            result.metadata["external_ids"]["P434"],
            ["a223958d-5c56-4b2c-a30a-87e357bc121b"],
        )
        self.assertEqual(
            result.metadata["relations"],
            [{"property_id": "P175", "target_entity_id": "Q238819"}],
        )
        self.assertEqual(result.metadata["raw_json"], WIKIDATA_PAYLOAD)
        self.assertEqual(result.blocks[0].block_type, BlockType.HEADING)
        self.assertIn("台湾创作歌手", MarkdownNormalizer().render(result.blocks))

    def test_musicbrainz_keeps_ids_aliases_relations_and_release_groups(self) -> None:
        result = JsonParser().parse(
            _parse_input(MUSICBRAINZ_PAYLOAD, "musicbrainz-source")
        )

        self.assertEqual(result.title, "Jay Chou")
        self.assertEqual(result.metadata["json_profile"], "musicbrainz")
        self.assertEqual(
            result.metadata["entity_id"],
            "a223958d-5c56-4b2c-a30a-87e357bc121b",
        )
        self.assertEqual(result.metadata["aliases"], ["周杰伦"])
        self.assertEqual(
            result.metadata["relations"][0]["url"]["resource"],
            "https://www.jvrmusic.com.tw/",
        )
        self.assertEqual(
            result.metadata["release_groups"][0]["title"], "Fantasy"
        )
        markdown = MarkdownNormalizer().render(result.blocks)
        self.assertIn("Fantasy", markdown)
        self.assertIn("7f149117-94d1-33c8-8d9a-e15d4aa76520", markdown)

    def test_generic_json_bounds_depth_field_items_and_total_output(self) -> None:
        payload = {
            "title": "A generic document",
            "long": "x" * 50,
            "nested": {"a": {"b": {"c": 1}}},
            "many": ["one", "two", "three", "four"],
            "recording_id": "recording-123",
        }
        parser = JsonParser(
            JsonParserConfig(
                max_depth=2,
                max_field_chars=8,
                max_output_chars=120,
                max_items=2,
            )
        )

        result = parser.parse(_parse_input(payload))

        self.assertEqual(result.metadata["json_profile"], "generic")
        self.assertTrue(result.metadata["truncated"])
        self.assertEqual(result.metadata["raw_json"], payload)
        self.assertEqual(result.metadata["ids"]["recording_id"], "recording-123")
        self.assertLessEqual(sum(len(block.text) for block in result.blocks), 120)
        self.assertNotIn("x" * 9, " ".join(block.text for block in result.blocks))

    def test_invalid_json_is_an_explicit_parser_error(self) -> None:
        parse_input = _parse_input({"valid": True})
        parse_input = parse_input.model_copy(update={"raw_bytes": b'{"broken":'})

        with self.assertRaisesRegex(ParserError, "json_parse_failed"):
            JsonParser().parse(parse_input)


if __name__ == "__main__":
    unittest.main()
