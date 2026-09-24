import unittest

from app.agent.foundation.subgraphs.worldline.genre_registry import (
    GenreVocabularyRegistry,
)


class WorldLineGenreRegistryTests(unittest.TestCase):
    def setUp(self) -> None:
        self.registry = GenreVocabularyRegistry.load()

    def test_loads_versioned_controlled_vocabulary(self) -> None:
        self.assertEqual(self.registry.version, "v1")
        self.assertEqual(
            {item.canonical_id for item in self.registry.entries()},
            {
                "genre:hip_hop",
                "genre:jazz",
                "genre:jazz_hip_hop",
                "genre:r_and_b",
                "genre:c_pop",
            },
        )

    def test_resolves_registered_genre_by_id_name_and_alias(self) -> None:
        by_id = self.registry.get("genre:jazz_hip_hop")
        by_name = self.registry.resolve("Jazz Hip-Hop")
        by_alias = self.registry.resolve("爵士嘻哈")

        self.assertIsNotNone(by_id)
        self.assertEqual(by_name, by_id)
        self.assertEqual(by_alias, by_id)

    def test_rejects_unregistered_genre(self) -> None:
        self.assertIsNone(self.registry.get("genre:unregistered"))
        self.assertIsNone(self.registry.resolve("Unregistered Genre"))

    def test_v23a_genres_are_not_executable_or_expandable(self) -> None:
        self.assertTrue(
            all(
                not item.executable and not item.expandable
                for item in self.registry.entries()
            )
        )


if __name__ == "__main__":
    unittest.main()
