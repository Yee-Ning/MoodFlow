import unittest

from pydantic import ValidationError

from app.schemas.music import (
    AddSongToPlaylistArgs,
    SearchMusicArgs,
)
from app.tools.definitions import SEARCH_MUSIC_TOOL
from app.tools.music import MUSIC_AGENT_TOOLS


class SearchMusicToolDefinitionTests(unittest.TestCase):
    def test_parameters_are_generated_from_pydantic_model(self) -> None:
        schema = SearchMusicArgs.model_json_schema()

        self.assertEqual(SEARCH_MUSIC_TOOL["parameters"], schema)
        self.assertFalse(schema["additionalProperties"])
        self.assertEqual(schema["required"], ["keyword"])
        self.assertEqual(schema["properties"]["limit"]["default"], 10)
        self.assertEqual(schema["properties"]["limit"]["minimum"], 1)
        self.assertEqual(schema["properties"]["limit"]["maximum"], 50)

    def test_arguments_reject_unknown_fields(self) -> None:
        with self.assertRaises(ValidationError):
            SearchMusicArgs(keyword="周杰伦", unknown="not allowed")

    def test_langchain_tools_use_explicit_schemas(self) -> None:
        expected_properties = {
            "search_music": {"keyword", "limit"},
            "get_song_lyrics": {"song_id"},
            "get_user_playlists": set(),
            "add_song_to_playlist": {"playlist_id", "song_id"},
        }

        self.assertEqual(
            [item.name for item in MUSIC_AGENT_TOOLS],
            list(expected_properties),
        )
        for item in MUSIC_AGENT_TOOLS:
            with self.subTest(tool=item.name):
                properties = item.tool_call_schema.model_json_schema()["properties"]
                self.assertEqual(set(properties), expected_properties[item.name])
                self.assertNotIn("user_id", properties)
                self.assertNotIn("session_cookie", properties)
                self.assertNotIn("runtime", properties)

    def test_write_tool_requires_only_business_ids(self) -> None:
        schema = AddSongToPlaylistArgs.model_json_schema()

        self.assertEqual(set(schema["required"]), {"playlist_id", "song_id"})
        self.assertFalse(schema["additionalProperties"])


if __name__ == "__main__":
    unittest.main()
