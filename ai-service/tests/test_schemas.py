import unittest

from pydantic import ValidationError

from app.schemas.chat import ChatRequest, STRUCTURED_OUTPUT_ADAPTER


class StructuredOutputSchemaTests(unittest.TestCase):
    def test_chat_request_keeps_old_payload_compatible(self) -> None:
        request = ChatRequest.model_validate({"message": "Hello"})

        self.assertIsNone(request.context)
        self.assertIsNone(request.thread_id)
        self.assertIsNone(request.resolved_current_song_id())

    def test_chat_request_accepts_runtime_context_references(self) -> None:
        request = ChatRequest.model_validate(
            {
                "message": "推荐几首和这首类似的歌",
                "context": {
                    "current_song_id": 185809,
                    "current_playlist_id": 1001,
                    "page": " /library ",
                },
            }
        )

        self.assertEqual(request.context.current_song_id, 185809)
        self.assertEqual(request.context.current_playlist_id, 1001)
        self.assertEqual(request.context.page, "/library")
        self.assertEqual(request.resolved_current_song_id(), 185809)

    def test_top_level_song_id_explicitly_overrides_legacy_context(self) -> None:
        request = ChatRequest.model_validate(
            {
                "message": "现在这首呢？",
                "thread_id": " thread-1 ",
                "current_song_id": None,
                "context": {"current_song_id": 185809},
            }
        )

        self.assertEqual(request.thread_id, "thread-1")
        self.assertIsNone(request.resolved_current_song_id())

    def test_accepts_each_output_variant(self) -> None:
        cases = [
            {"type": "chat", "message": "Hello", "search_query": None},
            {
                "type": "music_search",
                "message": "Searching for it.",
                "search_query": "Jay Chou Mojito",
            },
            {
                "type": "music_recommendation",
                "message": "Here are some calm songs.",
                "search_query": None,
            },
        ]

        for payload in cases:
            with self.subTest(output_type=payload["type"]):
                result = STRUCTURED_OUTPUT_ADAPTER.validate_python(payload)
                self.assertEqual(result.type, payload["type"])

    def test_rejects_cross_variant_search_query(self) -> None:
        invalid_cases = [
            {"type": "chat", "message": "Hello", "search_query": "unexpected"},
            {
                "type": "music_recommendation",
                "message": "Try these songs.",
                "search_query": "unexpected",
            },
            {"type": "music_search", "message": "Searching.", "search_query": None},
            {"type": "music_search", "message": "Searching.", "search_query": "   "},
        ]

        for payload in invalid_cases:
            with self.subTest(payload=payload):
                with self.assertRaises(ValidationError):
                    STRUCTURED_OUTPUT_ADAPTER.validate_python(payload)

    def test_rejects_extra_fields(self) -> None:
        with self.assertRaises(ValidationError):
            STRUCTURED_OUTPUT_ADAPTER.validate_python(
                {
                    "type": "chat",
                    "message": "Hello",
                    "search_query": None,
                    "extra": "not allowed",
                }
            )

    def test_generated_schema_requires_exact_fields(self) -> None:
        schema = STRUCTURED_OUTPUT_ADAPTER.json_schema()

        self.assertIn("oneOf", schema)
        for name in ("ChatOutput", "MusicSearchOutput", "MusicRecommendationOutput"):
            definition = schema["$defs"][name]
            self.assertFalse(definition["additionalProperties"])
            self.assertEqual(
                set(definition["required"]),
                {"type", "message", "search_query"},
            )


if __name__ == "__main__":
    unittest.main()
