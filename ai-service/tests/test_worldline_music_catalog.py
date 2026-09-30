import unittest
from unittest.mock import AsyncMock

from app.agent.foundation.subgraphs.worldline.entity_resolver import resolve_music_entity
from app.agent.foundation.subgraphs.worldline.models import EntityResolutionStatus, EntityType
from app.agent.foundation.subgraphs.worldline.music_service import JavaMusicEntityService
from app.schemas.music import MusicEntity


class WorldlineCatalogTests(unittest.IsolatedAsyncioTestCase):
    async def test_untyped_catalog_search_keeps_worldline_entities_among_playlists(self):
        client = AsyncMock()
        client.search_entities.return_value = [
            MusicEntity(entity_id=1, entity_type="playlist", name="Radiohead 精选"),
            MusicEntity(entity_id=2, entity_type="artist", name="Radiohead"),
            MusicEntity(entity_id=3, entity_type="song", name="Creep"),
            MusicEntity(entity_id=4, entity_type="album", name="OK Computer"),
        ]
        service = JavaMusicEntityService(client)
        entities = await service.search_entities("Radiohead", None)
        self.assertEqual([entity.entity_type for entity in entities], [
            EntityType.ARTIST, EntityType.SONG, EntityType.ALBUM,
        ])
        client.search_entities.assert_awaited_once_with("Radiohead", entity_type=None, limit=5)
        resolved = await resolve_music_entity(service, "Radiohead")
        self.assertEqual(resolved.status, EntityResolutionStatus.RESOLVED)
        self.assertEqual(resolved.entity.entity_id, "netease:artist:2")

    async def test_playlist_only_match_does_not_become_a_worldline_root(self):
        client = AsyncMock()
        client.search_entities.return_value = [
            MusicEntity(entity_id=1, entity_type="playlist", name="Radiohead"),
        ]
        resolved = await resolve_music_entity(JavaMusicEntityService(client), "Radiohead")
        self.assertEqual(resolved.status, EntityResolutionStatus.UNRESOLVED)
        self.assertIsNone(resolved.entity)
