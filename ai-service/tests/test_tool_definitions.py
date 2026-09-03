import unittest

from pydantic import ValidationError

from app.schemas.music import SearchMusicArgs
from app.tools.definitions import SEARCH_MUSIC_TOOL


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


if __name__ == "__main__":
    unittest.main()
