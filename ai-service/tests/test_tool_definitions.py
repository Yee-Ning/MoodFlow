import unittest

from pydantic import ValidationError

from app.schemas.music import (
    AddSongToPlaylistArgs,
    CreatePlaylistArgs,
    RemoveSongFromPlaylistArgs,
    SetSongLikedArgs,
    GetAlbumDetailArgs,
    GetPlaylistDetailArgs,
    SearchEntitiesArgs,
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
            "search_entities_tool": {"query", "entity_type", "limit"},
            "get_song_lyrics": {"song_id"},
            "get_user_playlists": set(),
            "get_playlist_detail_tool": {"playlist_id"},
            "get_album_detail_tool": {"album_id"},
            "add_song_to_playlist": {"playlist_id", "song_id"},
            "set_song_liked_tool": {"song_id", "liked"},
            "create_playlist_tool": {"name", "privacy"},
            "remove_song_from_playlist_tool": {"playlist_id", "song_id"},
            "get_liked_songs": set(),
            "delete_playlist_tool": {"playlist_id", "confirmed"},
            "get_song_popularity_tool": {"song_ids"},
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

    def test_asset_arguments_reject_invalid_or_private_fields(self) -> None:
        for arguments in ({"name": "  "}, {"name": "x" * 41}, {"name": "学习", "privacy": 1},
                          {"name": "学习", "session_cookie": "fake"}):
            with self.subTest(arguments=arguments), self.assertRaises(ValidationError):
                CreatePlaylistArgs.model_validate(arguments)
        with self.assertRaises(ValidationError):
            RemoveSongFromPlaylistArgs(playlist_id=1, song_id=0)
        with self.assertRaises(ValidationError):
            SetSongLikedArgs(song_id=1, liked="false")

    def test_entity_search_and_playlist_detail_reject_invalid_arguments(self) -> None:
        with self.assertRaises(ValidationError):
            SearchEntitiesArgs(query="   ")
        with self.assertRaises(ValidationError):
            SearchEntitiesArgs(query="学习", entity_type="genre")
        with self.assertRaises(ValidationError):
            GetPlaylistDetailArgs(playlist_id=0)
        with self.assertRaises(ValidationError):
            GetAlbumDetailArgs(album_id=0)


if __name__ == "__main__":
    unittest.main()
